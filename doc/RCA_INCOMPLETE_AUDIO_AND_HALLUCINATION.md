# 🔍 RCA: Incomplete TTS Audio + LLM Hallucination

**Date:** 2026-07-26
**Severity:** High — audio output is incomplete; answer contains fabricated data

---

## Issue 1: TTS Audio Cut Off Mid-Sentence

### Symptom

The spoken audio stops before the full text answer is read aloud. The user sees the complete text on screen but hears only part of it. The audio cuts off mid-word.

### Evidence

**User's text output (598 chars):**
> "UMD typically bills graduate tuition per credit hour... The BDS program has a slightly different duration with a first-year tuition ranging from ₹30.5 to 33.4 Lakhs over a period of 7 years. Would you like more specific information on any particular college or degree?"

**Audio file analysis:**
| Metric | Value |
|--------|-------|
| File duration | 37.5s |
| Truncated text (500 chars) TTS | 35.5s audio |
| Full text (598 chars) TTS | 41.4s audio |
| **Speech cut off** | **~6 seconds** |

### Root Cause

**The 500-character truncation limit at `app.py` line 313 cuts off text mid-word.**

```python
tts_text = answer[:500] + "..." if len(answer) > 500 else answer
```

At position 500, the text reads: `"...Lakhs over a"` — the truncation point falls in the middle of "over a period of 7 years." The closing sentence *"Would you like more specific information..."* is completely omitted from audio.

The 500-char limit was set to keep TTS generation under ~15s on CPU, but with GPU acceleration (2x speedup), we can safely increase this.

### Fix

1. Increase limit to 800 chars
2. Truncate at the last complete sentence boundary, not mid-word

---

## Issue 2: LLM Hallucinating Fabricated Data

### Symptom

The LLM generates answers with information that does NOT exist in the UMD/FDU university profile PDF:

| Hallucinated content | Evidence it's fabricated |
|---|---|
| "₹33.4 Lakh" (Indian Rupees) | Source PDF uses USD only, no INR amounts |
| "B.E./B.Tech., BBA, BDS programs" | Not mentioned in UMD/FDU profile |
| "7 years" for BDS | No such program in source |
| "fdu.edu/...graduate-tuition-fees" URL for UMD info | Mixed UMD context with FDU URL |

### Root Cause

**Qwen 2.5 7B is a 7B-parameter model with limited instruction-following ability.** The system prompt says "Use ONLY the provided context" but the model:

1. When it lacks specific data in the retrieved chunks, it fills gaps from pre-training knowledge
2. The pre-training data includes generic international student fee information (in INR) that leaks into the answer
3. The model confuses entity boundaries (UMD vs FDU), especially when both appear in the same context
4. Temperature 0.0 reduces but does NOT eliminate hallucination in smaller models

### Evidence: The RAG prompt is already strict but the model ignores it

```python
system_prompt = (
    "STRICT FACTUAL CONSTRAINTS:\n"
    "1. Rely EXCLUSIVELY on the provided context. If a detail isn't in the text, politely say so.\n"
    "2. Never merge or confuse details between UMD and FDU.\n"
    ...
)
```

The model is instructed not to hallucinate, but at 7B parameters, it cannot reliably follow these constraints — especially for numeric data and URLs.

### Fix

1. Add explicit "say you don't know" fallback with stronger language
2. Add explicit anti-hallucination examples in the prompt
3. Consider switching to `qwen2.5:7b-instruct-q3_K_M` (instruct-tuned variant already available in Ollama) which has better instruction following
