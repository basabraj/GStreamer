import gi
import numpy as np
import queue
import threading
import subprocess
import wave
import os
import cv2

gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

Gst.init(None)

WIDTH, HEIGHT, FPS = 640, 480, 30

frame_queue = queue.Queue(maxsize=2)

# Scanning box (corner brackets) overlay, centered in the frame
BOX_SIZE   = 280
BOX_X1     = (WIDTH - BOX_SIZE) // 2
BOX_Y1     = (HEIGHT - BOX_SIZE) // 2
BOX_X2     = BOX_X1 + BOX_SIZE
BOX_Y2     = BOX_Y1 + BOX_SIZE
CORNER_LEN = 30
BOX_COLOR  = (255, 255, 255)


def draw_scan_box(frame):
    corners = [
        (BOX_X1, BOX_Y1, 1, 1),    # top-left
        (BOX_X2, BOX_Y1, -1, 1),   # top-right
        (BOX_X1, BOX_Y2, 1, -1),   # bottom-left
        (BOX_X2, BOX_Y2, -1, -1),  # bottom-right
    ]
    for x, y, dx, dy in corners:
        cv2.line(frame, (x, y), (x + dx * CORNER_LEN, y), BOX_COLOR, 3)
        cv2.line(frame, (x, y), (x, y + dy * CORNER_LEN), BOX_COLOR, 3)
    return frame


# "Done" chime played once a QR code is decoded
DONE_SOUND_PATH = "/tmp/qr_done.wav"


def generate_done_sound(path=DONE_SOUND_PATH):
    if os.path.exists(path):
        return
    sample_rate = 44100
    tones = [(880, 0.12), (1320, 0.18)]
    chunks = []
    for freq, duration in tones:
        t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
        chunks.append((np.sin(2 * np.pi * freq * t) * 0.3 * 32767).astype(np.int16))
    audio = np.concatenate(chunks)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio.tobytes())


def play_done_sound():
    generate_done_sound()
    subprocess.run(["aplay", "-q", DONE_SOUND_PATH], check=False)


# Stop PipeWire before running:
# INPUT: tee splits stream — autovideosink for display, appsink for processing

pipeline_in = Gst.parse_launch(
    f"libcamerasrc af-mode=2 af-range=2 ! "
    f"video/x-raw,format=RGBx,width={WIDTH},height={HEIGHT},framerate={FPS}/1 ! "
    f"tee name=t "
    f"t. ! queue ! videoconvert ! autovideosink sync=false "
    f"t. ! queue ! videoconvert ! video/x-raw,format=BGR ! "
    f"appsink name=sink emit-signals=true sync=false max-buffers=1 drop=true"
)
sink = pipeline_in.get_by_name("sink")

# OUTPUT: processed BGR frames -> H264 RTP -> UDP
pipeline_out = Gst.parse_launch(
    f"appsrc name=src format=time is-live=true "
    f"caps=video/x-raw,format=BGR,width={WIDTH},height={HEIGHT},framerate={FPS}/1 ! "
    f"videoconvert ! "
    f"x264enc tune=zerolatency bitrate=2000 speed-preset=ultrafast ! "
    f"rtph264pay ! "
    f"udpsink host=127.0.0.1 port=6000"
)
src = pipeline_out.get_by_name("src")

# Pull BGR frame from appsink into queue for Python processing
def on_new_sample(appsink, data):
    sample = appsink.emit("pull-sample")
    if sample is None:
        return Gst.FlowReturn.ERROR

    buf   = sample.get_buffer()
    size  = buf.get_size()
    frame = np.ndarray(
        (HEIGHT, WIDTH, 3),
        buffer=buf.extract_dup(0, size),
        dtype=np.uint8
    ).copy()

    try:
        frame_queue.put_nowait(frame)
    except queue.Full:
        pass

    return Gst.FlowReturn.OK

sink.connect("new-sample", on_new_sample, sink)

loop = GLib.MainLoop()

def on_bus_message(bus, message):
    t = message.type
    if t == Gst.MessageType.ERROR:
        err, debug = message.parse_error()
        if "Output window was closed" in str(err):
            print("[INFO] Window closed — stopping.")
        else:
            print(f"[ERROR] {err}\n[DEBUG] {debug}")
        loop.quit()
    elif t == Gst.MessageType.EOS:
        loop.quit()

for pipeline in [pipeline_in, pipeline_out]:
    bus = pipeline.get_bus()
    bus.add_signal_watch()
    bus.connect("message", on_bus_message)

# GLib loop must start before set_state
glib_thread = threading.Thread(target=loop.run, daemon=True)
glib_thread.start()

pipeline_in.set_state(Gst.State.PLAYING)
pipeline_out.set_state(Gst.State.PLAYING)

ret, state, _ = pipeline_in.get_state(5 * Gst.SECOND)
print(f"[PIPELINE IN] {ret.value_nick} / state={state.value_nick}")
print("Camera running. Press Ctrl+C to quit.")

# Main thread: pull frames for processing and push to output
timestamp      = 0
frame_duration = Gst.SECOND // FPS

qr_detector = cv2.QRCodeDetector()

try:
    while True:
        try:
            frame = frame_queue.get(timeout=1.0)
        except queue.Empty:
            continue

        ### PYTHON IMAGE PROCESSING HERE ###

        # QR code detection
        data, points, _ = qr_detector.detectAndDecode(frame)

        draw_scan_box(frame)

        if data:
            pts = points.astype(int).reshape(-1, 2)
            cv2.polylines(frame, [pts], isClosed=True, color=(0, 255, 0), thickness=2)
            cv2.putText(frame, data, (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2, cv2.LINE_AA)

        # Push processed frame to output pipeline
        buf          = Gst.Buffer.new_wrapped(frame.tobytes())
        buf.pts      = timestamp
        buf.dts      = timestamp
        buf.duration = frame_duration
        timestamp   += frame_duration
        src.emit("push-buffer", buf)

        if data:
            print(f"[QR] {data}")
            play_done_sound()
            break

except KeyboardInterrupt:
    pass
finally:
    loop.quit()
    pipeline_in.set_state(Gst.State.NULL)
    pipeline_out.set_state(Gst.State.NULL)
