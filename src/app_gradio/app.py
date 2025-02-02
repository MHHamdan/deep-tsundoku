import os
import re
from pathlib import Path
from typing import Callable, List

import gradio as gr
import torch
from PIL.Image import Image
from gradio import CSVLogger
from transformers import DonutProcessor, VisionEncoderDecoderModel

from src.models.image_segmentation import crop_book_spines_in_image


def main():
    model_inference = BookSpineReader()
    frontend = make_frontend(model_inference.predict)
    frontend.launch()


def make_frontend(fn: Callable[[Image], str]):
    """Creates a gradio.Interface frontend for an image to text function"""
    # List of example images
    images_dir = os.path.join(get_project_root(), "data/images")
    example_images = [
        os.path.join(images_dir, f)
        for f in os.listdir(images_dir)
        if os.path.splitext(f)[1] in [".jpg", ".jpeg", ".png"]
    ]

    with gr.Blocks() as frontend:
        # TODO: Add example images
        # TODO: Change layout https://gradio.app/controlling_layout/
        image = gr.Image(type="pil", label="Bookshelf")
        run_button = gr.Button("Find books")
        # gr.Examples(examples=example_images, inputs=[gr.Image()])
        output = gr.Textbox(label="Recognized books")
        wrong_prediction_button = gr.Button("Flag wrong prediction 🐞")
        user_feedback = gr.Textbox(interactive=True, label="User feedback")

        # Log user feedback
        flag_button = gr.Button("Correct predictions")
        flagging_callback = CSVLogger()
        flag_components = [image, output, user_feedback]
        flagging_callback.setup(flag_components, "user_feedback")
        flag_method = FlagMethod(flagging_callback)
        flag_button.click(
            flag_method,
            inputs=flag_components,
            outputs=[],
            preprocess=False,
            queue=False,
        )

        # Functionality of buttons
        run_button.click(fn, inputs=image, outputs=output)
        wrong_prediction_button.click(
            lambda model_output: model_output, inputs=output, outputs=user_feedback
        )
    return frontend


class FlagMethod:
    """Copied from gradio's `interface.py` script that mimics the flagging callback"""

    def __init__(self, flagging_callback, flag_option=None):
        self.flagging_callback = flagging_callback
        self.flag_option = flag_option
        self.__name__ = "Flag"

    def __call__(self, *flag_data):
        self.flagging_callback.flag(flag_data, flag_option=self.flag_option)


class BookSpineReader:
    """
    Crops the image into the book spines and runs each image through an ML model
    that identifies the text in the image.
    """

    def __init__(self):
        self.image_reader_model = ImageReader()

    def predict(self, image) -> str:
        # Identify book spines in images
        book_spines = crop_book_spines_in_image(image, output_img_type="pil")
        # Read text in each book spine
        output = [self.image_reader_model.predict(img) for img in book_spines]
        return self._post_process_output(output)

    @staticmethod
    def _post_process_output(model_output: List[str]) -> str:
        model_output_clean = [s for s in model_output if len(s) > 0]
        if len(model_output_clean) == 0:
            return "No book found in the image 😞 Make sure the books are stacked vertically"
        else:
            return "\n".join(model_output_clean)


class ImageReader:
    """
    Runs a Machine Learning model that reads the text in an image
    """
    def __init__(self):
        """Initializes processing and inference models."""
        self.processor = DonutProcessor.from_pretrained("jay-jojo-cheng/donut-cover")
        self.model = VisionEncoderDecoderModel.from_pretrained(
            "jay-jojo-cheng/donut-cover"
        )
        
    def predict(self, image) -> str:
        pixel_values = self.processor(image, return_tensors="pt").pixel_values

        task_prompt = "<s_cord-v2>"
        decoder_input_ids = self.processor.tokenizer(
            task_prompt, add_special_tokens=False, return_tensors="pt"
        )["input_ids"]

        device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model.to(device)

        outputs = self.model.generate(
            pixel_values.to(device),
            decoder_input_ids=decoder_input_ids.to(device),
            max_length=self.model.decoder.config.max_position_embeddings,
            early_stopping=True,
            pad_token_id=self.processor.tokenizer.pad_token_id,
            eos_token_id=self.processor.tokenizer.eos_token_id,
            use_cache=True,
            num_beams=1,
            bad_words_ids=[[self.processor.tokenizer.unk_token_id]],
            return_dict_in_generate=True,
            output_scores=True,
        )
        return self._post_process_output(outputs)

    def _post_process_output(self, outputs) -> str:
        sequence = self.processor.batch_decode(outputs.sequences)[0]
        sequence = sequence.replace(self.processor.tokenizer.eos_token, "").replace(
            self.processor.tokenizer.pad_token, ""
        )
        sequence = re.sub(
            r"<.*?>", "", sequence, count=1
        ).strip()  # remove first task start token
        print(f"Prediction: {sequence}")
        return sequence


def get_project_root():
    """Returns the path to the project's root directory: deep-tsundoku"""
    return Path(__file__).parent.parent.parent


if __name__ == "__main__":
    main()
