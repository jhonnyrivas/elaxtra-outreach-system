FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential curl \
 && rm -rf /var/lib/apt/lists/*

FROM base AS builder

COPY pyproject.toml ./
RUN pip install --prefix=/install "pip>=24.2" setuptools wheel \
 && pip install --prefix=/install .

FROM base AS runtime

COPY --from=builder /install /usr/local
COPY . .

RUN useradd --create-home --uid 1000 app \
 && chown -R app:app /app
USER app

# Railway injects $PORT at runtime; locally we default to 8000 via WEBHOOK_PORT.
EXPOSE 8000

CMD ["python", "-m", "src.main", "serve"]
