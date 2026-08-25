"""Image preprocessing for OCR.

Invoices reach this service from phone cameras, gallery uploads and web
uploads, so nothing upstream can be trusted to have normalised them.
"""

import os

from PIL import Image

DEFAULT_MAX_DIMENSION = 2000


def resize_for_ocr(image_path, max_dimension=DEFAULT_MAX_DIMENSION):
    """Shrink an image so its longest side is at most ``max_dimension``.

    OCR accuracy plateaus well below typical phone-camera resolution while
    processing time keeps scaling with pixel count, so this is close to a
    free speed win.

    Writes a ``_resized`` copy alongside the original rather than
    overwriting it, and returns the path that should be fed to OCR. Images
    already within the limit are returned untouched.
    """
    with Image.open(image_path) as img:
        img.load()
        width, height = img.size
        longest = max(width, height)
        if longest <= max_dimension:
            return image_path

        scale = max_dimension / longest
        new_size = (max(1, round(width * scale)), max(1, round(height * scale)))
        resized = img.resize(new_size, Image.LANCZOS)

        base, ext = os.path.splitext(image_path)
        out_path = f"{base}_resized{ext}"
        if resized.mode not in ("RGB", "L"):
            resized = resized.convert("RGB")
        resized.save(out_path)

    return out_path