import logging
import gradio as gr

log_format = "%(asctime)s::%(levelname)s::%(name)s::"\
             "%(filename)s::%(lineno)d::%(message)s"
logging.basicConfig(level='INFO', format=log_format)
logger = logging.getLogger(__name__)


def load():
    return [
        ("Here's an audio", gr.Audio("https://github.com/gradio-app/gradio/raw/main/test/test_files/audio_sample.wav")),
        ("Here's an video", gr.Video("https://github.com/gradio-app/gradio/raw/main/demo/video_component/files/world.mp4"))
    ]

with gr.Blocks() as demo:
    chatbot = gr.Chatbot()
    button = gr.Button("Load audio and video")
    button.click(load, None, chatbot)


if __name__ == '__main__':
    logger.info("starting agent")
    demo.launch()
