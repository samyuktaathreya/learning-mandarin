FROM python:3.11-slim

WORKDIR /workspace

# Copy build configuration for the root package
COPY pyproject.toml setup.py* ./

# Copy the application source code and its required data
COPY app/ ./app/
COPY data/ ./data/

# Install the root package in editable mode and application dependencies
RUN pip install --no-cache-dir -e . && \
    pip install --no-cache-dir -r app/requirements.txt

# Set execution directory to app and launch Uvicorn
WORKDIR /workspace/app
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]