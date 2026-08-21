import sys
import threading
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst

from .callbacks import make_osd_probe
from .metrics import Metrics
from framework.events.event_manager import EventManager

STREAM_WIDTH = 640
STREAM_HEIGHT = 480
FPS = 30


def _make_elm(factory_name, name):
    elm = Gst.ElementFactory.make(factory_name, name)
    if not elm:
        raise RuntimeError(f"Unable to create element {factory_name} ({name})")
    return elm


class StreamManager:
    """
    Owns one fully independent GStreamer pipeline for a single camera,
    plus this camera's own EventManager/ViolationGallery (isolated
    cooldowns/track-ids), and pushes alerts out to shared MQTT/speaker.

    solution: a BaseSolution instance (PPEIndustrialSolution,
    DriverMonitoringSolution, or HealthcareMonitoringSolution) --
    supplies pgie_config_path (which nvinfer config to load) and is
    passed through to the OSD probe for evaluate()/manifest access.
    Previously this class was PPE-hardcoded (a fixed PGIE_CONFIG
    constant); now it's fully solution-agnostic.
    """

    def __init__(self, slot_id, solution, mqtt=None, speaker=None,
                 base_dir="/home/ksanju/ppe_system/deepstream_ppe_poc/backend",data_manager=None):
        self.slot_id = slot_id
        self.base_dir = base_dir
        self.pipeline = None
        self.device = None
        self.running = False
        self.metrics = Metrics(camera_count=1)

        self.mqtt = mqtt
        self.speaker = speaker
        self.data_manager = data_manager

        self._lock = threading.Lock()
        self._latest_jpeg = None
        self._latest_stats = {"persons": 0, "violations": 0, "fps": 0}
        self._pending_alerts = []
        self._broadcast_queue = []

        self.bind_solution(solution)

    def bind_solution(self, solution):
        """
        Assign (or reassign) this slot's Solution instance, rebuilding
        its EventManager/ViolationGallery so no debounce state or alert
        history leaks from a previously bound solution. Safe to call
        whether the slot is running or idle -- it does NOT touch the
        pipeline. Callers that need a *live* swap must stop() first,
        call this, then start() again (see swap_solution()).
        """
        self.solution = solution

        self.event_mgr = EventManager(
            screenshot_dir=f"{self.base_dir}/screenshots/{solution.name}/slot{self.slot_id}",
            cooldown_seconds=30,
        )

        from framework.alerts.violation_gallery import ViolationGallery
        self.gallery = ViolationGallery(
            save_dir=f"{self.base_dir}/temp/gallery/{solution.name}/slot{self.slot_id}",
            max_per_person=5,
        )

    def swap_solution(self, solution):
        """
        Rebind this slot to a different Solution. If the slot is
        currently running, this stops the pipeline, rebinds, and
        restarts on the same device -- reusing the exact stop()/start()
        path already proven by every live stream start/stop cycle this
        session, rather than reconfiguring nvinfer on a PLAYING
        pipeline. If the slot is idle, this just rebinds (no pipeline
        activity at all).
        """
        was_running = self.running
        device = self.device
        if was_running:
            self.stop()

        self.bind_solution(solution)

        if was_running:
            self.start(device)

    def is_running(self):
        return self.running

    def get_latest_jpeg(self):
        with self._lock:
            return self._latest_jpeg

    def get_latest_stats(self):
        with self._lock:
            return dict(self._latest_stats)

    def pop_new_alerts(self):
        with self._lock:
            alerts, self._broadcast_queue = self._broadcast_queue, []
            return alerts

    def _on_frame_result(self, smoothed):
        alerts = self.event_mgr.process(smoothed)
        stats = self.solution.get_stats(smoothed)

        with self._lock:
            self._latest_stats = {
                "persons": stats["persons"],
                "violations": stats["violations"],
                "fps": self.metrics.fps(0),
            }
            if alerts:
                self._pending_alerts.extend(alerts)

    def _on_new_sample(self, sink):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        buf = sample.get_buffer()
        ok, mapinfo = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.OK

        jpeg_bytes = bytes(mapinfo.data)
        buf.unmap(mapinfo)

        with self._lock:
            self._latest_jpeg = jpeg_bytes
            ready_alerts = self._pending_alerts
            self._pending_alerts = []

        for a in ready_alerts:
            self.event_mgr.save_screenshot(a, jpeg_bytes)
            self.gallery.capture(a, jpeg_bytes)

            if self.data_manager is not None:
                try:
                    self.data_manager.log_event(
                        camera_slot=str(self.slot_id),
                        solution=self.solution.name,
                        event_type=a["violation_type"],
                        person_id=a["person_id"],
                        screenshot_path=a.get("screenshot"),
                        timestamp=a["timestamp"],
                    )
                except Exception as e:
                    sys.stderr.write(f"[slot {self.slot_id}] DataManager.log_event failed: {e}\n")

            if self.mqtt is not None:
                self.mqtt.publish({**a, "camera": self.slot_id})
            if self.speaker is not None:
                self.speaker.alert(self.slot_id, a["person_id"], a["violation_type"])

            with self._lock:
                self._broadcast_queue.append(a)

        return Gst.FlowReturn.OK

    def _bus_call(self, bus, message):
        t = message.type
        if t == Gst.MessageType.EOS:
            sys.stdout.write(f"[slot {self.slot_id}] End-of-stream\n")
            self.stop()
        elif t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            sys.stderr.write(f"[slot {self.slot_id}] ERROR: {err}: {debug}\n")
            self.stop()
        return True

    def start(self, device_path):
        if self.running:
            self.stop()

        Gst.init(None)
        pipeline = Gst.Pipeline()

        source = _make_elm("v4l2src", f"src-{self.slot_id}")
        source.set_property("device", device_path)

        caps_v4l2 = _make_elm("capsfilter", f"v4l2caps-{self.slot_id}")
        caps_v4l2.set_property(
            "caps",
            Gst.Caps.from_string(
                f"video/x-raw, width={STREAM_WIDTH}, height={STREAM_HEIGHT}, framerate={FPS}/1"
            ),
        )

        vidconv = _make_elm("videoconvert", f"vidconv-{self.slot_id}")
        nvvidconv_in = _make_elm("nvvideoconvert", f"nvvidconv-in-{self.slot_id}")

        caps_nvmm = _make_elm("capsfilter", f"nvmmcaps-{self.slot_id}")
        caps_nvmm.set_property("caps", Gst.Caps.from_string("video/x-raw(memory:NVMM), format=NV12"))

        streammux = _make_elm("nvstreammux", f"mux-{self.slot_id}")
        streammux.set_property("width", STREAM_WIDTH)
        streammux.set_property("height", STREAM_HEIGHT)
        streammux.set_property("batch-size", 1)
        streammux.set_property("batched-push-timeout", 40000)
        streammux.set_property("live-source", 1)
        streammux.set_property("nvbuf-memory-type", 4)

        pgie = _make_elm("nvinfer", f"pgie-{self.slot_id}")
        pgie.set_property("config-file-path", self.solution.pgie_config_path)
        pgie.set_property("batch-size", 1)

        nvvidconv_osd = _make_elm("nvvideoconvert", f"nvvidconv-osd-{self.slot_id}")
        caps_osd = _make_elm("capsfilter", f"osdcaps-{self.slot_id}")
        caps_osd.set_property("caps", Gst.Caps.from_string("video/x-raw(memory:NVMM), format=RGBA"))

        nvdsosd = _make_elm("nvdsosd", f"osd-{self.slot_id}")

        probe_fn = make_osd_probe(
            self.slot_id, self.metrics, self.solution,
            frame_width=STREAM_WIDTH, on_result=self._on_frame_result,
        )
        osd_sink_pad = nvdsosd.get_static_pad("sink")
        osd_sink_pad.add_probe(Gst.PadProbeType.BUFFER, probe_fn, 0)

        nvvidconv_out = _make_elm("nvvideoconvert", f"nvvidconv-out-{self.slot_id}")
        caps_i420 = _make_elm("capsfilter", f"i420caps-{self.slot_id}")
        caps_i420.set_property("caps", Gst.Caps.from_string("video/x-raw, format=I420"))

        jpegenc = _make_elm("jpegenc", f"jpegenc-{self.slot_id}")

        appsink = _make_elm("appsink", f"appsink-{self.slot_id}")
        appsink.set_property("emit-signals", True)
        appsink.set_property("max-buffers", 1)
        appsink.set_property("drop", True)
        appsink.set_property("sync", False)
        appsink.connect("new-sample", self._on_new_sample)

        for e in (source, caps_v4l2, vidconv, nvvidconv_in, caps_nvmm,
                  streammux, pgie, nvvidconv_osd, caps_osd, nvdsosd,
                  nvvidconv_out, caps_i420, jpegenc, appsink):
            pipeline.add(e)

        source.link(caps_v4l2)
        caps_v4l2.link(vidconv)
        vidconv.link(nvvidconv_in)
        nvvidconv_in.link(caps_nvmm)

        sinkpad = streammux.get_request_pad("sink_0")
        srcpad = caps_nvmm.get_static_pad("src")
        srcpad.link(sinkpad)

        streammux.link(pgie)
        pgie.link(nvvidconv_osd)
        nvvidconv_osd.link(caps_osd)
        caps_osd.link(nvdsosd)
        nvdsosd.link(nvvidconv_out)
        nvvidconv_out.link(caps_i420)
        caps_i420.link(jpegenc)
        jpegenc.link(appsink)

        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._bus_call)

        pipeline.set_state(Gst.State.PLAYING)

        self.pipeline = pipeline
        self.device = device_path
        self.running = True

    def stop(self):
        if self.pipeline is not None:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None
        self.running = False
        with self._lock:
            self._latest_jpeg = None
            self._latest_stats = {"persons": 0, "violations": 0, "fps": 0}
            self._pending_alerts = []
            self._broadcast_queue = []
