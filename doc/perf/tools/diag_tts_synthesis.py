"""Diagnose Kokoro TTS synthesis cost.

Context: the N=2 measurement shows `llm_done -> tts_done` at 12-16 s under
concurrency and ~6 s single-session. 6 s to synthesise ~8 s of audio is ~0.75x
real-time, which is slow for Kokoro on a GPU with NO competition. This script
measures the uncontended floor and inspects the ONNX session the app builds.

Real-time factor (RTF) = synthesis_seconds / audio_seconds. Lower is better;
RTF << 1 means faster than real-time.

Read-only: loads the TTS engine, synthesises, exits. Changes nothing.
Run:  .venv/Scripts/python.exe doc/perf/tools/diag_tts_synthesis.py
"""
import os
import sys
import time

PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
sys.path.insert(0, PROJ)
os.chdir(PROJ)

CACHE = os.path.join(os.path.expanduser("~"), ".cache", "pipecat", "kokoro-onnx")
ONNX_PATH = os.path.join(CACHE, "kokoro-v1.0.onnx")
VOICES = os.path.join(CACHE, "voices-v1.0.bin")

TEXTS = [
    ("short (a greeting)", "Hi, I'm the admissions assistant."),
    ("typical answer", "The Bachelor of Technology in Computer Science is a four-year "
                       "programme, and annual tuition is 185,000 rupees."),
    ("long answer", "Meridian University offers undergraduate and postgraduate programmes "
                    "across engineering, business and health sciences. Applications open on "
                    "1 March and close on 30 June. Merit scholarships cover up to 40 percent "
                    "of tuition for candidates scoring above 90 percent, and hostel "
                    "accommodation costs 72,000 rupees per year including meals."),
]

print("=" * 88)
print("KOKORO TTS SYNTHESIS DIAGNOSTIC")
print("=" * 88)

import onnxruntime as ort                                         # noqa: E402
print(f"\nonnxruntime {ort.__version__}")
print(f"available providers: {ort.get_available_providers()}")

# --- what the APP builds (no SessionOptions, provider from ONNX_PROVIDER) ----
os.environ.setdefault("ONNX_PROVIDER", "CUDAExecutionProvider")
from kokoro_onnx import Kokoro                                    # noqa: E402

t0 = time.time()
kokoro = Kokoro(ONNX_PATH, VOICES)
load_s = time.time() - t0
print(f"\nengine constructed in {load_s:.2f} s")
try:
    sess = kokoro.sess
    print(f"  session providers      : {sess.get_providers()}")
    so = sess.get_session_options()
    print(f"  graph_optimization     : {so.graph_optimization_level}")
    print(f"  intra_op_num_threads   : {so.intra_op_num_threads}")
    print(f"  execution_mode         : {so.execution_mode}")
    print(f"  inputs                 : {[i.name for i in sess.get_inputs()]}")
except Exception as exc:
    print(f"  (session introspection failed: {exc!r})")

# --- real-time factor, uncontended, warm -----------------------------------
# Discard the first call: it pays lazy CUDA kernel/module init.
kokoro.create("warm up", voice="af_heart", speed=1.0)

print(f"\n{'case':<20}{'audio s':>9}{'synth s':>10}{'RTF':>8}   verdict")
print("-" * 88)
rtfs = []
for label, text in TEXTS:
    # 3 runs, take the best: we want the floor, not a scheduling artifact.
    best = None
    audio_s = 0.0
    for _ in range(3):
        t0 = time.time()
        audio, sr = kokoro.create(text, voice="af_heart", speed=1.0)
        dt = time.time() - t0
        audio_s = len(audio) / sr
        best = dt if best is None else min(best, dt)
    rtf = best / audio_s if audio_s else 0
    rtfs.append(rtf)
    verdict = ("faster than real-time" if rtf < 1 else
               "SLOWER than real-time" if rtf > 1.3 else "borderline")
    print(f"{label:<20}{audio_s:>9.2f}{best:>10.2f}{rtf:>8.2f}   {verdict}")

print(f"\nbest RTF across cases: {min(rtfs):.2f}")
print("\nInterpretation: on a GPU, Kokoro is normally well under 1.0 RTF. An RTF")
print("near or above 1.0 with NO competition points at the session configuration")
print("or the phonemiser, not at GPU contention.")
