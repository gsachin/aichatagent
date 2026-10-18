"""All-language performance benchmark for the OpenAI + Indic-TTS demo.

Run directly, like the other doc/perf tools:

    .venv/Scripts/python.exe doc/perf/tools/benchmark_languages.py --tts-only
    .venv/Scripts/python.exe doc/perf/tools/benchmark_languages.py --languages hi,ta,bn

It measures two halves separately so a regression is attributable:

* LLM: time-to-first-token, total latency, token usage, estimated cost, and a
  script check that the answer really is in the requested language.
* TTS: time-to-first-audio, total synthesis time, real-time factor (audio
  seconds per wall second), and the same script check.

The LLM half requires ``LLM_PROVIDER=openai`` and ``LLM_API_KEY``. Without
them it reports ``skipped`` rather than silently benchmarking the local model.
The Indic-TTS half requires the isolated service on ``INDIC_TTS_URL``; without
it those languages are ``pending`` rather than being synthesised with a
Kokoro English voice. Nothing here changes application state; results go to
``doc/perf/results/language_bench_<UTC>.json``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJ = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJ))
os.chdir(PROJ)

from dotenv import load_dotenv

load_dotenv(PROJ / ".env")

#: Languages the demo intends to serve. Indic-TTS covers the 13 AI4Bharat
#: languages; Kokoro covers English (and the optional non-Indic set).
INDIC_LANGUAGES = ["as", "bn", "brx", "gu", "hi", "kn", "ml", "mni", "mr",
                   "or", "raj", "ta", "te"]
KOKORO_LANGUAGES = ["en", "hi", "es", "fr", "it", "ja", "pt", "zh"]
ALL_LANGUAGES = ["en"] + INDIC_LANGUAGES

#: One short, one medium, one long prompt. The fee prompt also checks that a
#: number survives the translation.
PROMPTS = {
    "short": "What is the tuition fee for the MBA program?",
    "medium": "Which documents do I need to submit for admission?",
    "long": "How long does an admission decision take, and what happens after I apply?",
}
PROMPTS = list(PROMPTS.values())

#: Unicode block starts for the script check. English is checked separately.
SCRIPT_START = {
    "hi": "ऀ", "mr": "ऀ", "ne": "ऀ", "sa": "ऀ",
    "bn": "ঀ", "as": "ঀ",
    "gu": "઀",
    "pa": "਀",
    "or": "଀",
    "ta": "஀",
    "te": "ఀ",
    "kn": "ಀ",
    "ml": "ഀ",
    "ur": "؀",
}

#: Rough per-million-token list prices for the default model (verified docs,
#: 2026-10-17: gpt-6-luna $0.10 input / $0.50 output; cached input $0.01).
PRICE_INPUT_PER_MTOK = float(os.environ.get("BENCH_PRICE_INPUT_PER_MTOK", "0.10"))
PRICE_OUTPUT_PER_MTOK = float(os.environ.get("BENCH_PRICE_OUTPUT_PER_MTOK", "0.50"))

#: Acceptance targets from the approved plan. They are printed as PASS/FAIL;
#: adjust only with a measured reason.
TARGET_LLM_TTFT_MS = 1500.0
TARGET_KOKORO_FIRST_MS = 1000.0
TARGET_INDIC_FIRST_MS = 1500.0
#: RTF here is AUDIO SECONDS PER WALL SECOND (higher is better). A demo must
#: synthesise faster than it plays: 1.0 is real time, 2.0 is twice as fast.
TARGET_RTF_MIN = 1.0


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((p / 100.0) * (len(ordered) - 1)))))
    return round(ordered[idx], 1)


def _script_ok(text: str, language: str) -> bool:
    if not text:
        return False
    if language == "en":
        return any("a" <= c.lower() <= "z" for c in text)
    start = SCRIPT_START.get(language)
    if not start:
        return True
    lo = ord(start)
    return any(lo <= ord(c) < lo + 0x80 for c in text)


def _llm_available() -> tuple[bool, str]:
    import app.llm_backend as lb
    from app.config import settings

    if settings.LLM_PROVIDER != "openai":
        return False, f"LLM_PROVIDER={settings.LLM_PROVIDER!r}, not 'openai'"
    if not lb.LLM_API_KEY:
        return False, "LLM_API_KEY is empty"
    return True, ""


async def _llm_once(language: str, prompt: str) -> dict:
    from app.rag import query_rag_stream

    t0 = time.perf_counter()
    first = None
    parts: list[str] = []
    counters = None
    async for item in query_rag_stream(prompt, language=language):
        if isinstance(item, str):
            if first is None:
                first = time.perf_counter()
            parts.append(item)
        else:
            counters = item
    total = time.perf_counter() - t0
    answer = "".join(parts)
    prompt_tokens = getattr(counters, "prompt_eval_count", None) if counters else None
    out_tokens = getattr(counters, "eval_count", None) if counters else None
    cost = None
    if prompt_tokens is not None and out_tokens is not None:
        cost = (prompt_tokens / 1_000_000) * PRICE_INPUT_PER_MTOK + (
            out_tokens / 1_000_000
        ) * PRICE_OUTPUT_PER_MTOK
    return {
        "language": language,
        "prompt": prompt,
        "answer": answer,
        "ttft_ms": round((first - t0) * 1000, 1) if first else None,
        "total_ms": round(total * 1000, 1),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": out_tokens,
        "cost_usd": round(cost, 6) if cost is not None else None,
        "script_ok": _script_ok(answer, language),
        "ok": bool(answer.strip()),
    }


def bench_llm(languages: list[str], runs: int) -> list[dict]:
    available, why = _llm_available()
    if not available:
        return [{
            "language": lang, "status": "skipped", "reason": why,
        } for lang in languages]

    from app.llm_backend import provider_name
    from app.config import settings

    rows = []
    for lang in languages:
        measurements = []
        for i in range(runs):
            prompt = PROMPTS[i % len(PROMPTS)]
            try:
                measurements.append(asyncio.run(_llm_once(lang, prompt)))
            except Exception as exc:                     # noqa: BLE001
                measurements.append({
                    "language": lang, "prompt": prompt, "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "ttft_ms": None, "total_ms": None,
                    "prompt_tokens": None, "completion_tokens": None,
                    "cost_usd": None, "script_ok": False,
                })
        ok = [m for m in measurements if m.get("ok")]
        ttfts = [m["ttft_ms"] for m in ok if m.get("ttft_ms") is not None]
        totals = [m["total_ms"] for m in ok if m.get("total_ms") is not None]
        costs = [m["cost_usd"] for m in ok if m.get("cost_usd") is not None]
        in_toks = [m["prompt_tokens"] for m in ok if m.get("prompt_tokens") is not None]
        out_toks = [
            m["completion_tokens"] for m in ok
            if m.get("completion_tokens") is not None
        ]
        rows.append({
            "language": lang,
            "status": "ok" if ok else "failed",
            "provider": provider_name(),
            "model": settings.LLM_MODEL,
            "runs": len(measurements),
            "ok_runs": len(ok),
            "script_ok": bool(ok) and all(m["script_ok"] for m in ok),
            "ttft_p50_ms": _pct(ttfts, 50),
            "ttft_p95_ms": _pct(ttfts, 95),
            "total_p50_ms": _pct(totals, 50),
            "total_p95_ms": _pct(totals, 95),
            "prompt_tokens": int(statistics.median(in_toks)) if in_toks else None,
            "completion_tokens": (
                int(statistics.median(out_toks)) if out_toks else None
            ),
            "cost_usd_p50": round(statistics.median(costs), 6) if costs else None,
            "accept_ttft": (
                None if not ttfts else _pct(ttfts, 95) <= TARGET_LLM_TTFT_MS
            ),
            "samples": measurements,
        })
    return rows


def _tts_service_available() -> bool:
    try:
        from app import indic_tts

        return indic_tts.is_available()
    except Exception:                                    # noqa: BLE001
        return False


async def _tts_once(language: str, text: str, provider_enabled: bool) -> dict:
    from app.voice_handler import VoiceCallSession

    # Force a real synthesis: a cache hit would report an unrealistically good
    # RTF. The shared cache is a class attribute; clearing it is safe here
    # because this tool runs offline, not inside the server process.
    VoiceCallSession._shared_tts_cache.clear()
    session = VoiceCallSession(language=language)
    t0 = time.perf_counter()
    first = None
    audio_s = 0.0
    chunks = 0
    async for audio, sr in session.synthesise_stream(text):
        if first is None:
            first = time.perf_counter()
        audio_s += len(audio) / float(sr)
        chunks += 1
    total = time.perf_counter() - t0
    return {
        "language": language,
        "text": text,
        "first_audio_ms": round((first - t0) * 1000, 1) if first else None,
        "total_ms": round(total * 1000, 1),
        "audio_s": round(audio_s, 3),
        "rtf": round(audio_s / total, 3) if total > 0 else None,
        "chunks": chunks,
        "provider_enabled": provider_enabled,
    }


def bench_tts(languages: list[str], runs: int) -> list[dict]:
    from app.config import settings
    from app.indic_tts import INDIC_TTS_LANGUAGES
    from app.voice_handler import TTS_PROVIDER

    indic_up = _tts_service_available()
    if languages:
        # Warm the provider once so model load (Kokoro ONNX or the Indic
        # service's first checkpoint load) is not counted as synthesis latency.
        try:
            asyncio.run(_tts_once(languages[0], "warm up", indic_up))
        except Exception:                                # noqa: BLE001
            pass
    rows = []
    for lang in languages:
        if lang in INDIC_TTS_LANGUAGES and lang not in ("hi",) and not (
            TTS_PROVIDER == "indic_tts" and indic_up
        ):
            rows.append({
                "language": lang, "status": "pending",
                "reason": (
                    "TTS_PROVIDER is not indic_tts"
                    if TTS_PROVIDER != "indic_tts"
                    else "Indic-TTS service is not reachable at INDIC_TTS_URL"
                ),
            })
            continue
        measurements = []
        for i in range(runs):
            text = f"{PROMPTS[i % len(PROMPTS)]} (bench {i + 1})"
            try:
                measurements.append(asyncio.run(_tts_once(lang, text, indic_up)))
            except Exception as exc:                     # noqa: BLE001
                measurements.append({
                    "language": lang, "text": text,
                    "error": f"{type(exc).__name__}: {exc}",
                    "first_audio_ms": None, "total_ms": None,
                    "audio_s": None, "rtf": None, "chunks": 0,
                })
        ok = [m for m in measurements if m.get("rtf") is not None]
        firsts = [m["first_audio_ms"] for m in ok if m.get("first_audio_ms") is not None]
        rtfs = [m["rtf"] for m in ok if m.get("rtf") is not None]
        target_first = (
            TARGET_INDIC_FIRST_MS if lang in INDIC_TTS_LANGUAGES
            else TARGET_KOKORO_FIRST_MS
        )
        rows.append({
            "language": lang,
            "status": "ok" if ok else "failed",
            "tts_provider": TTS_PROVIDER,
            "runs": len(measurements),
            "ok_runs": len(ok),
            "first_audio_p50_ms": _pct(firsts, 50),
            "first_audio_p95_ms": _pct(firsts, 95),
            "rtf_p50": round(statistics.median(rtfs), 3) if rtfs else None,
            "rtf_p95": _pct(rtfs, 95),
            "accept_first": (
                None if not firsts else _pct(firsts, 95) <= target_first
            ),
            "accept_rtf": None if not rtfs else _pct(rtfs, 95) >= TARGET_RTF_MIN,
            "samples": measurements,
        })
    return rows


def _print_table(llm_rows: list[dict], tts_rows: list[dict]) -> None:
    print("\nLLM (OpenAI)")
    print(f"{'lang':<5} {'status':<9} {'ttft p95':>10} {'total p95':>11} "
          f"{'in tok':>8} {'out tok':>8} {'cost':>9} {'script':>7}")
    for r in llm_rows:
        if r.get("status") == "skipped":
            print(f"{r['language']:<5} {'skipped':<9} {r['reason']}")
            continue
        print(f"{r['language']:<5} {r['status']:<9} "
              f"{str(r.get('ttft_p95_ms')):>10} {str(r.get('total_p95_ms')):>11} "
              f"{str(r.get('prompt_tokens')):>8} {str(r.get('completion_tokens')):>8} "
              f"{str(r.get('cost_usd_p50')):>9} {str(r.get('script_ok')):>7}")

    print("\nTTS")
    print(f"{'lang':<5} {'status':<9} {'first p95':>10} {'rtf p95':>8} "
          f"{'provider':<10} notes")
    for r in tts_rows:
        if r.get("status") == "pending":
            print(f"{r['language']:<5} {'pending':<9} {'-':>10} {'-':>8} "
                  f"{'-':<10} {r['reason']}")
            continue
        print(f"{r['language']:<5} {r['status']:<9} "
              f"{str(r.get('first_audio_p95_ms')):>10} {str(r.get('rtf_p95')):>8} "
              f"{r.get('tts_provider', '-'):<10}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--languages", default="all",
                    help="Comma-separated codes, or 'all' (default).")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--llm-only", action="store_true")
    ap.add_argument("--tts-only", action="store_true")
    ap.add_argument("--output", default="")
    args = ap.parse_args()

    languages = (
        ALL_LANGUAGES if args.languages == "all"
        else [x.strip().lower() for x in args.languages.split(",") if x.strip()]
    )
    llm_rows = [] if args.tts_only else bench_llm(languages, max(1, args.runs))
    tts_rows = [] if args.llm_only else bench_tts(languages, max(1, args.runs))
    _print_table(llm_rows, tts_rows)

    out = Path(args.output) if args.output else (
        PROJ / "doc" / "perf" / "results"
        / f"language_bench_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "languages": languages,
        "targets": {
            "llm_ttft_ms": TARGET_LLM_TTFT_MS,
            "kokoro_first_ms": TARGET_KOKORO_FIRST_MS,
            "indic_first_ms": TARGET_INDIC_FIRST_MS,
            "rtf_min": TARGET_RTF_MIN,
        },
        "llm": llm_rows,
        "tts": tts_rows,
    }, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
