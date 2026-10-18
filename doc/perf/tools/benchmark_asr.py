"""ASR bake-off: Whisper vs IndicConformer vs Sarvam on domain utterances.

Run directly (not under pytest):

    .venv/Scripts/python.exe doc/perf/tools/benchmark_asr.py
    .venv/Scripts/python.exe doc/perf/tools/benchmark_asr.py --providers sarvam,indic_conformer,whisper

What it measures, on the same audio and with 8 kHz µ-law telephony simulated:

* WER and CER against the reference sentence
* domain-entity recall (programme names, fees, university name)
* intent-proxy accuracy: does the expected domain term survive?
* p50/p95 end-of-speech -> transcript latency (first call per provider is a
  warm-up and is excluded)
* Sarvam cost estimate at ₹30/hour

The intent metric is deliberately a keyword proxy, not the full LLM policy
pipeline. It answers "did the ASR preserve the words the intent layer keys on?"
without spending an LLM call per sample. Real intent quality still has to be
confirmed on live calls.

Audio: Hindi is synthesised locally with Kokoro (hf_alpha, lang=hi). Gujarati
uses Edge TTS when the optional `edge-tts` package and ffmpeg are available;
otherwise the Gujarati rows are marked pending rather than silently skipped.
The generated WAVs and manifest are kept under doc/perf/results/asr_bench/ so
the same set can be re-run against Sarvam once its key is configured.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import subprocess
import sys
import time
import unicodedata
import wave
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

PROJ = Path(__file__).resolve().parents[3]
if str(PROJ) not in sys.path:
    sys.path.insert(0, str(PROJ))

RESULTS = PROJ / "doc" / "perf" / "results"
AUDIO_DIR = RESULTS / "asr_bench"

HINDI = [
    ("hi_01", "मुझे बी.टेक कंप्यूटर साइंस में प्रवेश लेना है", ["B.Tech", "Computer Science"], "admission"),
    ("hi_02", "एमबीए की फीस कितनी है?", ["MBA", "fees"], "fees"),
    ("hi_03", "मेरिडियन यूनिवर्सिटी में कौन से कोर्स उपलब्ध हैं?", ["Meridian University"], "program_list"),
    ("hi_04", "बीसीए के लिए क्या योग्यता चाहिए?", ["BCA", "eligibility"], "eligibility"),
    ("hi_05", "मुझे स्कॉलरशिप के बारे में जानकारी चाहिए", ["scholarship"], "scholarship"),
    ("hi_06", "बी.कॉम में एडमिशन के लिए क्या प्रक्रिया है?", ["B.Com", "admission"], "admission"),
    ("hi_07", "हॉस्टल की फीस कितनी है?", ["hostel", "fees"], "fees"),
    ("hi_08", "एम.टेक में प्रवेश के लिए क्या जरूरी है?", ["M.Tech", "eligibility"], "eligibility"),
    ("hi_09", "मेरा नाम अमित वर्मा है", ["Amit", "Verma"], "profile"),
    ("hi_10", "मुझे हिंदी में बात करनी है", ["Hindi"], "language"),
]

GUJARATI = [
    ("gu_01", "મને બી.કોમમાં પ્રવેશ લેવો છે", ["B.Com", "admission"], "admission"),
    ("gu_02", "એમબીએની ફી કેટલી છે?", ["MBA", "fees"], "fees"),
    ("gu_03", "મેરિડિયન યુનિવર્સિટીમાં કયા કોર્સ ઉપલબ્ધ છે?", ["Meridian University"], "program_list"),
    ("gu_04", "બીસીએ માટે કઈ લાયકાત જોઈએ?", ["BCA", "eligibility"], "eligibility"),
    ("gu_05", "હોસ્ટેલની ફી કેટલી છે?", ["hostel", "fees"], "fees"),
]

_PUNCT = re.compile(r"[।॥.,!?;:\"'()\[\]{}]")

#: Domain terms canonicalised for WER. Sarvam's live mode is `codemix`, so it
#: writes "MBA की fees" where the reference is "एमबीए की फीस". Raw WER would
#: count that as four substitutions even though every intent-bearing token is
#: correct. Domain WER maps both scripts to one token first.
_CANONICAL_WORDS = {
    variant: canonical
    for canonical, variants in {
        "btech": ["btech", "बीटेक", "બીટેક"],
        "mba": ["mba", "एमबीए", "એમબીએ"],
        "bca": ["bca", "बीसीए", "બીસીએ"],
        "bcom": ["bcom", "बीकॉम", "બીકોમ"],
        "mtech": ["mtech", "एमटेक", "એમટેક"],
        "fees": ["fees", "फीस", "ફી"],
        "course": ["course", "कोर्स", "કોર્સ"],
        "scholarship": ["scholarship", "स्कॉलरशिप", "શિષ્યવૃત્તિ"],
        "admission": ["admission", "एडमिशन", "प्रवेश", "એડમિશન", "પ્રવેશ"],
        "hostel": ["hostel", "हॉस्टल", "હોસ્ટેલ"],
        "eligibility": ["eligibility", "योग्यता", "पात्रता", "લાયકાત", "પાત્રતા"],
        "meridian": ["meridian", "मेरिडियन", "મેરિડિયન"],
        "university": ["university", "यूनिवर्सिटी", "યુનિવર્સિટી"],
        "computer": ["computer", "कंप्यूटर", "કંપ્યુટર"],
        "science": ["science", "साइंस", "સાયન્સ"],
        "amit": ["amit", "अमित", "અમિત"],
        "verma": ["verma", "वर्मा", "વર્મા"],
        "hindi": ["hindi", "हिंदी", "હિન્દી"],
    }.items()
    for variant in variants
}

#: The same domain term in the scripts a provider may output. Entity recall
#: counts a group as hit when ANY variant survives; comparing only Latin
#: acronyms would mark a correct IndicConformer "बीटेक" as a miss.
_ENTITY_ALIASES = {
    "btech": ["btech", "बीटेक", "બીટેક"],
    "mba": ["mba", "एमबीए", "એમબીએ"],
    "bca": ["bca", "बीसीए", "બીસીએ"],
    "bcom": ["bcom", "बीकॉम", "બીકોમ"],
    "mtech": ["mtech", "एमटेक", "એમટેક"],
    "meridianuniversity": ["meridianuniversity", "मेरिडियनयूनिवर्सिटी",
                           "મેરિડિયનયુનિવર્સિટી"],
    "computerscience": ["computerscience", "कंप्यूटरसाइंस",
                        "કંપ્યુટરસાયન્સ"],
    "fees": ["fees", "फीस", "ફી"],
    "eligibility": ["eligibility", "योग्यता", "पात्रता", "લાયકાત", "પાત્રતા"],
    "admission": ["admission", "एडमिशन", "प्रवेश", "એડમિશન", "પ્રવેશ"],
    "scholarship": ["scholarship", "स्कॉलरशिप", "छात्रवृत्ति",
                    "શિષ્યવૃત્તિ"],
    "hostel": ["hostel", "हॉस्टल", "હોસ્ટેલ"],
    "amit": ["amit", "अमित", "અમિત"],
    "verma": ["verma", "वर्मा", "વર્મા"],
    "hindi": ["hindi", "हिंदी", "હિન્દી"],
}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "")
    text = _PUNCT.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Latin words are case-insensitive; Indic scripts have no case.
    return " ".join(w.lower() if w.isascii() else w for w in text.split())


def _levenshtein(a, b) -> int:
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(
                current[-1] + 1,
                previous[j] + 1,
                previous[j - 1] + (ca != cb),
            ))
        previous = current
    return previous[-1]


def wer(reference: str, hypothesis: str) -> float:
    ref = normalize(reference).split()
    hyp = normalize(hypothesis).split()
    return _levenshtein(ref, hyp) / max(1, len(ref))


def cer(reference: str, hypothesis: str) -> float:
    ref = normalize(reference).replace(" ", "")
    hyp = normalize(hypothesis).replace(" ", "")
    return _levenshtein(ref, hyp) / max(1, len(ref))


def _domain_tokens(text: str) -> list[str]:
    """Canonicalise greedy longest match, including split acronyms.

    Punctuation removal turns "बी.टेक" and "बी.सी.ए." into two/three tokens.
    The canonical keys are the joined forms ("बीटेक", "बीसीए"), so a one-token
    lookup would miss the exact term the intent layer needs. Try 3-, 2- then
    1-token joins before falling back to the raw token.
    """
    words = normalize(text).split()
    out: list[str] = []
    i = 0
    while i < len(words):
        for span in (3, 2, 1):
            if i + span > len(words):
                continue
            joined = "".join(words[i:i + span])
            canonical = _CANONICAL_WORDS.get(joined)
            if canonical:
                out.append(canonical)
                i += span
                break
        else:
            out.append(words[i])
            i += 1
    return out


def wer_domain(reference: str, hypothesis: str) -> float:
    ref = _domain_tokens(reference)
    hyp = _domain_tokens(hypothesis)
    return _levenshtein(ref, hyp) / max(1, len(ref))


def cer_domain(reference: str, hypothesis: str) -> float:
    ref = "".join(_domain_tokens(reference))
    hyp = "".join(_domain_tokens(hypothesis))
    return _levenshtein(ref, hyp) / max(1, len(ref))


def entity_hit(transcript: str, entity: str) -> bool:
    haystack = normalize(transcript).replace(" ", "")
    needle = normalize(entity).replace(" ", "")
    if not needle:
        return False
    variants = _ENTITY_ALIASES.get(needle, [needle])
    return any(variant in haystack for variant in variants)


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round((p / 100.0) * (len(ordered) - 1))))
    return ordered[idx]


def write_wav(path: Path, audio: np.ndarray, sr: int) -> None:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return pcm.astype(np.float32) / 32768.0, sr


def to_16k(audio: np.ndarray, sr: int) -> np.ndarray:
    from scipy.signal import resample_poly
    if sr == 16000:
        return audio.astype(np.float32)
    return resample_poly(audio, 16000, sr).astype(np.float32)


def telephony(audio16: np.ndarray) -> np.ndarray:
    """Round-trip through 8 kHz µ-law, the carrier path Twilio uses."""
    from scipy.signal import resample_poly
    from app.voice_handler import pcm_to_ulaw, ulaw_to_pcm

    audio8 = resample_poly(audio16, 8000, 16000).astype(np.float32)
    pcm8 = (np.clip(audio8, -1, 1) * 32767).astype(np.int16)
    ulaw = pcm_to_ulaw(pcm8.tobytes())
    pcm8b = np.frombuffer(ulaw_to_pcm(ulaw), dtype=np.int16)
    return resample_poly(pcm8b.astype(np.float32) / 32768.0, 16000, 8000)


def generate_audio() -> list[dict]:
    """Create/refresh the local Hindi set and the Edge-TTS Gujarati set."""
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []

    from app.voice_handler import _get_tts_engine
    engine = _get_tts_engine()
    for sid, text, entities, intent in HINDI:
        audio, sr = engine.create(text, voice="hf_alpha", speed=1.0, lang="hi")
        path = AUDIO_DIR / f"{sid}.wav"
        write_wav(path, audio, sr)
        rows.append(dict(file=path.name, language="hi", reference=text,
                         entities=entities, intent=intent))

    try:
        import edge_tts
        for sid, text, entities, intent in GUJARATI:
            mp3 = AUDIO_DIR / f"{sid}.mp3"
            wav = AUDIO_DIR / f"{sid}.wav"
            asyncio.run(edge_tts.Communicate(
                text, voice="gu-IN-DhwaniNeural"
            ).save(str(mp3)))
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3),
                 "-ar", "16000", "-ac", "1", str(wav)],
                check=True,
            )
            mp3.unlink(missing_ok=True)
            rows.append(dict(file=wav.name, language="gu", reference=text,
                             entities=entities, intent=intent))
    except Exception as exc:                    # noqa: BLE001
        print(f"Gujarati generation pending: {exc}")

    manifest = AUDIO_DIR / "manifest.jsonl"
    with manifest.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"Generated {len(rows)} clips under {AUDIO_DIR}")
    return rows


def load_manifest(paths: list[str] | None) -> list[dict]:
    if not paths:
        manifest = AUDIO_DIR / "manifest.jsonl"
        if not manifest.exists():
            return generate_audio()
        rows = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not rows:
            return generate_audio()
        return rows
    rows = []
    for raw in paths:
        p = Path(raw)
        if p.suffix == ".jsonl":
            rows += [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
        else:
            rows.append(json.loads(p.read_text(encoding="utf-8")))
    return rows


def transcribe_whisper(audio16: np.ndarray, language: str) -> str:
    from app.voice_handler import _get_stt_model
    model = _get_stt_model()
    segments, _info = model.transcribe(
        audio16,
        language=language or None,
        beam_size=1,
        best_of=1,
        temperature=0.0,
        condition_on_previous_text=False,
        vad_filter=True,
        vad_parameters=dict(threshold=0.5, min_speech_duration_ms=250,
                            min_silence_duration_ms=500, speech_pad_ms=100),
    )
    return " ".join(seg.text.strip() for seg in segments).strip()


def transcribe_indic(audio16: np.ndarray, language: str):
    from app import stt_indic
    pcm = (np.clip(audio16, -1, 1) * 32767).astype(np.int16)
    return stt_indic.transcribe(pcm, language)


def transcribe_sarvam(audio16: np.ndarray, language: str):
    from app import stt_sarvam
    pcm = (np.clip(audio16, -1, 1) * 32767).astype(np.int16)
    return stt_sarvam.transcribe(pcm, language_hint=language)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", nargs="*", default=None)
    parser.add_argument("--generate", action="store_true",
                        help="regenerate the built-in Hindi/Gujarati clips")
    parser.add_argument("--providers", default="indic_conformer,whisper")
    parser.add_argument("--no-telephony", action="store_true")
    parser.add_argument("--out-dir", default=str(RESULTS))
    args = parser.parse_args()

    rows = generate_audio() if args.generate else load_manifest(args.manifest)
    providers = [p.strip() for p in args.providers.split(",") if p.strip()]

    from app import stt_sarvam
    if "sarvam" in providers and not stt_sarvam.is_configured():
        print("Sarvam skipped: SARVAM_API_KEY is empty")
        providers.remove("sarvam")
    if not providers:
        print("No providers selected")
        return 1

    results: dict[str, list[dict]] = {p: [] for p in providers}
    audio_seconds = 0.0

    for row in rows:
        path = Path(row["file"])
        if not path.is_absolute():
            candidate = AUDIO_DIR / path
            path = candidate if candidate.exists() else path
        if not path.exists():
            print(f"missing {path}; run --generate")
            continue
        audio, sr = read_wav(path)
        audio16 = to_16k(audio, sr)
        if not args.no_telephony:
            audio16 = telephony(audio16)
        audio_seconds += len(audio16) / 16000.0

        for provider in providers:
            started = time.perf_counter()
            try:
                if provider == "whisper":
                    text = transcribe_whisper(audio16, row["language"])
                elif provider == "indic_conformer":
                    text = transcribe_indic(audio16, row["language"]).transcript
                elif provider == "sarvam":
                    text = transcribe_sarvam(audio16, row["language"]).transcript
                else:
                    raise ValueError(f"unknown provider {provider}")
                latency = (time.perf_counter() - started) * 1000.0
                error = ""
            except Exception as exc:            # noqa: BLE001
                text, latency, error = "", 0.0, f"{type(exc).__name__}: {exc}"
            hits = [e for e in row.get("entities", []) if entity_hit(text, e)]
            results[provider].append({
                "file": row["file"], "language": row["language"],
                "reference": row["reference"], "transcript": text,
                "wer": wer(row["reference"], text),
                "cer": cer(row["reference"], text),
                "wer_domain": wer_domain(row["reference"], text),
                "cer_domain": cer_domain(row["reference"], text),
                "entity_hits": hits,
                "entity_total": len(row.get("entities", [])),
                "intent": row.get("intent", ""),
                "intent_ok": bool(hits),
                "latency_ms": latency,
                "error": error,
            })
            print(f"  {provider:14s} {row['file']:7s} "
                  f"wer={results[provider][-1]['wer']:.2f} "
                  f"wer_dom={results[provider][-1]['wer_domain']:.2f} "
                  f"ent={len(hits)}/{len(row.get('entities', []))} "
                  f"{latency:.0f} ms")
            if error:
                print(f"    error: {error}")

    summary = {"generated_at": datetime.now(timezone.utc).isoformat(),
               "telephony_simulated": not args.no_telephony,
               "audio_seconds": round(audio_seconds, 1),
               "providers": {}}
    for provider, items in results.items():
        # Drop the first successful call per provider AND language: the first
        # IndicConformer call per language pays a model load, and comparing
        # that against Whisper's already-warm model would be dishonest.
        timed: list[float] = []
        warmed: set[tuple[str, str]] = set()
        for item in items:
            if item["error"]:
                continue
            key = (provider, item["language"])
            if key not in warmed:
                warmed.add(key)
                continue
            timed.append(item["latency_ms"])
        summary["providers"][provider] = {
            "items": items,
            "wer_mean": round(statistics.mean([i["wer"] for i in items]), 4) if items else None,
            "cer_mean": round(statistics.mean([i["cer"] for i in items]), 4) if items else None,
            "wer_domain_mean": round(
                statistics.mean([i["wer_domain"] for i in items]), 4
            ) if items else None,
            "cer_domain_mean": round(
                statistics.mean([i["cer_domain"] for i in items]), 4
            ) if items else None,
            "entity_recall": round(
                sum(len(i["entity_hits"]) for i in items) /
                max(1, sum(i["entity_total"] for i in items)), 4),
            "intent_proxy": round(
                sum(1 for i in items if i["intent_ok"]) / max(1, len(items)), 4),
            "latency_p50_ms": round(percentile(timed, 50), 1),
            "latency_p95_ms": round(percentile(timed, 95), 1),
            "errors": sum(1 for i in items if i["error"]),
            "sarvam_cost_inr": (
                round(audio_seconds / 3600.0 * 30.0, 4)
                if provider == "sarvam" else None
            ),
        }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"asr_bench_{stamp}.json"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    md_path = out_dir / f"asr_bench_{stamp}.md"
    md = [
        f"# ASR benchmark — {summary['generated_at']}",
        "",
        f"Telephony simulated: {summary['telephony_simulated']} · "
        f"audio: {summary['audio_seconds']} s",
        "",
        "| Provider | WER raw | WER domain | CER domain | Entity recall | Intent proxy | p50 ms | p95 ms | Errors |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for provider, s in summary["providers"].items():
        md.append(
            f"| {provider} | {s['wer_mean']:.3f} | {s['wer_domain_mean']:.3f} | "
            f"{s['cer_domain_mean']:.3f} | "
            f"{s['entity_recall']:.2f} | {s['intent_proxy']:.2f} | "
            f"{s['latency_p50_ms']:.0f} | {s['latency_p95_ms']:.0f} | "
            f"{s['errors']} |"
        )
    md += ["", "WER raw counts script differences; WER domain canonicalises "
           "MBA/एमबीए, fees/फीस, etc. before scoring. Intent proxy = expected "
           "domain term survived; it is not the LLM policy layer.", ""]
    md_path.write_text("\n".join(md), encoding="utf-8")
    print(f"\nWrote {json_path}\nWrote {md_path}")
    for provider, s in summary["providers"].items():
        print(f"{provider:14s} WER={s['wer_mean']:.3f} "
              f"WERdom={s['wer_domain_mean']:.3f} "
              f"CERdom={s['cer_domain_mean']:.3f} "
              f"entities={s['entity_recall']:.2f} intent_proxy={s['intent_proxy']:.2f} "
              f"p50={s['latency_p50_ms']:.0f}ms p95={s['latency_p95_ms']:.0f}ms "
              f"errors={s['errors']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
