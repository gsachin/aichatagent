"""Round-trip smoke test for the six foreign languages.

You do not need to speak Spanish, French, Italian, Japanese, Portuguese or
Chinese to use this. It checks each language on two independent axes:

TEXT
  Ask the live RAG pipeline one English question with ``language=<code>``.
  Confirm the answer is in the target script, ask the LLM to identify the
  language and translate it back to English, and compare the numbers. The
  back-translation is printed so you can read what a caller would have heard.

AUDIO
  Synthesize a known sentence with the SAME Kokoro voice and phoneme map the
  call path uses, resample it through the telephone rates (24 kHz -> 8 kHz ->
  16 kHz), then transcribe it with the SAME faster-whisper model the call path
  uses. If the voice were reading Spanish with English phonemes, Whisper would
  detect English or return garbage. The same check runs on the pre-recorded
  ``fixed_response_<code>.wav`` asset.

Every generated WAV is written next to the JSON report so a native speaker can
review the actual audio. This script never writes to the database, never calls
Twilio, and never changes application settings.

Examples::

    .venv/Scripts/python.exe scripts/test_foreign_languages.py
    .venv/Scripts/python.exe scripts/test_foreign_languages.py --languages es,ja
    .venv/Scripts/python.exe scripts/test_foreign_languages.py --no-llm
    .venv/Scripts/python.exe scripts/test_foreign_languages.py --no-audio
    .venv/Scripts/python.exe scripts/test_foreign_languages.py --out doc/perf/results/foreign_smoke

Run it with the uvicorn server stopped if GPU memory is tight: both Kokoro and
Whisper are loaded in this process.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
import wave
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))
import os  # noqa: E402

os.chdir(PROJ)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJ / ".env")

import numpy as np  # noqa: E402

from app.languages import (  # noqa: E402
    DEFAULT_FOREIGN_LANGUAGES,
    contains_cjk,
    normalize_language_code,
)

try:  # The Windows console defaults to cp1252; the output is multilingual.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

#: One English question per language. The model is told to answer in the
#: selected language; the question itself stays English so a non-speaker can
#: read exactly what was asked.
QUESTION = "What is the tuition fee for the MBA program?"

#: A short, phonetically useful sentence per language. These are played through
#: Kokoro and transcribed back by Whisper; the source is printed in the report.
SPOKEN = {
    "es": "Hola, ¿cuál es la matrícula del programa de MBA?",
    "fr": "Bonjour, quel est le montant des frais de scolarité du programme MBA ?",
    "it": "Buongiorno, qual è la retta del programma MBA?",
    "ja": "こんにちは。MBAプログラムの学費はいくらですか。",
    "pt": "Olá, qual é a mensalidade do programa de MBA?",
    "zh": "您好，MBA 课程的学费是多少？",
}

#: Function words that make an English answer visibly not Spanish/French/Italian/
#: Portuguese. Two distinct hits (or a language-specific accented character) are
#: enough for this coarse script check; the LLM judge is the content check.
_LATIN_MARKERS = {
    "es": {"el", "la", "los", "las", "de", "que", "para", "con", "una", "es",
           "del", "se", "su", "más", "matrícula", "programa", "año", "dólares"},
    "fr": {"le", "la", "les", "des", "du", "une", "pour", "avec", "est", "que",
           "dans", "sur", "frais", "scolarité", "programme", "ans", "montant"},
    "it": {"il", "la", "gli", "le", "di", "che", "per", "con", "una", "è",
           "del", "della", "tasse", "programma", "anni", "retta"},
    "pt": {"o", "a", "os", "as", "de", "que", "para", "com", "uma", "é",
           "do", "da", "mensalidade", "programa", "anos", "qual"},
}
_LATIN_ACCENTS = {
    "es": "áéíóúñü¿¡",
    "fr": "àâæçéèêëîïôœùûüÿ",
    "it": "àèéìíîòóùú",
    "pt": "áâãàçéêíóôõú",
}
_WORD_RE = re.compile(r"[a-záéíóúñüàâæçèêëîïôœùûÿ]+", re.IGNORECASE)
_KANA_RE = re.compile(r"[぀-ヿ]")
_HAN_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_NUMBER_RE = re.compile(r"\d[\d,.]*")


# ── small helpers ────────────────────────────────────────────────────────────

def _normalise(text: str) -> str:
    """Case/punctuation/space-normalized text for similarity comparisons."""
    text = unicodedata.normalize("NFKC", text or "").lower()
    text = re.sub(r"[^\w\s぀-ヿ㐀-䶿一-鿿]", "", text)
    if contains_cjk(text):
        return re.sub(r"\s+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _similarity(source: str, hypothesis: str) -> float:
    a, b = _normalise(source), _normalise(hypothesis)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _numbers(text: str) -> set[str]:
    return {
        n.replace(",", "").replace(".", "")
        for n in _NUMBER_RE.findall(unicodedata.normalize("NFKC", text or ""))
    }


def _script_ok(text: str, code: str) -> bool:
    if not text.strip():
        return False
    if code == "ja":
        return bool(_KANA_RE.search(text) or _HAN_RE.search(text))
    if code == "zh":
        return bool(_HAN_RE.search(text)) and not _KANA_RE.search(text)
    if code in _LATIN_MARKERS:
        words = {w.lower() for w in _WORD_RE.findall(text)}
        hits = words & _LATIN_MARKERS[code]
        accents = any(ch in text for ch in _LATIN_ACCENTS[code])
        return len(hits) >= 2 or accents
    return True


def _judge_code(raw: str) -> str:
    code = normalize_language_code(raw)
    name_map = {
        "spanish": "es", "español": "es", "espanol": "es",
        "french": "fr", "français": "fr", "francais": "fr",
        "italian": "it", "italiano": "it",
        "japanese": "ja", "日本語": "ja",
        "portuguese": "pt", "português": "pt", "portugues": "pt",
        "chinese": "zh", "中文": "zh",
    }
    return name_map.get((raw or "").strip().lower(), code)


def _verdict(pass_ok: bool, review_ok: bool) -> str:
    return "PASS" if pass_ok else ("REVIEW" if review_ok else "FAIL")


# ── text check ───────────────────────────────────────────────────────────────

def _llm_judge(answer: str) -> dict:
    """Ask the model to identify the language and back-translate.

    The same model produced the answer, so this is a consistency check, not an
    independent evaluation. Whisper is the independent judge on the audio side.
    """
    from app.llm_backend import chat, is_ready

    if not is_ready():
        return {"skipped": "LLM not reachable (set LLM_API_KEY)"}

    messages = [
        {
            "role": "system",
            "content": (
                "You are a careful language evaluator. Return only a JSON "
                "object with keys 'language' and 'translation'."
            ),
        },
        {
            "role": "user",
            "content": (
                "Text:\n\"\"\"\n" + answer + "\n\"\"\"\n\n"
                "1. Which language is the text? Give its ISO 639-1 code.\n"
                "2. Translate it into English. Preserve every number, currency "
                "amount and proper noun exactly.\n"
                'Return exactly: {"language":"xx","translation":"..."}'
            ),
        },
    ]
    raw = ""
    try:
        raw = chat(messages, json_mode=True, num_predict=500)
    except Exception:                                    # noqa: BLE001
        try:
            raw = chat(messages, num_predict=500)
        except Exception as exc:                         # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {exc}"}
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        return {"error": "no JSON in judge reply", "raw": raw[:300]}
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"error": "invalid JSON from judge", "raw": raw[:300]}
    return payload


def check_text(code: str, use_llm: bool) -> dict:
    from app.rag import query_rag

    started = time.perf_counter()
    try:
        answer = query_rag(QUESTION, language=code)
    except Exception as exc:                             # noqa: BLE001
        return {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}
    elapsed = round((time.perf_counter() - started) * 1000)

    if not answer or not answer.strip():
        return {"status": "FAIL", "error": "empty answer", "latency_ms": elapsed}

    row = {
        "answer": answer,
        "latency_ms": elapsed,
        "script_ok": _script_ok(answer, code),
    }
    if not use_llm:
        row["status"] = _verdict(row["script_ok"], False)
        return row

    judge = _llm_judge(answer)
    row["judge"] = judge
    if judge.get("skipped") or judge.get("error"):
        row["status"] = _verdict(row["script_ok"], False)
        return row

    judged = _judge_code(str(judge.get("language", "")))
    translation = str(judge.get("translation", ""))
    source_numbers = _numbers(answer)
    translated_numbers = _numbers(translation)
    numbers_ok = (
        None if not source_numbers
        else bool(source_numbers & translated_numbers)
    )
    language_ok = judged == code
    row.update({
        "judged_language": judged,
        "translation": translation,
        "numbers_ok": numbers_ok,
    })
    row["status"] = _verdict(
        row["script_ok"] and language_ok
        and (numbers_ok is not False),
        row["script_ok"] or language_ok,
    )
    return row


# ── audio check ──────────────────────────────────────────────────────────────

def _transcribe(model, audio_float: np.ndarray) -> tuple[str, str, float]:
    """Transcribe float32 audio in [-1, 1] (the form faster-whisper wants)."""
    segments, info = model.transcribe(
        audio_float.astype(np.float32),
        language=None,
        beam_size=1,
        best_of=1,
        temperature=0.0,
        condition_on_previous_text=False,
        vad_filter=True,
        vad_parameters=dict(
            threshold=0.5,
            min_speech_duration_ms=250,
            min_silence_duration_ms=500,
            speech_pad_ms=100,
        ),
    )
    transcript = " ".join(seg.text.strip() for seg in segments).strip()
    return (
        transcript,
        normalize_language_code(getattr(info, "language", "")),
        float(getattr(info, "language_probability", 0.0) or 0.0),
    )


def _save_8k(path: Path, audio: np.ndarray) -> None:
    pcm16 = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(pcm16.tobytes())


def _phone_round_trip(audio: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray]:
    """24 kHz float -> 8 kHz telephone -> 16 kHz Whisper input."""
    from scipy.signal import resample

    a8 = resample(audio, int(len(audio) * 8000 / sr)).astype(np.float32)
    a16 = resample(a8, int(len(a8) * 16000 / 8000)).astype(np.float32)
    return a8, a16


def check_audio(code: str, out_dir: Path) -> dict:
    try:
        from app.voice_handler import (
            _get_stt_model,
            _get_tts_engine,
            _kokoro_lang_for,
            _kokoro_render,
            _tts_speed_for,
            _tts_text_for,
            _tts_voice_for,
        )
    except Exception as exc:                             # noqa: BLE001
        return {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}

    source = SPOKEN[code]
    try:
        engine = _get_tts_engine()
        voice = _tts_voice_for(code)
        started = time.perf_counter()
        # Use the application's render helper, not engine.create directly:
        # ja/zh must go through misaki phonemes or this test measures the old
        # espeak path instead of what a caller hears. The TTS reading may
        # substitute a local term for a Latin abbreviation, so compare the
        # transcript against what was actually spoken.
        tts_text = _tts_text_for(source, code)
        audio, sr = _kokoro_render(
            engine, tts_text, code, voice, _tts_speed_for(code)
        )
        synth_ms = round((time.perf_counter() - started) * 1000)
        audio = np.asarray(audio, dtype=np.float32)
        a8, a16 = _phone_round_trip(audio, sr)
        wav_path = out_dir / f"{code}_roundtrip.wav"
        _save_8k(wav_path, a8)

        transcript, detected, probability = _transcribe(_get_stt_model(), a16)
    except Exception as exc:                             # noqa: BLE001
        return {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}

    similarity = round(_similarity(tts_text, transcript), 3)
    detected_ok = detected == code
    row = {
        "source": source,
        "tts_text": tts_text,
        "voice": voice,
        "phoneme_source": "misaki" if code in ("ja", "zh") else "espeak",
        "phoneme_language": _kokoro_lang_for(code, voice),
        "speed": _tts_speed_for(code),
        "synth_ms": synth_ms,
        "wav": str(wav_path),
        "transcript": transcript,
        "detected_language": detected,
        "language_probability": round(probability, 3),
        "similarity": similarity,
    }
    row["status"] = _verdict(
        detected_ok and probability >= 0.5 and similarity >= 0.5,
        detected_ok and similarity >= 0.35,
    )
    return row


def check_fixed_asset(code: str) -> dict:
    """Transcribe the actual pre-recorded fixed-response asset."""
    try:
        from app.admission import (
            asset_available,
            asset_path,
            fixed_response_asset,
            fixed_response_text,
        )
        from app.voice_handler import _get_stt_model, _tts_text_for
    except Exception as exc:                             # noqa: BLE001
        return {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}

    name = fixed_response_asset(code)
    if not asset_available(name):
        return {"status": "FAIL", "asset": name, "error": "asset missing; run scripts/build_call_assets.py"}

    try:
        with wave.open(str(asset_path(name)), "rb") as w:
            if w.getframerate() != 8000 or w.getnchannels() != 1 or w.getsampwidth() != 2:
                return {"status": "FAIL", "asset": name, "error": "not 8 kHz mono 16-bit"}
            pcm = w.readframes(w.getnframes())
        audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        from scipy.signal import resample
        audio_16k = resample(audio, int(len(audio) * 2)).astype(np.float32)
        transcript, detected, probability = _transcribe(
            _get_stt_model(), audio_16k
        )
        source = fixed_response_text(code)
        tts_text = _tts_text_for(source, code)
        similarity = round(_similarity(tts_text, transcript), 3)
    except Exception as exc:                             # noqa: BLE001
        return {"status": "FAIL", "asset": name, "error": f"{type(exc).__name__}: {exc}"}

    detected_ok = detected == code
    row = {
        "asset": name,
        "source": source,
        "transcript": transcript,
        "detected_language": detected,
        "language_probability": round(probability, 3),
        "similarity": similarity,
    }
    row["status"] = _verdict(
        detected_ok and probability >= 0.5 and similarity >= 0.45,
        detected_ok and similarity >= 0.30,
    )
    return row


# ── reporting ────────────────────────────────────────────────────────────────

def _fmt(value) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--languages", default=",".join(DEFAULT_FOREIGN_LANGUAGES),
                    help="Comma-separated codes (default: all six).")
    ap.add_argument("--no-llm", action="store_true",
                    help="Skip the LLM language/back-translation check.")
    ap.add_argument("--no-audio", action="store_true",
                    help="Skip Kokoro -> Whisper and asset transcription.")
    ap.add_argument("--out", default="",
                    help="Output directory (default: doc/perf/results/foreign_smoke_<UTC>).")
    args = ap.parse_args(argv)

    codes = [
        normalize_language_code(part)
        for part in args.languages.split(",") if part.strip()
    ]
    unknown = [c for c in codes if c not in DEFAULT_FOREIGN_LANGUAGES]
    if unknown:
        ap.error(f"not a foreign language: {','.join(unknown)}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out) if args.out else (
        PROJ / "doc" / "perf" / "results" / f"foreign_smoke_{stamp}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 78)
    print("Foreign-language smoke test")
    print(f"languages: {', '.join(codes)}")
    print(f"text LLM check: {'off' if args.no_llm else 'on'}")
    print(f"audio round-trip: {'off' if args.no_audio else 'on'}")
    print(f"output: {out_dir}")
    print("=" * 78)

    report: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "question": QUESTION,
        "text": {},
        "audio": {},
    }

    for code in codes:
        print(f"\n--- {code} ---")
        if args.no_llm:
            report["text"][code] = {"status": "SKIPPED", "reason": "--no-llm"}
        else:
            row = check_text(code, use_llm=True)
            report["text"][code] = row
            print(f"  text    : {row['status']}")
            if row.get("answer"):
                print(f"  answer  : {row['answer'][:300]}")
            if row.get("translation"):
                print(f"  english : {row['translation'][:300]}")
            if row.get("error"):
                print(f"  error   : {row['error']}")

        if args.no_audio:
            report["audio"][code] = {"status": "SKIPPED", "reason": "--no-audio"}
            continue

        audio = check_audio(code, out_dir)
        asset = check_fixed_asset(code)
        report["audio"][code] = {"roundtrip": audio, "fixed_response": asset}
        print(f"  audio   : {audio['status']}  "
              f"stt={audio.get('detected_language')} "
              f"prob={_fmt(audio.get('language_probability'))} "
              f"sim={_fmt(audio.get('similarity'))}  wav={audio.get('wav', '-')}")
        if audio.get("transcript"):
            print(f"  heard   : {audio['transcript'][:300]}")
        if audio.get("error"):
            print(f"  error   : {audio['error']}")
        print(f"  asset   : {asset['status']}  "
              f"stt={asset.get('detected_language')} "
              f"sim={_fmt(asset.get('similarity'))}")
        if asset.get("error"):
            print(f"  error   : {asset['error']}")

    # A compact table for the report and the console.
    print("\n" + "=" * 78)
    print(f"{'lang':<5} {'text':<8} {'script':<7} {'judge':<6} {'audio':<8} "
          f"{'stt':<5} {'prob':<5} {'sim':<5} {'asset':<8}")
    worst = 0  # 0 PASS, 1 REVIEW, 2 FAIL
    for code in codes:
        text = report["text"].get(code, {})
        audio = report["audio"].get(code, {})
        rt = audio.get("roundtrip", {}) if isinstance(audio, dict) else {}
        fixed = audio.get("fixed_response", {}) if isinstance(audio, dict) else {}
        statuses = [text.get("status"), rt.get("status"), fixed.get("status")]
        for status in statuses:
            if status == "FAIL":
                worst = 2
            elif status == "REVIEW" and worst < 1:
                worst = 1
        print(f"{code:<5} {_fmt(text.get('status')):<8} "
              f"{_fmt(text.get('script_ok')):<7} "
              f"{_fmt(text.get('judged_language')):<6} "
              f"{_fmt(rt.get('status')):<8} "
              f"{_fmt(rt.get('detected_language')):<5} "
              f"{_fmt(rt.get('language_probability')):<5} "
              f"{_fmt(rt.get('similarity')):<5} "
              f"{_fmt(fixed.get('status')):<8}")

    report_path = out_dir / "report.json"
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8", newline="\n",
    )
    print(f"\nreport: {report_path}")
    print("WAVs are next to it; send them to a native speaker for the final review.")
    print("PASS = automatic round-trip; REVIEW = listen/review; FAIL = investigate.")
    return 0 if worst < 2 else 1


if __name__ == "__main__":
    raise SystemExit(main())
