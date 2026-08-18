import sys
import gi
gi.require_version('Gst', '1.0')
from gi.repository import GLib, Gst
import pyds

from .callbacks import osd_sink_pad_buffer_probe

CONFIG_DIR = "/home/ksanju/ppe_system/deepstream_ppe_poc/backend/config"
PGIE_CONFIG = f"{CONFIG_DIR}/config_infer_primary_yolov8.txt"

CAMERA_DEVICES = ["/dev/video0", "/dev/video2"]
STREAM_WIDTH = 640
STREAM_HEIGHT = 480
FPS = 30


def make_elm(factory_name, name):
    elm = Gst.ElementFactory.make(factory_name, name)
    if not elm:
        sys.stderr.write(f"ERROR: Unable to create element {factory_name} ({name})\n")
        sys.exit(1)
    return elm


def build_usb_source_bin(pipeline, index, device_path):
    source = make_elm("v4l2src", f"usb-cam-source-{index}")
    source.set_property("device", device_path)

    caps_v4l2src = make_elm("capsfilter", f"v4l2src-caps-{index}")
    caps_v4l2src.set_property(
        "caps",
        Gst.Caps.from_string(
            f"video/x-raw, width={STREAM_WIDTH}, height={STREAM_HEIGHT}, framerate={FPS}/1"
        ),
    )

    vidconvsrc = make_elm("videoconvert", f"convertor-src-{index}")
    nvvidconvsrc = make_elm("nvvideoconvert", f"nv-convertor-src-{index}")

    caps_nvmm = make_elm("capsfilter", f"nvmm-caps-{index}")
    caps_nvmm.set_property(
        "caps",
        Gst.Caps.from_string("video/x-raw(memory:NVMM), format=NV12"),
    )

    for e in (source, caps_v4l2src, vidconvsrc, nvvidconvsrc, caps_nvmm):
        pipeline.add(e)

    source.link(caps_v4l2src)
    caps_v4l2src.link(vidconvsrc)
    vidconvsrc.link(nvvidconvsrc)
    nvvidconvsrc.link(caps_nvmm)

    return caps_nvmm


def build_pipeline():
    Gst.init(None)

    pipeline = Gst.Pipeline()
    if not pipeline:
        sys.stderr.write("ERROR: Unable to create pipeline\n")
        sys.exit(1)

    num_sources = len(CAMERA_DEVICES)

    streammux = make_elm("nvstreammux", "stream-muxer")
    pipeline.add(streammux)
    streammux.set_property("width", STREAM_WIDTH)
    streammux.set_property("height", STREAM_HEIGHT)
    streammux.set_property("batch-size", num_sources)
    streammux.set_property("batched-push-timeout", 40000)
    streammux.set_property("live-source", 1)
    streammux.set_property("nvbuf-memory-type", 4)

    for i, device in enumerate(CAMERA_DEVICES):
        src_tail = build_usb_source_bin(pipeline, i, device)
        sinkpad = streammux.get_request_pad(f"sink_{i}")
        srcpad = src_tail.get_static_pad("src")
        if not sinkpad or not srcpad:
            sys.stderr.write(f"ERROR: Could not get pads for source {i}\n")
            sys.exit(1)
        srcpad.link(sinkpad)

    pgie = make_elm("nvinfer", "primary-inference")
    pgie.set_property("config-file-path", PGIE_CONFIG)
    pgie.set_property("batch-size", num_sources)
    pipeline.add(pgie)

    nvvidconv_osd = make_elm("nvvideoconvert", "convertor-osd")
    pipeline.add(nvvidconv_osd)

    caps_osd = make_elm("capsfilter", "caps-osd")
    caps_osd.set_property("caps", Gst.Caps.from_string("video/x-raw(memory:NVMM), format=RGBA"))
    pipeline.add(caps_osd)

    nvdsosd = make_elm("nvdsosd", "onscreendisplay")
    pipeline.add(nvdsosd)

    osd_sink_pad = nvdsosd.get_static_pad("sink")
    if not osd_sink_pad:
        sys.stderr.write("ERROR: Unable to get sink pad of nvdsosd\n")
        sys.exit(1)
    osd_sink_pad.add_probe(Gst.PadProbeType.BUFFER, osd_sink_pad_buffer_probe, 0)

    tiler = make_elm("nvmultistreamtiler", "tiler")
    tiler.set_property("rows", 1)
    tiler.set_property("columns", num_sources)
    tiler.set_property("width", STREAM_WIDTH * num_sources)
    tiler.set_property("height", STREAM_HEIGHT)
    pipeline.add(tiler)

    nvvidconv_final = make_elm("nvvideoconvert", "convertor-final")
    pipeline.add(nvvidconv_final)

    sink = make_elm("nveglglessink", "nvvideo-renderer")
    sink.set_property("sync", 0)
    pipeline.add(sink)

    streammux.link(pgie)
    pgie.link(nvvidconv_osd)
    nvvidconv_osd.link(caps_osd)
    caps_osd.link(nvdsosd)
    nvdsosd.link(tiler)
    tiler.link(nvvidconv_final)
    nvvidconv_final.link(sink)

    return pipeline


def bus_call(bus, message, loop):
    t = message.type
    if t == Gst.MessageType.EOS:
        sys.stdout.write("End-of-stream\n")
        loop.quit()
    elif t == Gst.MessageType.WARNING:
        err, debug = message.parse_warning()
        sys.stderr.write(f"WARNING: {err}: {debug}\n")
    elif t == Gst.MessageType.ERROR:
        err, debug = message.parse_error()
        sys.stderr.write(f"ERROR: {err}: {debug}\n")
        loop.quit()
    return True


def run():
    pipeline = build_pipeline()
    loop = GLib.MainLoop()
    bus = pipeline.get_bus()
    bus.add_signal_watch()
    bus.connect("message", bus_call, loop)

    pipeline.set_state(Gst.State.PLAYING)
    try:
        loop.run()
    except KeyboardInterrupt:
        pass
    finally:
        pipeline.set_state(Gst.State.NULL)


if __name__ == "__main__":
    run()
