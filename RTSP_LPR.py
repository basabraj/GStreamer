#!/usr/bin/env python3
"""
LPR (License Plate Recognition) on a live RTSP H.265 CCTV stream.
Extends RTSP_bullet.py: replaces autovideosink with appsink,
runs Hailo LPRNet inference on each frame, and displays with OpenCV.

NOTE: LPRNet reads text from a PRE-CROPPED plate image (300x75 px).
      This script resizes the whole frame to 300x75 and feeds it to LPRNet.
      For reliable results, zoom the camera so the plate fills most of the frame,
      or integrate a plate-detector in front of LPRNet (two-stage pipeline).
"""

import sys
import threading
import queue
from pathlib import Path

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

import numpy as np
import cv2

Gst.init(None)

# ── Hailo path ─────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path("/home/pi/Desktop/hailo_apps")))
from hailo_apps.python.core.common.hailo_inference import HailoInfer

# ── Config ─────────────────────────────────────────────────────────────────────
RTSP_URL = "rtsp://192.168.96.31:554/rtsp/streaming?channel=01&subtype=A"
HEF_PATH = "/home/pi/Desktop/hailo_apps/python/standalone_apps/object_detection/lprnet.hef"

# LPRNet character set: digits 0-9, blank class at index 10
CHARS = "0123456789"
BLANK = 10


# ── CTC greedy decoder ─────────────────────────────────────────────────────────
def ctc_decode(logits: np.ndarray) -> str:
    """
    Args:
        logits: shape (T, C) — T timesteps, C classes (19 x 11 for LPRNet)
    Returns:
        decoded plate string, e.g. "1234567"
    """
    best = np.argmax(logits, axis=-1).tolist()   # (T,)
    # collapse consecutive repeats
    collapsed = [best[0]]
    for c in best[1:]:
        if c != collapsed[-1]:
            collapsed.append(c)
    # remove blank tokens
    return "".join(CHARS[c] for c in collapsed if c != BLANK)


# ── Main application ───────────────────────────────────────────────────────────
class RTSPLPRApp:
    def __init__(self):
        print("[LPRNet] Loading model …")
        self.hailo = HailoInfer(HEF_PATH, batch_size=1)
        self.input_h, self.input_w, _ = self.hailo.get_input_shape()
        print(f"[LPRNet] Input size: {self.input_w}×{self.input_h}")

        self.frame_q    = queue.Queue(maxsize=2)
        self.plate_text = ""
        self.text_lock  = threading.Lock()

        self.pipeline = self._build_pipeline()
        self.loop     = GLib.MainLoop()

    # ── GStreamer pipeline ──────────────────────────────────────────────────────
    def _build_pipeline(self):
        pipe = Gst.Pipeline.new("rtsp-lpr")

        source  = Gst.ElementFactory.make("rtspsrc",      "source")
        depay   = Gst.ElementFactory.make("rtph265depay", "depay")
        parser  = Gst.ElementFactory.make("h265parse",    "parser")
        decoder = Gst.ElementFactory.make("avdec_h265",   "decoder")
        convert = Gst.ElementFactory.make("videoconvert", "convert")
        capsf   = Gst.ElementFactory.make("capsfilter",   "capsfilter")
        sink    = Gst.ElementFactory.make("appsink",      "sink")

        if not all([source, depay, parser, decoder, convert, capsf, sink]):
            raise RuntimeError("Failed to create one or more GStreamer elements")

        source.set_property("location", RTSP_URL)
        source.set_property("latency",  200)

        capsf.set_property("caps", Gst.Caps.from_string("video/x-raw,format=RGB"))

        sink.set_property("emit-signals", True)
        sink.set_property("max-buffers",  2)
        sink.set_property("drop",         True)
        sink.set_property("sync",         False)
        sink.connect("new-sample", self._on_new_sample)

        for el in [source, depay, parser, decoder, convert, capsf, sink]:
            pipe.add(el)

        self._depay = depay
        source.connect("pad-added", self._on_pad_added)

        depay.link(parser)
        parser.link(decoder)
        decoder.link(convert)
        convert.link(capsf)
        capsf.link(sink)

        return pipe

    def _on_pad_added(self, src, pad):
        caps      = pad.get_current_caps() or pad.query_caps(None)
        structure = caps.get_structure(0)
        media     = structure.get_string("media")
        encoding  = structure.get_string("encoding-name")

        print(f"[PAD] media={media}, encoding={encoding}")
        if media != "video" or encoding != "H265":
            print("[PAD] Skipping non-H265 pad")
            return

        sink_pad = self._depay.get_static_pad("sink")
        if not sink_pad.is_linked():
            ret = pad.link(sink_pad)
            print(f"[PAD] Linked: {ret}")

    # ── appsink callback (GStreamer thread) ─────────────────────────────────────
    def _on_new_sample(self, sink):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.ERROR

        buf  = sample.get_buffer()
        caps = sample.get_caps()
        s    = caps.get_structure(0)
        w    = s.get_int("width").value
        h    = s.get_int("height").value

        ok, map_info = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.ERROR

        frame = np.frombuffer(map_info.data, dtype=np.uint8).reshape((h, w, 3)).copy()
        buf.unmap(map_info)

        try:
            self.frame_q.put_nowait(frame)
        except queue.Full:
            pass   # drop frame — inference is still busy

        return Gst.FlowReturn.OK

    # ── Inference + display thread ──────────────────────────────────────────────
    def _infer_loop(self):
        while True:
            try:
                frame = self.frame_q.get(timeout=1.0)
            except queue.Empty:
                continue

            if frame is None:
                break

            # Resize full frame to LPRNet input resolution (width × height)
            plate = cv2.resize(frame, (self.input_w, self.input_h))

            # ── Hailo async inference ───────────────────────────────────────────
            done   = threading.Event()
            result = {"text": ""}

            def _callback(completion_info, bindings_list, _r=result, _d=done):
                try:
                    if completion_info.exception:
                        print(f"[Hailo] Inference error: {completion_info.exception}")
                        return
                    b   = bindings_list[0]
                    out = b.output().get_buffer()   # numpy array
                    # Handle various output shapes: (5,19,11), (1,19,11), (19,11)
                    if out.ndim == 3:
                        logits = out[-1]            # take last slice → (19, 11)
                    elif out.ndim == 2:
                        logits = out                # already (T, C)
                    else:
                        logits = out.reshape(-1, len(CHARS) + 1)
                    _r["text"] = ctc_decode(logits)
                finally:
                    _d.set()

            self.hailo.run([plate], _callback)
            done.wait(timeout=2.0)

            with self.text_lock:
                self.plate_text = result["text"]

            # ── Display ─────────────────────────────────────────────────────────
            display = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            label   = f"Plate: {self.plate_text}" if self.plate_text else "Plate: ---"

            # Black banner at top for readability
            cv2.rectangle(display, (0, 0), (420, 52), (0, 0, 0), -1)
            cv2.putText(display, label, (8, 38),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 255, 0), 2, cv2.LINE_AA)

            cv2.imshow("LPR - CCTV  [q = quit]", display)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                self.loop.quit()
                break

    # ── GStreamer bus ───────────────────────────────────────────────────────────
    def _on_bus_message(self, bus, message):
        t = message.type
        if t == Gst.MessageType.ERROR:
            err, dbg = message.parse_error()
            print(f"[ERROR] {err}\n[DEBUG] {dbg}")
            self.loop.quit()
        elif t == Gst.MessageType.EOS:
            print("[EOS] Stream ended")
            self.loop.quit()
        elif t == Gst.MessageType.STATE_CHANGED:
            if message.src == self.pipeline:
                old, new, _ = message.parse_state_changed()
                print(f"[STATE] {old.value_nick} → {new.value_nick}")

    # ── Run ─────────────────────────────────────────────────────────────────────
    def run(self):
        infer_thread = threading.Thread(target=self._infer_loop, daemon=True)
        infer_thread.start()

        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_bus_message)

        self.pipeline.set_state(Gst.State.PLAYING)
        print("Stream started. Press 'q' in the video window to quit.")

        try:
            self.loop.run()
        except KeyboardInterrupt:
            pass
        finally:
            self.pipeline.set_state(Gst.State.NULL)
            self.frame_q.put(None)      # stop inference thread
            self.hailo.close()
            cv2.destroyAllWindows()
            print("Stopped.")


if __name__ == "__main__":
    RTSPLPRApp().run()
