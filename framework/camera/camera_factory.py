"""
framework/camera/camera_factory.py — builds the source-through-NVMM
segment of a DeepStream pipeline for a given camera source type, so
StreamManager doesn't need to know the difference between USB, RTSP,
or (later) CSI sources.

Design constraint: USB's v4l2src has a static pad, so it can be linked
synchronously in sequence like everything else in the pipeline. RTSP's
rtspsrc has a DYNAMIC pad -- it isn't available until rtspsrc
negotiates the stream over the network, so it must be linked via a
pad-added signal callback instead of a direct .link() call. This
factory owns that asynchrony so callers never have to special-case it.

Every build_* method: creates its elements, adds them to `pipeline`,
links everything through to a final NVMM capsfilter, and links that
capsfilter's src pad into `streammux`'s already-requested `sink_pad`.
For USB this happens synchronously before the function returns; for
RTSP the final link happens later, inside the pad-added callback, once
rtspsrc has actually negotiated the incoming stream.
"""
import sys
from gi.repository import Gst

STREAM_WIDTH = 640
STREAM_HEIGHT = 480
FPS = 30


def _make_elm(factory_name, name):
    elm = Gst.ElementFactory.make(factory_name, name)
    if not elm:
        raise RuntimeError(f"Unable to create element {factory_name} ({name})")
    return elm


class CameraFactory:
    @staticmethod
    def create(pipeline, slot_id, source_type, source_uri, streammux, sink_pad):
        """
        source_type: "usb" or "rtsp"
        source_uri: /dev/videoX for usb, rtsp://... for rtsp
        sink_pad: streammux's already-requested sink_0 pad (caller owns
        requesting/releasing it, same as today).
        """
        if source_type == "usb":
            CameraFactory._build_usb(pipeline, slot_id, source_uri, sink_pad)
        elif source_type == "rtsp":
            CameraFactory._build_rtsp(pipeline, slot_id, source_uri, sink_pad)
        else:
            raise ValueError(f"Unknown camera source type: {source_type!r}")

    # ── USB (v4l2) ──────────────────────────────────────────────
    # Exactly the pipeline segment StreamManager.start() built inline
    # before this refactor -- unchanged element-for-element, just
    # relocated. Verified byte-identical behavior against the
    # pre-refactor version before this was trusted live.
    @staticmethod
    def _build_usb(pipeline, slot_id, device_path, sink_pad):
        source = _make_elm("v4l2src", f"src-{slot_id}")
        source.set_property("device", device_path)

        caps_v4l2 = _make_elm("capsfilter", f"v4l2caps-{slot_id}")
        caps_v4l2.set_property(
            "caps",
            Gst.Caps.from_string(
                f"video/x-raw, width={STREAM_WIDTH}, height={STREAM_HEIGHT}, framerate={FPS}/1"
            ),
        )

        vidconv = _make_elm("videoconvert", f"vidconv-{slot_id}")
        nvvidconv_in = _make_elm("nvvideoconvert", f"nvvidconv-in-{slot_id}")

        caps_nvmm = _make_elm("capsfilter", f"nvmmcaps-{slot_id}")
        caps_nvmm.set_property("caps", Gst.Caps.from_string("video/x-raw(memory:NVMM), format=NV12"))

        for e in (source, caps_v4l2, vidconv, nvvidconv_in, caps_nvmm):
            pipeline.add(e)

        source.link(caps_v4l2)
        caps_v4l2.link(vidconv)
        vidconv.link(nvvidconv_in)
        nvvidconv_in.link(caps_nvmm)

        srcpad = caps_nvmm.get_static_pad("src")
        srcpad.link(sink_pad)

    # ── RTSP ────────────────────────────────────────────────────
    # rtspsrc's src pad is dynamic -- only appears once the RTSP
    # negotiation with the camera completes, so depay/parse/decoder
    # linking happens inside _on_pad_added, not inline here.
    @staticmethod
    def _build_rtsp(pipeline, slot_id, rtsp_url, sink_pad):
        rtspsrc = _make_elm("rtspsrc", f"rtspsrc-{slot_id}")
        rtspsrc.set_property("location", rtsp_url)
        rtspsrc.set_property("latency", 200)

        depay = _make_elm("rtph264depay", f"depay-{slot_id}")
        parse = _make_elm("h264parse", f"parse-{slot_id}")
        decoder = _make_elm("nvv4l2decoder", f"decoder-{slot_id}")

        caps_nvmm = _make_elm("capsfilter", f"nvmmcaps-{slot_id}")
        caps_nvmm.set_property("caps", Gst.Caps.from_string("video/x-raw(memory:NVMM), format=NV12"))

        for e in (rtspsrc, depay, parse, decoder, caps_nvmm):
            pipeline.add(e)

        depay.link(parse)
        parse.link(decoder)
        decoder.link(caps_nvmm)

        srcpad = caps_nvmm.get_static_pad("src")
        srcpad.link(sink_pad)

        def _on_pad_added(element, pad):
            caps = pad.query_caps(None)
            structure = caps.get_structure(0)
            media = structure.get_string("media")
            encoding = structure.get_string("encoding-name")

            if media == "video" and encoding == "H264":
                sinkpad = depay.get_static_pad("sink")
                if not sinkpad.is_linked():
                    pad.link(sinkpad)
            else:
                sys.stderr.write(
                    f"[slot {slot_id}] RTSP stream is not H264 "
                    f"(got media={media}, encoding={encoding}) -- unsupported.\n"
                )

        rtspsrc.connect("pad-added", _on_pad_added)
