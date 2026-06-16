# QR Code Scanners — `qr_scanner_rpi.py` & `qr_scanner_RTSP.py`

Two standalone, one-shot QR code scanners built on the same GStreamer pipeline
pattern used elsewhere in this project. Each script captures live video,
decodes QR codes with OpenCV, draws an outline around any QR code found,
plays a "done" chime, and exits.

---

## Files

| File | Video source | Notes |
|---|---|---|
| `qr_scanner_rpi.py` | Raspberry Pi Camera Module (via `libcamerasrc`) | Has a fixed corner-bracket scan-box overlay |
| `qr_scanner_RTSP.py` | Network RTSP camera (H.265) | No fixed overlay — only draws a box when a QR code is actually detected |

Both scripts are self-contained — they do **not** import `rpi_camera.py` or
`RTSP_correct.py`, they just follow the same pipeline architecture.

---

## Setup (one-time)

### 1. System packages

```bash
sudo apt-get update
sudo apt-get install -y \
    python3-gi \
    python3-gi-cairo \
    gir1.2-gstreamer-1.0 \
    gir1.2-gst-plugins-base-1.0 \
    libgirepository1.0-dev \
    libcairo2-dev \
    pkg-config \
    gstreamer1.0-tools \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly \
    gstreamer1.0-libav \
    gstreamer1.0-libcamera \
    gstreamer1.0-pipewire \
    libgtk-3-dev \
    libgtk2.0-dev \
    alsa-utils
```

`gstreamer1.0-libcamera` is required for `qr_scanner_rpi.py` (provides the
`libcamerasrc` element). `alsa-utils` provides `aplay`, used for the done
chime.

### 2. Python virtual environment

```bash
cd "GStreamer Pipeline"
python3.11 -m venv gstreamer_env
source gstreamer_env/bin/activate
pip install -r requirements.txt   # requirements.txt is in the project root
```

`requirements.txt` installs:
- `PyGObject` — GStreamer (`gi.repository.Gst`/`GLib`) bindings
- `pycairo` — PyGObject dependency
- `numpy` — frame buffer handling + chime audio generation
- `opencv-python` — QR detection only (no GUI/GStreamer support needed)

To activate the venv in a new terminal (run from the parent of `GStreamer Pipeline/`):
```bash
source "GStreamer Pipeline/gstreamer_env/bin/activate"
```

---

## `qr_scanner_rpi.py` — Pi Camera scanner

### How it works

```
libcamerasrc (af-mode=2 af-range=2, RGBx)
      |
     tee
     |--- queue → videoconvert → autovideosink         (local preview)
     |--- queue → videoconvert → BGR → appsink          (Python processing)
                                          |
                                       appsrc → x264enc → rtph264pay → udpsink:6000
```

- `libcamerasrc` captures from the Pi Camera Module at 640×480 @30fps.
  `af-mode=2 af-range=2` enables **continuous autofocus over the full range**
  (fixes blurry close-up QR codes).
- A `tee` splits the raw stream: one branch goes straight to a local preview
  window (`autovideosink`), the other goes to Python via `appsink`.
- In the main loop, every frame gets:
  1. A fixed white corner-bracket **scan box** drawn in the center
     (`draw_scan_box()`), as a visual guide for where to hold the QR code.
  2. QR detection via `cv2.QRCodeDetector()`.
  3. If a QR code is found: a green outline + the decoded text are drawn on
     the frame, a "done" chime is played, and the script exits (one-shot —
     no continuous re-detection).
- The local preview branch shows the **raw** frame (not the annotated one);
  the annotated frame is only sent out over UDP (`appsrc → udpsink:6000`).

### How to run

**Step 1 — Stop PipeWire** (it holds exclusive access to the camera):
```bash
systemctl --user stop wireplumber pipewire pipewire-pulse
systemctl --user stop pipewire.socket pipewire-pulse.socket
```

**Step 2 — Activate the venv and run** (from the `GStreamer Pipeline/` root):
```bash
source gstreamer_env/bin/activate
python QR/qr_scanner_rpi.py
```

A preview window opens with the scan box. Hold a QR code in front of the
camera. On detection, a chime plays, `[QR] <decoded data>` is printed, and
the script exits. Press `Ctrl+C` to quit early.

**Step 3 — Restart PipeWire when done** (needed for audio etc.):
```bash
systemctl --user start pipewire pipewire-pulse wireplumber
```

To view the annotated/UDP stream on another machine:
```bash
gst-launch-1.0 udpsrc port=6000 ! \
  application/x-rtp,encoding-name=H264 ! \
  rtph264depay ! avdec_h264 ! autovideosink
```

---

## `qr_scanner_RTSP.py` — Network RTSP camera scanner

### How it works

```
rtspsrc → rtph265depay → h265parse → avdec_h265 → videoconvert → videoscale → BGR → appsink
                                                                                          |
appsrc → tee → queue → videoconvert → autovideosink              (local preview, processed)
              → queue → videoconvert → x264enc → rtph264pay → udpsink:6000  (network, processed)
```

- `rtspsrc` connects to the camera at `RTSP_URL` (H.265 stream, native
  1920×1080). `on_pad_added` dynamically links the video pad once the
  stream negotiates.
- The decoded frame is scaled to **1280×720 BGR** and pulled into Python via
  `appsink`.
- QR detection uses `cv2.QRCodeDetectorAruco()` (more robust to blur/tilt
  than the basic `cv2.QRCodeDetector()`).
- There is **no fixed scan-box overlay** — a green outline is drawn only when
  a QR code is actually detected.
- Every processed frame (with or without the QR outline) is pushed into
  `pipeline_out`, which uses a `tee` to send it to **both** a local preview
  window (`autovideosink`) and a UDP stream (`udpsink:6000`). This way the
  preview shows exactly what's being detected.
- On detection: prints `[QR] <decoded data>`, plays the "done" chime, and
  exits (one-shot).

### How to run

PipeWire does **not** need to be stopped for RTSP (no local camera hardware
involved).

**Step 1 — Set the RTSP URL** in `QR/qr_scanner_RTSP.py`:
```python
RTSP_URL = "rtsp://username:password@camera-ip:554/stream-path"
```

**Step 2 — Activate the venv and run** (from the `GStreamer Pipeline/` root):
```bash
source gstreamer_env/bin/activate
python QR/qr_scanner_RTSP.py
```

A preview window opens showing the live RTSP feed. Hold a QR code in view of
the camera. On detection, a chime plays, `[QR] <decoded data>` is printed,
and the script exits. Press `Ctrl+C` to quit early.

To view the UDP stream on another machine:
```bash
gst-launch-1.0 udpsrc port=6000 ! \
  application/x-rtp,encoding-name=H264 ! \
  rtph264depay ! avdec_h264 ! autovideosink
```

---

## The "done" chime

Both scripts generate a short two-tone WAV file at `/tmp/qr_done.wav` (once,
on first use) and play it with `aplay` when a QR code is decoded.

If you don't hear anything, the HDMI output volume may be at 0%:
```bash
pactl set-sink-volume alsa_output.platform-107c701400.hdmi.hdmi-stereo 100%
```
This resets on reboot — re-run it if the chime goes silent again. To check
the current level: `pactl list sinks | grep -A1 Volume`.

---

## Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| `gst_parse_error: no element "libcamerasrc"` | Missing `gstreamer1.0-libcamera` package | `sudo apt-get install -y gstreamer1.0-libcamera` |
| `Camera already in use` | PipeWire holds the camera, or a stale process is running | Stop PipeWire (see above) and `pkill -f qr_scanner_rpi.py` if a previous run is still alive |
| QR code blurry / not detected (Pi camera) | Autofocus not engaged | Already handled via `af-mode=2 af-range=2` in `qr_scanner_rpi.py` |
| QR code not detected (RTSP) | Low resolution / detector limits | Already handled via 1280×720 processing + `cv2.QRCodeDetectorAruco()` |
| No chime plays | HDMI sink volume at 0% | `pactl set-sink-volume alsa_output.platform-107c701400.hdmi.hdmi-stereo 100%` |
| No display window | `opencv-python` has no GUI support on ARM | Both scripts use `autovideosink`, not `cv2.imshow` — don't add `cv2.imshow` |
