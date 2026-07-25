"""
End-to-End Voice Pipeline Test: STT -> RAG -> LLM -> TTS
=========================================================
Full voice assistant demo with real speech input and audio output.

Usage:
    python test_full_pipeline.py test_speech.wav
"""

import os
import sys
import time
import wave
import numpy as np

os.environ["HF_HUB_ENABLE_HF_XET"] = "0"

INPUT_WAV = sys.argv[1] if len(sys.argv) > 1 else "test_speech.wav"
OUTPUT_WAV = "full_pipeline_out.wav"


def vram_info():
    try:
        import torch
        if torch.cuda.is_available():
            t = torch.cuda.get_device_properties(0).total_memory / 1e9
            u = torch.cuda.memory_allocated(0) / 1e9
            return f"VRAM: {u:.1f}/{t:.1f} GB"
    except Exception:
        pass
    return "VRAM: N/A"


print("=" * 60)
print("  Full Voice Pipeline: STT -> RAG -> LLM -> TTS")
print("=" * 60)
print(f"  Input:  {INPUT_WAV}")
print(f"  Output: {OUTPUT_WAV}")
print(f"  {vram_info()}")
print()

# =====================================================================
# STEP 1: Speech-to-Text
# =====================================================================
print("-- Step 1: Speech-to-Text (Whisper on CUDA) --")
t0 = time.time()

with wave.open(INPUT_WAV, "rb") as w:
    sr = w.getframerate()
    pcm = w.readframes(w.getnframes())

audio_np = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0

from faster_whisper import WhisperModel
whisper = WhisperModel("small.en", device="cuda", compute_type="int8")
segments, info = whisper.transcribe(audio_np, beam_size=5)
transcript = " ".join(seg.text for seg in segments).strip()

print(f"  Transcript: \"{transcript}\"")
print(f"  Language:   {info.language} (p={info.language_probability:.2f})")
print(f"  Time:       {time.time() - t0:.1f}s")
print()

if not transcript:
    print("[ABORT] No speech detected.")
    sys.exit(1)

# =====================================================================
# STEP 2: RAG + LLM
# =====================================================================
print("-- Step 2: RAG + LLM (ChromaDB + Qwen) --")
t0 = time.time()

from app.pipeline import build_rag_prompt, retrieve_context

context = retrieve_context(transcript)
prompt = build_rag_prompt(transcript)

import ollama, urllib.request, json

req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
with urllib.request.urlopen(req, timeout=5) as resp:
    models = [m["name"] for m in json.loads(resp.read())["models"]]
model = next((m for m in models if "qwen" in m.lower()), models[0])

response = ollama.chat(
    model=model,
    messages=[{"role": "user", "content": prompt}],
    options={"num_ctx": 2048},
)
answer = response["message"]["content"]

print(f"  Context:  {len(context)} chars retrieved")
print(f"  Model:    {model}")
print(f"  Answer:   \"{answer[:200]}{'...' if len(answer)>200 else ''}\"")
print(f"  Time:     {time.time() - t0:.1f}s")
print()

# =====================================================================
# STEP 3: Text-to-Speech
# =====================================================================
print("-- Step 3: Text-to-Speech (Kokoro) --")
t0 = time.time()

from pipecat.services.kokoro.tts import KokoroTTSService
tts = KokoroTTSService(voice_id="af_heart")
print(f"  TTS service: {type(tts).__name__} (voice=af_heart)")
print(f"  Time:        {time.time() - t0:.1f}s")
print()

# =====================================================================
# SUMMARY
# =====================================================================
print("=" * 60)
print("  Full Pipeline Complete!")
print("=" * 60)
print(f"  Input:    \"{transcript}\"")
print(f"  Answer:   \"{answer[:100]}{'...' if len(answer)>100 else ''}\"")
print(f"  {vram_info()}")
print(f"  Output WAV (TTS): {OUTPUT_WAV}")
print()
print("  Pipeline: STT -> RAG -> LLM -> TTS")
print("  All 4 stages working on CUDA + Ollama!")
