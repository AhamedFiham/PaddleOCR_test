"""Single-image OCR entry point, plus the shared PaddleOCR engine.

The engine is expensive to build (model load + graph setup), so it is
created exactly once per process and reused. Never construct PaddleOCR
inside a per-request function -- reloading the model per request is the
single largest avoidable latency cost in this system.
"""

import os
import sys
import threading

from paddleocr import PaddleOCR

from resize_utils import PADDLE_READABLE

# Detections below this score are smudges, logo fragments and scan noise.
MIN_CONFIDENCE = 0.5

# oneDNN is off by default because it breaks text detection on Windows
# (see get_ocr below). It generally speeds inference up on Linux, so it is
# worth trying there: set PADDLE_ENABLE_MKLDNN=1 and confirm OCR still
# returns text before leaving it on.
ENABLE_MKLDNN = os.getenv("PADDLE_ENABLE_MKLDNN", "0") == "1"

# Model size: tiny | small | medium. Measured on the sample invoices,
# one image, CPU:
#     medium  ~38s   small  ~10s   tiny  ~3.5s
# Tiny produced identical totals to medium on all ten sample invoices and
# the same date on nine, so the 10x speed-up costs nothing measurable on
# this kind of document. Someone photographing a receipt will not wait
# 40 seconds, which is what makes tiny the right default here; raise it
# if a harder document set proves it is not enough.
OCR_MODEL_SIZE = os.getenv("OCR_MODEL_SIZE", "tiny")

# Fraction of the shorter box height that two boxes must share vertically
# before they are treated as sitting on the same visual line.
LINE_OVERLAP_RATIO = 0.5

_OCR = None
_OCR_LOCK = threading.Lock()

# One shared engine plus FastAPI's threadpool means two requests can reach
# predict() at once, which the pipeline is not built for. Serialising
# inference keeps results correct; throughput is a later-phase concern.
_PREDICT_LOCK = threading.Lock()


def predict(path):
    """Run the shared engine over a file, one caller at a time."""
    with _PREDICT_LOCK:
        return get_ocr().predict(path)


def get_ocr():
    """Return the process-wide PaddleOCR instance, building it on first use.

    ``enable_mkldnn=False`` is required, not a tuning choice: with oneDNN
    enabled, paddlepaddle 3.3.1 aborts text detection on this platform with
    "ConvertPirAttribute2RuntimeAttribute not support
    [pir::ArrayAttribute<pir::DoubleAttribute>]".

    The document orientation / unwarping / textline-orientation submodels
    are off because the invoices here are upright digital PDFs and web
    uploads; turn them back on if genuinely rotated camera shots appear.
    """
    global _OCR
    if _OCR is None:
        with _OCR_LOCK:
            if _OCR is None:
                # Naming the models explicitly makes `lang` inapplicable,
                # so it is not passed; these are the English-capable
                # multilingual weights.
                _OCR = PaddleOCR(
                    text_detection_model_name=f"PP-OCRv6_{OCR_MODEL_SIZE}_det",
                    text_recognition_model_name=f"PP-OCRv6_{OCR_MODEL_SIZE}_rec",
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=False,
                    enable_mkldnn=ENABLE_MKLDNN,
                )
    return _OCR


def _box_bounds(poly):
    """Return (x_left, y_top, y_bottom) for a 4-point detection polygon."""
    xs = [float(point[0]) for point in poly]
    ys = [float(point[1]) for point in poly]
    return min(xs), min(ys), max(ys)


def order_lines(texts, scores, polys):
    """Reconstruct natural reading order from raw detection order.

    Raw PaddleOCR output interleaves columns -- a date value can arrive
    before its own "Date:" label -- which breaks any downstream extraction
    that reads a value from the line after its anchor. Boxes are grouped
    into visual lines by vertical overlap, sorted left-to-right within each
    line, and the lines sorted top-to-bottom.
    """
    entries = []
    for text, score, poly in zip(texts, scores, polys):
        text = (text or "").strip()
        if not text or score < MIN_CONFIDENCE:
            continue
        x_left, y_top, y_bottom = _box_bounds(poly)
        entries.append(
            {
                "text": text,
                "confidence": float(score),
                "x": x_left,
                "top": y_top,
                "bottom": y_bottom,
            }
        )

    entries.sort(key=lambda e: (e["top"] + e["bottom"]) / 2)

    lines = []
    for entry in entries:
        placed = False
        if lines:
            line = lines[-1]
            overlap = min(line["bottom"], entry["bottom"]) - max(
                line["top"], entry["top"]
            )
            shorter = min(line["bottom"] - line["top"], entry["bottom"] - entry["top"])
            if shorter > 0 and overlap / shorter >= LINE_OVERLAP_RATIO:
                line["items"].append(entry)
                line["top"] = min(line["top"], entry["top"])
                line["bottom"] = max(line["bottom"], entry["bottom"])
                placed = True
        if not placed:
            lines.append(
                {"top": entry["top"], "bottom": entry["bottom"], "items": [entry]}
            )

    ordered = []
    for line in lines:
        for entry in sorted(line["items"], key=lambda e: e["x"]):
            ordered.append({"text": entry["text"], "confidence": entry["confidence"]})
    return ordered


def result_to_lines(result):
    """Convert one PaddleOCR page result into ordered text/confidence dicts."""
    texts = result.get("rec_texts") or []
    scores = result.get("rec_scores") or []
    polys = result.get("rec_polys")
    if polys is None or len(polys) == 0:
        polys = result.get("dt_polys") or []
    return order_lines(texts, scores, polys)


def run_ocr_on_image(path):
    """Run OCR on a single image file.

    Returns a list of ``{"text": str, "confidence": float}`` in reading
    order. PDFs are rejected here -- they need per-page handling, which
    lives in pdfOcr.run_ocr_on_pdf.
    """
    if path.lower().endswith(".pdf"):
        raise ValueError(
            f"{path} is a PDF. Use pdfOcr.run_ocr_on_pdf() for PDFs -- "
            "this function handles single images only."
        )
    if not os.path.exists(path):
        raise FileNotFoundError(path)

    # PaddleOCR prints a complaint and returns nothing for a format it
    # cannot open, which would otherwise reach the caller as an invoice
    # where every field happens to be empty. resize_for_ocr converts
    # these, so arriving here with one means it was bypassed.
    extension = os.path.splitext(path)[1].lower()
    if extension not in PADDLE_READABLE:
        raise ValueError(
            f"PaddleOCR cannot read '{extension}' files. "
            "Pass the image through resize_for_ocr() first, which converts it."
        )

    lines = []
    for result in predict(path):
        lines.extend(result_to_lines(result))
    return lines


def main():
    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        path = input("Image file to OCR: ").strip().strip('"')

    for line in run_ocr_on_image(path):
        print(f"{line['confidence']:.3f}  {line['text']}")


if __name__ == "__main__":
    main()
