"""Multi-page PDF OCR entry point.

PaddleOCR reads PDFs natively via pypdfium2 -- each page is rendered
internally, so there is no separate PDF-to-image conversion step. The
image-side speed lever (resize_utils.resize_for_ocr) therefore does not
apply here; the equivalent knob for PDFs is render DPI/scale, which lives
inside the pipeline.
"""

import os
import sys

from paddleOcr import predict, result_to_lines


def run_ocr_on_pdf(path):
    """Run OCR across every page of a PDF.

    Returns a single flat list of ``{"text": str, "confidence": float}``
    in reading order, pages concatenated front to back. Reading order is
    reconstructed per page and low-confidence detections dropped, both in
    paddleOcr.result_to_lines.
    """
    if not path.lower().endswith(".pdf"):
        raise ValueError(
            f"{path} is not a PDF. Use paddleOcr.run_ocr_on_image() for images."
        )
    if not os.path.exists(path):
        raise FileNotFoundError(path)

    lines = []
    for result in predict(path):
        lines.extend(result_to_lines(result))
    return lines


def main():
    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        path = input("PDF file to OCR: ").strip().strip('"')

    for line in run_ocr_on_pdf(path):
        print(f"{line['confidence']:.3f}  {line['text']}")


if __name__ == "__main__":
    main()