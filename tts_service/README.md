# Indic-TTS service (AI4Bharat)

The app's `app/indic_tts.py` client expects this service at
`INDIC_TTS_URL` (default `http://127.0.0.1:8020`). It is intentionally outside
the main Python 3.11 venv: Indic-TTS's Coqui TTS fork is a 2023-era stack and
is likely to conflict with the application's torch/transformers versions.

**Qwen3-TTS is not an option for Indian languages.** Its 10 languages are
Chinese, English, Japanese, Korean, German, French, Russian, Portuguese,
Spanish and Italian — no Hindi, Tamil, Telugu, Bengali, etc.

## Checkpoints

One zip per language, ~1.5 GB each, from the
[`v1-checkpoints-release`](https://github.com/AI4Bharat/Indic-TTS/releases/tag/v1-checkpoints-release):

| Code | Language | Code | Language |
|---|---|---|---|
| `as` | Assamese | `ml` | Malayalam |
| `bn` | Bengali | `mni` | Manipuri |
| `brx` | Bodo | `mr` | Marathi |
| `gu` | Gujarati | `or` | Odia |
| `hi` | Hindi | `raj` | Rajasthani |
| `kn` | Kannada | `ta` | Tamil |
| | | `te` | Telugu |

Download and extract one language (PowerShell):

```powershell
.\tts_service\download_checkpoints.ps1 -Language hi
```

The layout the service expects:

```
tts_service/checkpoints/hi/
  config.json
  fastpitch/best_model.pth
  hifigan/best_model.pth
  hifigan/config.json
```

If a zip extracts to a different shape, move the files into that layout.

## Build and run

CPU (works, slower):

```powershell
docker build -t indic-tts .\tts_service
docker run --rm -p 8020:8020 -v "${PWD}\tts_service\checkpoints:/checkpoints" indic-tts
```

GPU (requires NVIDIA Container Toolkit; FastPitch/HiFiGAN are non-autoregressive
and fast, so a small GPU is enough):

```powershell
docker run --rm --gpus all -e INDIC_TTS_CUDA=1 -p 8020:8020 `
  -v "${PWD}\tts_service\checkpoints:/checkpoints" indic-tts
```

Health and a smoke test:

```powershell
curl.exe http://127.0.0.1:8020/health
curl.exe -X POST http://127.0.0.1:8020/synthesize `
  -H "Content-Type: application/json" `
  -d '{"text":"Meridian University mein aapka swagat hai.","language":"hi"}' `
  --output hi.wav
```

## Enable in the app

```dotenv
TTS_PROVIDER=indic_tts
INDIC_TTS_URL=http://127.0.0.1:8020
INDIC_TTS_LANGUAGE=hi
```

Restart the app. English still uses Kokoro; Hindi falls back to Kokoro
`hf_alpha` if the service is down. Other Indic languages return the fixed
response rather than being spoken with an English voice.

## Benchmark

```powershell
.venv\Scripts\python.exe doc\perf\tools\benchmark_languages.py `
  --tts-only --languages hi,ta,bn,mr --runs 3
```

The tool reports `pending` until `TTS_PROVIDER=indic_tts` **and** `/health`
answers, so it never fabricates a result for a language that was not measured.

## If the build fails

The fallback order is:

1. Kokoro Hindi (`hf_alpha`) for `hi` — already installed, streaming.
2. [IndicParler-TTS](https://huggingface.co/ai4bharat/indic-parler-tts) —
   modern `transformers` model, 19 languages, gated; easier on Python 3.11 but
   non-streaming and GPU-recommended.
3. Text-only local-language output on WhatsApp/web.
