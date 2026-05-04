import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

Gst.init(None)

RTSP_URL_MAIN   = "rtsp://admin:Digital%40123@192.168.96.104:554/live3.sdp"
RTSP_URL_BULLET = "rtsp://192.168.96.31:554/rtsp/streaming?channel=01&subtype=A"

CAM_W  = 640   # width of each camera feed
CAM_H  = 480   # height of each camera feed
FULL_W = CAM_W * 2  # total composite window width = 1280

# compositor tiles both streams side by side in one window
# textoverlay adds camera labels
pipeline_str = (
    f"compositor name=comp "
    f"sink_0::xpos=0      sink_0::ypos=0 "
    f"sink_1::xpos={CAM_W} sink_1::ypos=0 ! "
    f"video/x-raw,width={FULL_W},height={CAM_H} ! "
    f"autovideosink sync=false "

    f"rtspsrc location={RTSP_URL_MAIN} latency=200 protocols=udp+tcp ! "
    f"decodebin ! videoconvert ! videoscale ! "
    f"video/x-raw,width={CAM_W},height={CAM_H} ! "
    f"textoverlay text=\"CAM 1 - MAIN\" valignment=top halignment=left "
    f"font-desc=\"Sans Bold 14\" shaded-background=true ! "
    f"comp.sink_0 "

    f"rtspsrc location={RTSP_URL_BULLET} latency=200 protocols=tcp ! "
    f"decodebin ! videoconvert ! videoscale ! "
    f"video/x-raw,width={CAM_W},height={CAM_H} ! "
    f"textoverlay text=\"CAM 2 - IR BULLET\" valignment=top halignment=left "
    f"font-desc=\"Sans Bold 14\" shaded-background=true ! "
    f"comp.sink_1"
)

pipeline = Gst.parse_launch(pipeline_str)
loop = GLib.MainLoop()

def on_bus_message(bus, message):
    t = message.type
    if t == Gst.MessageType.ERROR:
        err, debug = message.parse_error()
        if "Output window was closed" in str(err):
            print("[INFO] Window closed.")
        else:
            print(f"[ERROR] {err}")
            print(f"[DEBUG] {debug}")
        loop.quit()
    elif t == Gst.MessageType.EOS:
        print("[EOS] Stream ended.")
        loop.quit()
    elif t == Gst.MessageType.STATE_CHANGED:
        if message.src == pipeline:
            old, new, _ = message.parse_state_changed()
            print(f"[STATE] {old.value_nick} -> {new.value_nick}")

bus = pipeline.get_bus()
bus.add_signal_watch()
bus.connect("message", on_bus_message)

pipeline.set_state(Gst.State.PLAYING)
print(f"Composite view started ({FULL_W}x{CAM_H}) — both cameras in one window.")
print("Close the window or press Ctrl+C to quit.")

try:
    loop.run()
except KeyboardInterrupt:
    pass
finally:
    pipeline.set_state(Gst.State.NULL)
