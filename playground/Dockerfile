# syntax=docker/dockerfile:1.7
#
# Loreto Playground. Build it with `docker compose build` (from playground/): compose.yaml
# passes the repository root as the additional build context `loreto`, from which only
# loreto.py is copied. Without compose:
#
#   docker build -t loreto-playground --build-context loreto=.. .
#
# The build downloads the tiktoken encodings and the Hugging Face tokenisers (each checked
# against its sha256). The running container needs no network.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken \
    HF_HUB_OFFLINE=1 \
    PYTHONPATH=/opt/loreto \
    LORETO_SRC=/opt/loreto \
    LORETO_DEMO_TOKENIZERS_DIR=/opt/tokenizers

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# tiktoken downloads its encodings once, here, and checks their sha256.
RUN python -c "import tiktoken; [tiktoken.get_encoding(e) for e in ('o200k_base', 'cl100k_base')]"

COPY --from=loreto loreto.py /opt/loreto/loreto.py

COPY config/ config/
# Hugging Face tokenisers at a fixed revision; the build fails if a sha256 does not match.
COPY get_tokenizers.py .
RUN python get_tokenizers.py --dest /opt/tokenizers
COPY examples/ examples/
COPY app/ app/

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin loreto
USER loreto

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

# Worker processes: WEB_CONCURRENCY (default 1). Each one loads every tokeniser.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
