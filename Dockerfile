FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        git \
        libgl1 \
        libsm6 \
        libxext6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /svc

COPY edge/requirements.txt /tmp/edge-requirements.txt
RUN pip install --upgrade pip \
    && pip install --no-cache-dir -r /tmp/edge-requirements.txt

COPY edge /svc/edge
RUN pip install --no-cache-dir -e /svc/edge

WORKDIR /svc/edge

CMD ["python", "main.py"]
