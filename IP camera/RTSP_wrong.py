import cv2, gi, numpy as np
gi.require_version('Gst', '1.0')
from gi.repository import Gst

Gst.init(None)


RTSP_URL = "rtsp://admin:Digital%40123@192.168.96.104:554/live3.sdp"  # network camera

pipeline_in = Gst.Pipeline.new("pipeline")

source = Gst.ElementFactory.make("rtspsrc", None)
source.set_property("location", RTSP_URL)

depay =Gst.ElementFactory.make("rtph264depay", None)
sink = Gst.ElementFactory.make("appsink", None)

pipeline_in.add(source)
pipeline_in.add(depay)
pipeline_in.add(sink)

source.connect("pad-added", self.on_dynamic_pad)
#Gst.Element.link("depay", depay, "parser", parser)
Gst.Element.link("videorate", videorate, "sink", sink)

pipeline_in.set_state(Gst.State.PLAYING)  # missing  "

depay.link(parser)


def sample_image(sink, data):
    sample = sink.emit("pull-sample")
    buffer = sample.get_buffer()
    caps =  sample.get_caps()
    width = caps.get_structure(0).get_value('width')
    height = caps.get_structure(0).get_value('height')
    buffer_size = buffer.get_size()

    image = np.ndarray((height, width, 3),
      buffer = buffer.extract_dup(0, buffer_size), dtype=np.uint8) 
    return Gst.FlowReturn.OK

sink.set_property("emit-signals", True)
sink.connect("new-sample", sample_image, sink)



