FROM python:3.11-slim

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
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]