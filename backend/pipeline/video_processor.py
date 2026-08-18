import os
import sys
import time
import threading

import cv2
import gi

gi.require_version('Gst', '1.0')
from gi.repository import Gst

from .callbacks import make_osd_probe
from .metrics import Metrics
from framework.events.event_manager import EventManager


STREAM_WIDTH = 640
STREAM_HEIGHT = 480


def _make_elm(factory_name, name):
    elm = Gst.ElementFactory.make(factory_name, name)

    if not elm:
        raise RuntimeError(
            f"Unable to create element {factory_name} ({name})"
        )

    return elm


class VideoProcessor:
    """
    Owns one GStreamer pipeline that decodes an uploaded video file,
    runs the same YOLOv8 + tracker + smoother pipeline as live cameras,
    and streams annotated JPEG frames + progress back out — mirroring
    StreamManager's push model but running to completion instead of
    forever, sourced from filesrc+decodebin instead of v4l2src.

    The same annotated JPEG frames sent to the WebSocket stream are also
    decoded and written to an MP4 file for offline playback.
    """

    def __init__(
        self,
        job_id,
        video_path,
        solution,
        mqtt=None,
        speaker=None,
        base_dir="/home/ksanju/ppe_system/deepstream_ppe_poc/backend",
    ):
        self.job_id = job_id
        self.video_path = video_path
        self.solution = solution

        self.pipeline = None
        self.running = False
        self.done = False
        self.error = None

        self.metrics = Metrics(camera_count=1)

        self.mqtt = mqtt
        self.speaker = speaker

        self.event_mgr = EventManager(
            screenshot_dir=f"{base_dir}/screenshots/{solution.name}/upload_{job_id}",
            cooldown_seconds=15,
        )

        from alerts.violation_gallery import ViolationGallery

        self.gallery = ViolationGallery(
            save_dir=f"{base_dir}/temp/gallery/{solution.name}/upload_{job_id}",
            max_per_person=5,
        )

        self.total_frames = 0
        self.frame_count = 0

        # Annotated output video.
        # Every annotated JPEG frame produced for the live WebSocket stream
        # is also written to this MP4 file.
        self.output_dir = f"{base_dir}/outputs"
        os.makedirs(self.output_dir, exist_ok=True)

        self.output_path = (
            f"{self.output_dir}/{job_id}_annotated.mp4"
        )

        self._video_writer = None

        # Default fallback FPS.
        # This is overwritten by _probe_total_frames() when the source
        # video exposes a valid FPS value.
        self._source_fps = 25.0

        self._lock = threading.Lock()

        self._latest_jpeg = None

        self._latest_stats = {
            "persons": 0,
            "violations": 0,
            "fps": 0,
        }

        self._pending_alerts = []
        self._broadcast_queue = []

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
            alerts, self._broadcast_queue = (
                self._broadcast_queue,
                [],
            )

            return alerts

    def get_progress(self):
        if self.total_frames <= 0:
            return 100 if self.done else 0

        pct = int(
            (self.frame_count / self.total_frames) * 100
        )

        return min(100, max(0, pct))

    def _probe_total_frames(self):
        """
        Probe the uploaded video before starting the DeepStream pipeline.

        Retrieves:
        - Total source frame count for progress reporting.
        - Source FPS for the annotated output video.
        """

        cap = cv2.VideoCapture(self.video_path)

        try:
            count = int(
                cap.get(cv2.CAP_PROP_FRAME_COUNT)
            )

            fps = cap.get(
                cv2.CAP_PROP_FPS
            )

            if fps and fps > 1:
                self._source_fps = fps

        finally:
            cap.release()

        self.total_frames = (
            count if count > 0 else 0
        )

    # Called from the GStreamer probe thread
    # (per frame, before jpegenc).
    def _on_frame_result(self, smoothed):
        alerts = self.event_mgr.process(smoothed)

        stats = self.solution.get_stats(smoothed)
        persons = stats["persons"]
        violations = stats["violations"]

        self.frame_count += 1

        with self._lock:
            self._latest_stats = {
                "persons": persons,
                "violations": violations,
                "fps": self.metrics.fps(0),
            }

            if alerts:
                self._pending_alerts.extend(alerts)

    def _write_frame_to_video(self, jpeg_bytes):
        """
        Decode the exact annotated JPEG frame sent to the WebSocket
        stream and write it to the annotated MP4 output file.

        The VideoWriter is created lazily from the first valid frame so
        that the output dimensions exactly match the streamed frame.
        """

        try:
            import numpy as np

            arr = np.frombuffer(
                jpeg_bytes,
                dtype=np.uint8,
            )

            frame = cv2.imdecode(
                arr,
                cv2.IMREAD_COLOR,
            )

            if frame is None:
                return

            # Lazily initialize the writer using the dimensions of the
            # first annotated frame received from the DeepStream pipeline.
            if self._video_writer is None:
                h, w = frame.shape[:2]

                fourcc = cv2.VideoWriter_fourcc(
                    *"mp4v"
                )

                self._video_writer = cv2.VideoWriter(
                    self.output_path,
                    fourcc,
                    self._source_fps,
                    (w, h),
                )

            self._video_writer.write(frame)

        except Exception as e:
            sys.stderr.write(
                f"[upload {self.job_id}] "
                f"video write failed: {e}\n"
            )

    def _on_new_sample(self, sink):
        """
        Receives the annotated JPEG generated by the GStreamer pipeline.

        The same JPEG bytes are:
        1. Written into the annotated output MP4.
        2. Stored as the latest frame for WebSocket broadcasting.
        3. Used for violation screenshots and gallery captures.
        """

        sample = sink.emit("pull-sample")

        if sample is None:
            return Gst.FlowReturn.OK

        buf = sample.get_buffer()

        ok, mapinfo = buf.map(
            Gst.MapFlags.READ
        )

        if not ok:
            return Gst.FlowReturn.OK

        jpeg_bytes = bytes(
            mapinfo.data
        )

        buf.unmap(
            mapinfo
        )

        # Write the exact annotated frame being streamed over WebSocket
        # to the output MP4 file.
        self._write_frame_to_video(
            jpeg_bytes
        )

        with self._lock:
            self._latest_jpeg = jpeg_bytes

            ready_alerts = (
                self._pending_alerts
            )

            self._pending_alerts = []

        for a in ready_alerts:
            self.event_mgr.save_screenshot(
                a,
                jpeg_bytes,
            )

            self.gallery.capture(
                a,
                jpeg_bytes,
            )

            if self.mqtt is not None:
                self.mqtt.publish({
                    **a,
                    "camera": (
                        f"upload:{self.job_id}"
                    ),
                })

            if self.speaker is not None:
                self.speaker.alert(
                    self.job_id,
                    a["person_id"],
                    a["violation_type"],
                )

            with self._lock:
                self._broadcast_queue.append(
                    a
                )

        return Gst.FlowReturn.OK

    def _on_pad_added(
        self,
        decodebin,
        pad,
        nvvidconv_in,
    ):
        caps = (
            pad.get_current_caps()
            or pad.query_caps(None)
        )

        if (
            not caps
            or caps.get_size() == 0
        ):
            return

        structure = caps.get_structure(0)
        name = structure.get_name()

        if not name.startswith("video/"):
            # Ignore audio and subtitle pads.
            return

        sinkpad = (
            nvvidconv_in
            .get_static_pad("sink")
        )

        if (
            sinkpad
            and not sinkpad.is_linked()
        ):
            pad.link(sinkpad)

    def _bus_call(
        self,
        bus,
        message,
    ):
        t = message.type

        if t == Gst.MessageType.EOS:
            sys.stdout.write(
                f"[upload {self.job_id}] "
                f"End-of-stream\n"
            )

            self._finish()

        elif t == Gst.MessageType.ERROR:
            err, debug = (
                message.parse_error()
            )

            sys.stderr.write(
                f"[upload {self.job_id}] "
                f"ERROR: {err}: {debug}\n"
            )

            self.error = str(err)

            self._finish()

        return True

    def start(self):
        if self.running:
            return

        # Probe frame count and source FPS before processing starts.
        self._probe_total_frames()

        Gst.init(None)

        pipeline = Gst.Pipeline()

        source = _make_elm(
            "filesrc",
            f"upload-src-{self.job_id}",
        )

        source.set_property(
            "location",
            self.video_path,
        )

        decodebin = _make_elm(
            "decodebin",
            f"upload-decode-{self.job_id}",
        )

        nvvidconv_in = _make_elm(
            "nvvideoconvert",
            f"upload-nvconv-in-{self.job_id}",
        )

        caps_nvmm = _make_elm(
            "capsfilter",
            f"upload-nvmmcaps-{self.job_id}",
        )

        caps_nvmm.set_property(
            "caps",
            Gst.Caps.from_string(
                "video/x-raw(memory:NVMM), format=NV12"
            ),
        )

        streammux = _make_elm(
            "nvstreammux",
            f"upload-mux-{self.job_id}",
        )

        streammux.set_property(
            "width",
            STREAM_WIDTH,
        )

        streammux.set_property(
            "height",
            STREAM_HEIGHT,
        )

        streammux.set_property(
            "batch-size",
            1,
        )

        streammux.set_property(
            "batched-push-timeout",
            40000,
        )

        streammux.set_property(
            "live-source",
            0,
        )

        streammux.set_property(
            "nvbuf-memory-type",
            4,
        )

        pgie = _make_elm(
            "nvinfer",
            f"upload-pgie-{self.job_id}",
        )

        pgie.set_property(
            "config-file-path",
            self.solution.pgie_config_path,
        )

        pgie.set_property(
            "batch-size",
            1,
        )

        nvvidconv_osd = _make_elm(
            "nvvideoconvert",
            f"upload-nvconv-osd-{self.job_id}",
        )

        caps_osd = _make_elm(
            "capsfilter",
            f"upload-osdcaps-{self.job_id}",
        )

        caps_osd.set_property(
            "caps",
            Gst.Caps.from_string(
                "video/x-raw(memory:NVMM), format=RGBA"
            ),
        )

        nvdsosd = _make_elm(
            "nvdsosd",
            f"upload-osd-{self.job_id}",
        )

        probe_fn = make_osd_probe(
            self.job_id[:8],
            self.metrics,
            self.solution,
            frame_width=STREAM_WIDTH,
            on_result=self._on_frame_result,
        )

        osd_sink_pad = (
            nvdsosd.get_static_pad("sink")
        )

        osd_sink_pad.add_probe(
            Gst.PadProbeType.BUFFER,
            probe_fn,
            0,
        )

        nvvidconv_out = _make_elm(
            "nvvideoconvert",
            f"upload-nvconv-out-{self.job_id}",
        )

        caps_i420 = _make_elm(
            "capsfilter",
            f"upload-i420caps-{self.job_id}",
        )

        caps_i420.set_property(
            "caps",
            Gst.Caps.from_string(
                "video/x-raw, format=I420"
            ),
        )

        jpegenc = _make_elm(
            "jpegenc",
            f"upload-jpegenc-{self.job_id}",
        )

        appsink = _make_elm(
            "appsink",
            f"upload-appsink-{self.job_id}",
        )

        appsink.set_property(
            "emit-signals",
            True,
        )

        appsink.set_property(
            "max-buffers",
            1,
        )

        appsink.set_property(
            "drop",
            True,
        )

        appsink.set_property(
            "sync",
            False,
        )

        appsink.connect(
            "new-sample",
            self._on_new_sample,
        )

        for e in (
            source,
            decodebin,
            nvvidconv_in,
            caps_nvmm,
            streammux,
            pgie,
            nvvidconv_osd,
            caps_osd,
            nvdsosd,
            nvvidconv_out,
            caps_i420,
            jpegenc,
            appsink,
        ):
            pipeline.add(e)

        source.link(
            decodebin
        )

        decodebin.connect(
            "pad-added",
            self._on_pad_added,
            nvvidconv_in,
        )

        nvvidconv_in.link(
            caps_nvmm
        )

        sinkpad = (
            streammux.get_request_pad(
                "sink_0"
            )
        )

        srcpad = (
            caps_nvmm.get_static_pad(
                "src"
            )
        )

        srcpad.link(
            sinkpad
        )

        streammux.link(
            pgie
        )

        pgie.link(
            nvvidconv_osd
        )

        nvvidconv_osd.link(
            caps_osd
        )

        caps_osd.link(
            nvdsosd
        )

        nvdsosd.link(
            nvvidconv_out
        )

        nvvidconv_out.link(
            caps_i420
        )

        caps_i420.link(
            jpegenc
        )

        jpegenc.link(
            appsink
        )

        bus = pipeline.get_bus()

        bus.add_signal_watch()

        bus.connect(
            "message",
            self._bus_call,
        )

        pipeline.set_state(
            Gst.State.PLAYING
        )

        self.pipeline = pipeline
        self.running = True
        self.done = False

    def _finish(self):
        """
        Stop the DeepStream pipeline and finalize the annotated MP4.

        Releasing VideoWriter is required to flush the remaining frames
        and properly write the MP4 container metadata.
        """

        if self.pipeline is not None:
            self.pipeline.set_state(
                Gst.State.NULL
            )

        if self._video_writer is not None:
            self._video_writer.release()
            self._video_writer = None

        self.running = False
        self.done = True

    def stop(self):
        # _finish() also releases and finalizes the output VideoWriter.
        self._finish()

        with self._lock:
            self._latest_jpeg = None
            self._pending_alerts = []
            self._broadcast_queue = []
