ARG PYTHON_VERSION=3.10
FROM python:${PYTHON_VERSION}-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_SYSTEM_PYTHON=1

WORKDIR /svc/edge

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        git \
        libgl1 \
        libsm6 \
        libxext6 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip uv

COPY pyproject.toml README.md /svc/edge/
COPY main.py /svc/edge/main.py
COPY src /svc/edge/src
COPY trackers /svc/edge/trackers

RUN uv pip install --system --no-cache -e ".[vision]"

RUN python -c "import edge; print(edge.__name__)"

CMD ["python", "main.py"]
