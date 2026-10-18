# Isolated AI4Bharat Indic-TTS service.
#
# The application venv is Python 3.11; Indic-TTS's Coqui TTS fork is from the
# Python 3.8 era. Keeping it in this image means a failed build/migration cannot
# take the voice app down.
FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    INDIC_TTS_CHECKPOINTS=/checkpoints \
    INDIC_TTS_CUDA=0

RUN apt-get update && apt-get install -y --no-install-recommends \
        git build-essential libsndfile1 ffmpeg espeak-ng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# AI4Bharat's inference instructions use this Coqui fork.
RUN git clone --depth 1 https://github.com/gokulkarthik/TTS /opt/TTS \
    && pip install --no-cache-dir -e /opt/TTS

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app.py /app/app.py

EXPOSE 8020
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8020"]
