"""Image preprocessing for OCR.

Invoices reach this service from phone cameras, gallery uploads and web
uploads, so nothing upstream can be trusted to have normalised them.
"""

import os

from PIL import Image, ImageOps

# iPhones save gallery photos as HEIC, which Pillow cannot open on its
# own -- without this an iPhone upload fails at Image.open(). Registering
# the opener teaches Pillow the format; resize_for_ocr then converts it
# to PNG, since PaddleOCR cannot read HEIC either.
try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIF_SUPPORTED = True
except ImportError:  # pragma: no cover - depends on the install
    HEIF_SUPPORTED = False

DEFAULT_MAX_DIMENSION = 2000

# Formats PaddleOCR will open. Anything else has to be converted first:
# it rejects the file and yields no text at all, which otherwise surfaces
# as an invoice with every field empty rather than as an error.
PADDLE_READABLE = {
    ".bmp", ".dib", ".jpeg", ".jpg", ".png", ".webp",
    ".pbm", ".pgm", ".ppm", ".pnm", ".sr", ".ras", ".tiff", ".tif",
}


def resize_for_ocr(image_path, max_dimension=DEFAULT_MAX_DIMENSION):
    """Normalise an image for OCR and return the path to feed PaddleOCR.

    Three things happen here, all of them driven by what phones actually
    produce:

    1. EXIF orientation is applied. A phone stores the photo in sensor
       orientation plus a rotation tag; PIL's resize drops the tag, so
       without this the OCR sees a sideways or upside-down invoice and
       returns text in scrambled reading order.
    2. Oversized images are shrunk. Accuracy plateaus well below phone
       resolution while processing time keeps scaling with pixel count.
    3. Formats PaddleOCR cannot read (AVIF, HEIC) are converted to PNG.

    A copy is written alongside the original rather than overwriting it,
    so the stored upload stays exactly what the user sent.
    """
    extension = os.path.splitext(image_path)[1].lower()

    with Image.open(image_path) as img:
        width, height = img.size
        longest = max(width, height)
        needs_resize = longest > max_dimension

        if needs_resize:
            # Ask the JPEG decoder for a smaller image up front. Decoding
            # a 4032x3024 photo in full costs ~50MB before any resizing;
            # drafting it down first costs ~13MB, which matters when the
            # container has a hard memory limit. The box must be the real
            # target, not a square -- draft refuses to scale if either
            # side would land under the requested size. No-op for formats
            # that do not support it.
            scale = max_dimension / longest
            img.draft("RGB", (max(1, round(width * scale)), max(1, round(height * scale))))

        img.load()

        # Rotate to upright before anything measures the dimensions --
        # a 90-degree rotation swaps which side is the longest.
        oriented = ImageOps.exif_transpose(img)
        rotated = oriented.size != img.size
        img = oriented

        width, height = img.size
        longest = max(width, height)
        needs_convert = extension not in PADDLE_READABLE
        # An alpha channel has to be flattened even when the image is
        # small enough to need no other work, or a transparent-background
        # PNG reaches OCR as black-on-black.
        needs_flatten = img.mode in ("RGBA", "LA", "P")

        if not (needs_resize or needs_convert or rotated or needs_flatten):
            return image_path

        if longest > max_dimension:
            scale = max_dimension / longest
            img = img.resize(
                (max(1, round(width * scale)), max(1, round(height * scale))),
                Image.LANCZOS,
            )

        base = os.path.splitext(image_path)[0]
        # PNG is lossless and always readable; a converted file must not
        # keep an extension PaddleOCR would refuse.
        out_extension = extension if extension in PADDLE_READABLE else ".png"
        out_path = f"{base}_resized{out_extension}"

        if img.mode in ("RGBA", "LA", "P"):
            # Flatten onto white rather than letting convert("RGB") drop
            # the alpha channel. Transparent pixels are commonly stored
            # as black, so dropping alpha turns a transparent-background
            # screenshot into black text on black, and OCR returns
            # nothing at all -- an empty invoice rather than an error.
            img = img.convert("RGBA")
            flattened = Image.new("RGB", img.size, (255, 255, 255))
            flattened.paste(img, mask=img.split()[-1])
            img = flattened
        elif img.mode != "RGB":
            # CMYK, I;16, L and friends.
            img = img.convert("RGB")

        img.save(out_path)

    return out_path
