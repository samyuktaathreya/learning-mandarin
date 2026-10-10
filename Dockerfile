FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user and set up virtual environment paths
RUN useradd --create-home appuser
ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /workspace

# Copy build configuration for the root package
COPY pyproject.toml setup.py* ./

# Copy application source code and data
COPY app/ ./app/
COPY data/ ./data/

# alembic business
COPY alembic.ini ./
COPY migrations/ ./migrations/

# Install dependencies inside the virtual environment
RUN pip install --no-cache-dir -e . && \
    pip install --no-cache-dir -r app/requirements.txt

# Switch ownership and drop root privileges
RUN chown -R appuser:appuser /workspace $VIRTUAL_ENV
USER appuser

# Launch Uvicorn from the project root
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]