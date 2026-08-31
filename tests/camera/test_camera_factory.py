"""
tests/camera/test_camera_factory.py

SCOPE NOTE: deliberately thin. CameraFactory builds real GStreamer
elements requiring actual hardware/network state to reach PLAYING --
not something a unit test can safely fake. Real verification is the
manual hardware tests already run this session. This only covers
input validation, the one behavior safe to unit test.

Run from repo root:
    python3 -m unittest tests.camera.test_camera_factory -v
"""
import sys
import os
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "backend"))

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst

from framework.camera.camera_factory import CameraFactory

Gst.init(None)


class TestCameraFactoryValidation(unittest.TestCase):
    def test_unknown_source_type_raises_value_error(self):
        pipeline = Gst.Pipeline()
        streammux = Gst.ElementFactory.make("nvstreammux", "mux-test")
        pipeline.add(streammux)
        sink_pad = streammux.get_request_pad("sink_0")

        with self.assertRaises(ValueError):
            CameraFactory.create(
                pipeline, slot_id=0, source_type="bluetooth",
                source_uri="whatever", streammux=streammux, sink_pad=sink_pad,
            )

    def test_unknown_source_type_error_names_the_bad_value(self):
        pipeline = Gst.Pipeline()
        streammux = Gst.ElementFactory.make("nvstreammux", "mux-test2")
        pipeline.add(streammux)
        sink_pad = streammux.get_request_pad("sink_0")

        with self.assertRaises(ValueError) as ctx:
            CameraFactory.create(
                pipeline, slot_id=0, source_type="bluetooth",
                source_uri="whatever", streammux=streammux, sink_pad=sink_pad,
            )
        self.assertIn("bluetooth", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
