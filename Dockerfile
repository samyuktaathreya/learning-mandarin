FROM python:3.11-slim

# ffmpeg decodes browser recordings; curl + bzip2 fetch and unpack the speech model
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    curl \
    bzip2 \
    && rm -rf /var/lib/apt/lists/*

# Download the speech model (as root, before app code, so this layer is cached)
ARG PHONEME_MODEL=sherpa-onnx-streaming-zipformer-small-ctc-zh-int8-2025-04-01
RUN mkdir -p /opt/models \
    && curl -fL -o /tmp/model.tar.bz2 \
       https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/${PHONEME_MODEL}.tar.bz2 \
    && tar xjf /tmp/model.tar.bz2 -C /opt/models --no-same-owner \
    && rm /tmp/model.tar.bz2 \
    && chmod -R a+rX /opt/models
ENV PHONEME_MODEL_DIR=/opt/models/${PHONEME_MODEL}

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

# Install dependencies inside the virtual environment
RUN pip install --no-cache-dir -e . && \
    pip install --no-cache-dir -r app/requirements.txt

# Switch ownership and drop root privileges
RUN chown -R appuser:appuser /workspace $VIRTUAL_ENV
USER appuser

# Set execution directory to app and launch Uvicorn
WORKDIR /workspace/app
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]