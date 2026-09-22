FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .
RUN python scripts/setup_caption_assets.py

# Exercise this image's Linux FFmpeg before deployment, with synthetic clips only.
# The dummy database URL is local to this command; the tests forbid database access.
RUN DATABASE_URL=postgresql+psycopg://render_smoke:unused@127.0.0.1:1/render_smoke \
    python -m unittest discover -s tests -p test_render_cfr.py -v

EXPOSE 10000
CMD ["sh", "-c", "uvicorn app.api:app --host 0.0.0.0 --port ${PORT:-10000}"]
