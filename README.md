# GStreamer Pipeline — Raspberry Pi Camera & RTSP Streaming

A Python-based video streaming project using GStreamer on a Raspberry Pi 5. It captures live video from the Raspberry Pi Camera Module, displays it locally, processes frames with NumPy, and streams encoded H.264 video over UDP. It also includes RTSP receivers for single and multi-camera network streams displayed in a composite window.

---

## Project Files

### `IP camera/` — Pi Camera & RTSP viewers

| File | Description |
|---|---|
| `IP camera/rpi_camera.py` | Captures from Pi camera, displays locally, streams H.264 via UDP |
| `IP camera/RTSP_correct.py` | Receives and displays a single RTSP H.265 stream from a network camera |
| `IP camera/RTSP_wrong.py` | Early prototype — kept for reference only |

### Root — Multi-camera & LPR

| File | Description |
|---|---|
| `RTSP_pipeline string approach.py` | Receives two RTSP streams and displays them side by side in one composite window |
| `RTSP_bullet.py` | Receives and displays an RTSP stream from the IR bullet camera |
| `RTSP_LPR.py` | RTSP receiver with Hailo-based licence-plate recognition |

### `QR/` — QR code scanners

| File | Description |
|---|---|
| `QR/qr_scanner_rpi.py` | One-shot QR scanner using the Pi camera (libcamerasrc) |
| `QR/qr_scanner_RTSP.py` | One-shot QR scanner using a network RTSP camera (H.265) |

---

## Hardware Requirements

- **Raspberry Pi 5** (tested on Raspberry Pi OS Bookworm 64-bit)
- **Raspberry Pi Camera Module 3** (IMX708 sensor) connected via CSI ribbon cable
- For RTSP streaming: network IP cameras (H.264 or H.265) accessible on the local network

---

## Software Requirements

### Operating System
- Raspberry Pi OS Bookworm (Debian 12), 64-bit
- Python 3.11

### System Packages

Install with `apt`:

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
    gstreamer1.0-pipewire \
    libgtk-3-dev \
    libgtk2.0-dev
```

### Python Virtual Environment

```bash
cd "GStreamer Pipeline"
python3.11 -m venv gstreamer_env
source gstreamer_env/bin/activate
pip install -r requirements.txt
```

---

## How to Use

### 1. Pi Camera Stream — `rpi_camera.py`

Captures live video from the Raspberry Pi Camera Module, splits the stream into:
- A local display window via `autovideosink`
- A Python frame buffer via `appsink` for image processing
- An H.264 encoded UDP stream sent to port 6000

**Pipeline architecture:**
```
libcamerasrc (RGBx)
      |
     tee
     |--- queue → videoconvert → autovideosink        (display)
     |--- queue → videoconvert → BGR → appsink         (processing)
                                          |
                                       appsrc → x264enc → rtph264pay → udpsink:6000
```

**Step 1 — Stop PipeWire** (it holds exclusive camera access):
```bash
systemctl --user stop wireplumber pipewire pipewire-pulse
```

**Step 2 — Activate the virtual environment and run:**
```bash
source gstreamer_env/bin/activate
python "IP camera/rpi_camera.py"
```

**Step 3 — To receive the UDP stream on another machine:**
```bash
gst-launch-1.0 udpsrc port=6000 ! \
  application/x-rtp,encoding-name=H264 ! \
  rtph264depay ! avdec_h264 ! autovideosink
```

**Step 4 — Restart PipeWire when done:**
```bash
systemctl --user start pipewire pipewire-pulse wireplumber
```

**To add custom image processing**, edit the marked section in `IP camera/rpi_camera.py`:
```python
### PYTHON IMAGE PROCESSING HERE ###
```

---

### 2. Single RTSP Camera — `RTSP_correct.py`

Receives an H.265 RTSP stream from a network IP camera and displays it in a window.

**Pipeline architecture:**
```
rtspsrc → rtph265depay → h265parse → avdec_h265 → videoconvert → autovideosink
```

**Step 1 — Set the RTSP URL** in `IP camera/RTSP_correct.py`:
```python
RTSP_URL = "rtsp://username:password@camera-ip:554/stream-path"
```

**Step 2 — Run:**
```bash
source gstreamer_env/bin/activate
python "IP camera/RTSP_correct.py"
```

> Note: PipeWire does **not** need to be stopped for RTSP streaming.

---

### 3. Dual Camera Composite View — `RTSP_pipeline string approach.py`

Receives two RTSP streams simultaneously and displays them **side by side in a single 1280×480 window** using GStreamer's `compositor` element. Each feed is labeled with its camera name using `textoverlay`.

**Window layout:**
```
┌─────────────────────┬─────────────────────┐
│   CAM 1 - MAIN      │  CAM 2 - IR BULLET  │
│      640×480        │      640×480         │
└─────────────────────┴─────────────────────┘
              1280×480 single window
```

**Pipeline architecture:**
```
rtspsrc (MAIN)   → decodebin → videoconvert → videoscale → textoverlay ─┐
                                                                         ├→ compositor → autovideosink
rtspsrc (BULLET) → decodebin → videoconvert → videoscale → textoverlay ─┘
```

**Camera URLs configured in the script:**
```python
RTSP_URL_MAIN   = "rtsp://admin:Digital%40123@192.168.96.104:554/live3.sdp"
RTSP_URL_BULLET = "rtsp://192.168.96.31:554/rtsp/streaming?channel=01&subtype=A"
```

**Run:**
```bash
source gstreamer_env/bin/activate
python "RTSP_pipeline string approach.py"
```

**Key design decisions:**
- `decodebin` is used for both cameras — it auto-detects the codec (H.264 or H.265), so no manual codec configuration is needed
- MAIN camera uses `protocols=udp+tcp` (UDP preferred, TCP fallback)
- BULLET camera uses `protocols=tcp` (forced TCP — this camera resets UDP connections)
- Closing the composite window exits the script cleanly

---

## Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| `Camera already in use` | PipeWire holds the camera | `systemctl --user stop wireplumber pipewire pipewire-pulse` |
| `Could not open camera pipeline` | Stale `gst-launch` process running | `pkill -f gst-launch` |
| `GST_PAD_LINK_NOFORMAT` | Wrong codec in pipeline | Use `decodebin` for auto codec detection |
| `streaming stopped, reason error (-5)` | Camera rejects UDP connections | Add `protocols=tcp` to `rtspsrc` |
| `Pipeline stuck at paused` | GLib loop not running before `set_state` | Ensure `loop.run()` thread starts before `set_state(PLAYING)` |
| No display window | `opencv-python` installed without GUI | Use `autovideosink` instead of `cv2.imshow` |
| Segmentation fault on display | Qt threading conflict with GLib | Never call `cv2.imshow` from a GLib callback thread |

---

## Notes

- `libcamerasrc` requires PipeWire to be stopped because PipeWire holds exclusive access to the camera via libcamera on Raspberry Pi OS Bookworm.
- `pipewiresrc` (GStreamer PipeWire plugin) is installed but does not complete state negotiation when used via Python GObject bindings — use `libcamerasrc` with PipeWire stopped instead.
- `opencv-python` from PyPI is built **without** GStreamer support on ARM. Use `PyGObject` (GI bindings) for all GStreamer pipeline control.
- `decodebin` is recommended over hardcoded codec elements (`rtph264depay`, `rtph265depay`) when the camera codec is unknown or may vary.
- The `compositor` element requires `gstreamer1.0-plugins-good` to be installed.
