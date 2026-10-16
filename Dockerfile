FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home appuser
ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv $VIRTUAL_ENV && chown -R appuser:appuser $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
ENV PRONUNCIATION_THREADS=2 MALLOC_ARENA_MAX=2

WORKDIR /workspace
RUN chown appuser:appuser /workspace
USER appuser

# 1. Dependencies only: this layer is reused until requirements.txt changes
COPY --chown=appuser:appuser app/requirements.txt ./app/requirements.txt
RUN pip install --no-cache-dir -r app/requirements.txt

# 2. Source code: changes here no longer touch the layer above
COPY --chown=appuser:appuser pyproject.toml setup.py* alembic.ini ./
COPY --chown=appuser:appuser app/ ./app/
COPY --chown=appuser:appuser data/ ./data/
COPY --chown=appuser:appuser migrations/ ./migrations/

# 3. Register the package itself (fast, since the deps are already installed)
RUN pip install --no-cache-dir -e .

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]