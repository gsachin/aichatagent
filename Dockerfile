# Multi-stage Dockerfile for University Admissions Voice AI Assistant
# Supports both FastAPI and Streamlit services

FROM python:3.11-slim as base

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    libssl-dev \
    libffi-dev \
    python3-dev \
    git \
    curl \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# ──────────────────────────────────────────────────────────────────
# Builder stage - Install Python dependencies
# ──────────────────────────────────────────────────────────────────
FROM base as builder

COPY requirements.txt .

# Install Python dependencies
RUN pip install --user --no-warn-script-location -r requirements.txt

# ──────────────────────────────────────────────────────────────────
# Final stage
# ──────────────────────────────────────────────────────────────────
FROM base as final

# Copy Python dependencies from builder
COPY --from=builder /root/.local /root/.local

# Add local pip installation to PATH
ENV PATH=/root/.local/bin:$PATH

# Copy application code
COPY . /app

# Create necessary directories
RUN mkdir -p /app/logs /app/chroma_local_db /app/models

# Verify installation
RUN python -c "import torch; import streamlit; import fastapi; print('✓ All dependencies installed')"

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || curl -f http://localhost:8501 || exit 1

# Default command (can be overridden)
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
