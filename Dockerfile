# Python 3.12: paddlepaddle publishes no wheels for 3.14+.
FROM python:3.12-slim

# libgomp1 is required by paddlepaddle; libgl1 and libglib2.0-0 by the
# OpenCV build that paddleocr pulls in. Without them the import fails at
# runtime rather than at build time.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# A container sees the host's CPU count, not its own quota, so OpenMP
# spawns a thread per host core -- on a big shared host that is dozens of
# threads, each with its own allocation arena, and resident memory
# balloons until the platform OOM-kills the process. That surfaces as
# "Killed" in the logs and a 502 to the caller, not as a Python error.
# Inference here is serialised anyway, so extra threads buy nothing.
ENV OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    # Cap glibc's per-thread malloc arenas, which otherwise fragment
    # badly in long-running threaded workloads.
    MALLOC_ARENA_MAX=2

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Which model set to bake in. It has to match the OCR_MODEL_SIZE the
# container runs with: get_ocr() names the models it wants, so a
# mismatch means the runtime asks for weights the image does not have
# and downloads them on the first request anyway. Setting it as ENV, not
# just ARG, is what keeps the two in step by default.
ARG OCR_MODEL_SIZE=tiny
ENV OCR_MODEL_SIZE=${OCR_MODEL_SIZE}

# Bake the detection and recognition models into the image. They would
# otherwise download on the first request, making it look like a hang.
# Building them in also means the container starts working without
# outbound network access.
#
# This goes through the app's own get_ocr() rather than constructing a
# PaddleOCR here, so the baked weights cannot drift from the ones the
# service actually loads.
RUN python -c "from paddleOcr import get_ocr; get_ocr()"

EXPOSE 8000

# One worker on purpose: each worker loads its own copy of the models
# (~1GB of RAM) and inference is serialised behind a lock anyway. Scale
# this only after measuring, alongside the job queue.
# Shell form on purpose, so $PORT expands at runtime. Railway, Render,
# Cloud Run and Fly all inject the port the app must listen on, and fail
# their health check if it binds a hardcoded one. Falling back to 8000
# keeps docker-compose working unchanged.
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --timeout-keep-alive 600
