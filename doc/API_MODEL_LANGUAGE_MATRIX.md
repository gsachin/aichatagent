# API, Model & Language Support Matrix

Verified against the running stack on **2026-10-17** (branch `fix/voice-grounding-boundary`,
commit `e5b2965` `multilingual-v1`, plus the uncommitted sample-rate fix) and the
live `.env`. Code references are to the files on this branch.

This document answers three questions:

1. Which external APIs and models does every chatbot surface use, and why?
2. Which language does each model support?
3. With the current stack, which languages can we actually support end to end?

---

## 1. Executive summary

| Concern | Current choice |
|---|---|
| **LLM (all text generation)** | OpenAI Chat Completions, `gpt-6-luna` (`LLM_MODEL`), one remote provider only |
| **Knowledge delivery** | Full document in the system prompt (`CONTEXT_MODE=doc`); **no RAG, no embeddings, no Chroma** |
| **STT (voice)** | Sarvam Saaras v4 (`codemix`) -> IndicConformer (hi/gu) -> faster-whisper `small` |
| **TTS (voice)** | Sarvam Bulbul v4 Flash for Gujarati; Kokoro ONNX for English/Hindi and other mapped languages; AI4Bharat Indic-TTS v1 optional |
| **Voice carrier** | Twilio Programmable Voice + Media Streams (8 kHz μ-law) |
| **WhatsApp** | Twilio WhatsApp webhook; text only; same OpenAI model |
| **Streamlit chat** | Text only; LangChain `ChatOpenAI`; same OpenAI model |
| **End-to-end voice languages today** | **English (`en`), Hindi (`hi`), Gujarati (`gu`)** |
| **Text-only languages** | Anything the LLM can answer; `LANGUAGE_NAMES` explicitly names 20 codes |
| **Foreign-language voice today** | **None are end-to-end.** Spanish/French/Italian/Japanese/Portuguese/Chinese have TTS voices and LLM support, but no working STT route, language selection, or localized fixed lines |

Removed in the OpenAI migration: Ollama, MLX, Qwen local models, ChromaDB,
nomic-embed-text, `langchain-ollama`, `langchain-community`, and the RAG
retriever/index. The live stack has **one** generation provider and **zero**
embedding models.

---

## 2. API inventory

| API | Endpoint / protocol | SDK or client | Used by | Why |
|---|---|---|---|---|
| **OpenAI Chat Completions** | `POST {LLM_BASE_URL}/chat/completions` (default `https://api.openai.com/v1`) | `openai==2.54.0`; `langchain-openai==1.5.1` for Streamlit | Voice, WhatsApp, Streamlit, sentiment, lead extraction, intent classification | Single multilingual remote model; no GPU; full-document prompting; prompt caching |
| **OpenAI Models** | `GET {LLM_BASE_URL}/models` | stdlib `urllib` | `/ready`, health, CLI | Reachability and model listing |
| **Sarvam STT (Saaras)** | `POST https://api.sarvam.ai/speech-to-text` (multipart WAV, 16 kHz mono) | `requests` | Live voice STT, primary | Indic-first, code-mixed telephony, 8 kHz native, 49 domain keyterms |
| **Sarvam TTS (Bulbul)** | `POST https://api.sarvam.ai/text-to-speech/stream` (JSON -> raw linear16 PCM) | `httpx` | Gujarati voice, greeting and answers | Kokoro has no Gujarati voice; Bulbul streams at 8 kHz, the Twilio carrier rate |
| **Twilio Programmable Voice** | REST `Calls.json` + Media Streams WebSocket (`/ws/twilio`, `/ws/twilio-outbound`) | `twilio==9.10.9`, FastAPI WebSocket | Inbound and outbound calls | Carrier, μ-law audio, DTMF/IVR, call control |
| **Twilio WhatsApp** | `POST /twilio/whatsapp` webhook | FastAPI form handler | WhatsApp chat | Inbound text/documents; no model of its own |
| **Salesforce middleware** | `http://127.0.0.1:8098` (local FastAPI) | `httpx` | CRM lead/conversation updates | Not an AI API; keeps CRM writes out of the call path |
| **PostgreSQL** | `DATABASE_URL` | `psycopg2` | Conversations, leads, call queue, sentiment | Persistence |
| **Cloudflare Tunnel** | ephemeral `trycloudflare.com` hosts | `cloudflared` | Twilio/WhatsApp/Streamlit ingress (development) | Public HTTPS without a fixed domain |
| **Indic-TTS service** | `http://127.0.0.1:8020` (optional local FastAPI) | `httpx` | AI4Bharat Indic-TTS v1 | Widest Indic TTS coverage; not active in the current `.env` |
| **Local model runtimes** | In-process ONNX / CTranslate2 | `kokoro-onnx`, `faster-whisper`, `onnx-asr` | Kokoro TTS, Whisper STT, IndicConformer STT | Local fallback and low-latency speech |

### OpenAI request shape

- `LLM_MODEL=gpt-6-luna`, `LLM_BASE_URL=https://api.openai.com/v1`
- `LLM_MAX_TOKENS=192` keeps spoken answers short
- `LLM_REASONING_EFFORT=none`
- `LLM_PROMPT_CACHE_KEY=meridian-kb-v1` (stable system+document prefix)
- `LLM_TEMPERATURE` is empty, so **temperature is omitted**. `gpt-6-luna` rejects
  non-default temperature values with HTTP 400.
- Streaming is used by voice (`LLM_STREAM=1`) and optionally for tool use; text
  surfaces use the same adapter.

---

## 3. Model inventory and rationale

| Model | Provider | Where used | Why this model |
|---|---|---|---|
| **`gpt-6-luna`** | OpenAI | All text generation: voice answers, WhatsApp, Streamlit, sentiment, lead extraction, follow-up parsing, admission-intent classification | One remote multilingual model; handles English + Indic + code-mixed prompts; full KB fits in context; prompt caching; no GPU/VRAM on the box |
| **Sarvam Saaras v4** (`saaras:v4`) | Sarvam AI | Primary STT for live voice | Trained on Indian languages and code-mixed speech; accepts 8 kHz telephony; 49 keyterms keep Meridian/B.Tech/MBA names intact |
| **AI4Bharat IndicConformer-600M** (int8 ONNX) | Local, via `onnx-asr` | STT fallback for **Hindi and Gujarati only** | Offline fallback when Sarvam is unavailable; ~138 MB per language; CPU int8 ~0.25 RTF |
| **faster-whisper `small`** | Local CTranslate2 | Last-resort STT; broad language coverage | Historical fallback; multilingual `small` (not `small.en`); greedy decoding and VAD tuned for phone audio |
| **Sarvam Bulbul v4 Flash** (`bulbul:v4-flash`) | Sarvam AI | Gujarati TTS (answers and greeting) | Kokoro has no Gujarati voice; Bulbul has Gujarati personas and streams raw 8 kHz linear16, matching Twilio |
| **Kokoro ONNX** | Local, `kokoro-onnx==0.5.0` | English and Hindi TTS today; mapped voices for six more languages | Low-latency local TTS; 24 kHz; no per-character cloud cost |
| **AI4Bharat Indic-TTS v1** | Local service at `:8020` (optional) | Not active (`TTS_PROVIDER=sarvam`) | Covers 13 Indic languages, including four Sarvam does not (as, brx, mni, raj) and is the fallback for wider Indic coverage |
| ~~Embedding model~~ | — | **None** | RAG was removed. The entire `content/meridian/meridian_knowledge_base.md` (~15.6 KB / ~3.2k tokens) is injected every turn |

### Why no RAG / embeddings

`CONTEXT_MODE=doc` (`app/doc_context.py`) loads the configured Markdown file(s),
caches them by `(path, mtime, size)`, and enforces `DOC_MAX_BYTES` /
`DOC_MAX_TOKENS`. With a 15.6 KB knowledge base, passing the whole document is
simpler, removes an embedding round trip from every turn, and keeps a stable
prefix for OpenAI prompt caching. `app/rag.py` still owns prompt construction
and the public `query_rag` / `query_rag_stream` functions, but it no longer
retrieves chunks.

---

## 4. Chatbot surfaces and the models they use

| Surface | Entry point | STT | LLM | TTS | Language selection |
|---|---|---|---|---|---|
| **Inbound Twilio call** | `/twilio/voice` -> IVR -> `/ws/twilio` | Sarvam -> IndicConformer -> Whisper | OpenAI `gpt-6-luna` (`query_rag_stream`) | Sarvam gu / Kokoro en,hi | IVR digits 1=en, 2=hi, 3=gu; `?language=` pins a code |
| **Outbound Twilio call** | `OutboundCallWorker` -> `/twilio/outbound-voice` -> `/ws/twilio-outbound` | same | same | same | `language` query on the TwiML URL; **the worker does not send one today**, so outbound defaults to `DEFAULT_LANGUAGE=en` |
| **WhatsApp** | `POST /twilio/whatsapp` | none (voice notes rejected) | OpenAI via `run_rag_query_sync(mode="chat")` | none | Twilio `Language` form field, else `DEFAULT_LANGUAGE` |
| **Streamlit chat** | `streamlit run app.py` (port 8501) | none | OpenAI via LangChain `ChatOpenAI` | none | Selector: English / Hindi / Gujarati only |
| **CLI (legacy)** | `python admissions_bot.py` | none | OpenAI via `get_chat_model` | none | none; not a served surface |
| **Post-call / background** | Twilio disconnect, WhatsApp intent, CRM tasks | none | OpenAI via `backend_chat` | none | follows the transcript language |

### 4.1 Twilio voice

- Inbound language is chosen by IVR (`_LANGUAGE_BY_DIGIT`, `app/main.py:548`) or a
  `?language=<code>` query on the voice webhook.
- The language travels to the Media Stream as a `<Parameter>`; the session sets
  `self.language` from it.
- With `STT_LANGUAGE=auto`, the detected language replaces the session language
  only when it is in `_SUPPORTED_LANGUAGES`, the transcript is long enough, the
  probability is at least `STT_LANGUAGE_MIN_PROB=0.5`, and the transcript is not
  low-confidence (`_apply_detected_language`, `app/voice_handler.py:2349`).
- Normal answers are synthesized clause by clause as the LLM streams, and each
  provider chunk carries its own sample rate into the μ-law converter.
- Fallback lines (no speech, noise-gated, sign-off) use the batch converter,
  which now receives the real sample rate (`_synthesise_with_rate` /
  `_pcm_to_ulaw_chunks`, `app/voice_handler.py:2634`, `:3075`).

### 4.2 WhatsApp

- Text only by design; incoming audio/voice notes are answered with a request to
  type (`twilio_whatsapp_webhook`, `app/main.py:2411`).
- Answers go through `_whatsapp_chat_rag` -> `run_rag_query_sync(mode="chat")` ->
  `query_rag` -> OpenAI (`app/main.py:2140`, `app/pipeline.py:309`,
  `app/rag.py:107`).
- Conversation history and known lead fields are attached per turn.
- The answer language is `Language` from the Twilio form payload, falling back to
  `DEFAULT_LANGUAGE`; `language_instruction` then asks the LLM to answer in that
  language.

### 4.3 Streamlit

- Text-only after the voice-recording/TTS removal.
- `load_rag_chain()` builds a LangChain `create_retrieval_chain` whose retriever
  is a `RunnableLambda` returning the **whole document**, with
  `get_chat_model(num_ctx=None)` as the LLM (`app.py:255`).
- The language selector is hard-coded to English / Hindi / Gujarati
  (`app.py:155`); `_with_answer_language()` appends `language_instruction`.
- No temperature is passed, because `gpt-6-luna` rejects non-default values.

### 4.4 Background model calls (all OpenAI `gpt-6-luna`)

| Job | Code | Why it calls the LLM |
|---|---|---|
| Lead extraction from a call transcript | `app/database.py:200` | Pull name/email/program out of free text as JSON |
| Follow-up date parsing | `app/leads/service.py:240` | Convert "next week" into an ISO timestamp |
| Follow-up intent confirmation | `app/leads/service.py:460` | Confirm weak phrases like "later" mean a follow-up |
| Admission-intent classification | `app/leads/service.py:526` | Confirm medium-strength phrases mean "ready to enroll" |
| WhatsApp admission-intent classification | `app/main.py:2330` | Same classification for the WhatsApp state machine |
| Sentiment / lead scoring | `app/sentiment/scorer.py:143` | Extract structured sentiment/BANT JSON |

These calls use `default_model([...])`/`pick_model(...)`; any local model tags
passed by older call sites (for example `qwen2.5:7b`) are ignored, and
`LLM_MODEL` is the single source of truth (`app/llm_backend.py:77`).

### 4.5 Test-only / legacy paths

- `app/pipeline.py` is a Pipecat test pipeline (`pipecat-ai==1.6.0`). Its module
  docstring still mentions Ollama/Chroma/Qwen, but the actual LLM service is
  `OpenAILLMService` (`app/pipeline.py:131`). The live Twilio path does **not**
  use Pipecat.
- `admissions_bot.py` is a standalone CLI. It calls `get_chat_model`, so it uses
  `LLM_MODEL`; the `model="qwen2.5:7b"` argument is an ignored legacy tag.
- `test_rag_llm.py`, `test_full_pipeline.py`, and `run_pipeline_test.py` are
  manual diagnostics, not chatbot surfaces.

---

## 5. Language support by model

### 5.1 LLM: OpenAI `gpt-6-luna`

The LLM is language-agnostic; the product controls the answer language with the
per-turn `language_instruction` (`app/voice_system_prompt.py:858`). The explicit
name map (`LANGUAGE_NAMES`, `:789`) contains:

```
en, hi, as, bn, brx, gu, kn, ml, mni, mr, or, raj, ta, te,
es, fr, it, ja, pt, zh
```

Any other code is passed through as the code itself, so the LLM may still answer,
but there is no curated prompt name for it. Quality and script correctness must be
tested per language.

The instruction asks the model to translate every sentence, heading, label and
list item, while keeping Meridian University, degree abbreviations, numbers,
currency, email addresses and URLs in Latin script so the grounding boundary can
still match them against the English knowledge base.

### 5.2 STT

| Provider | Languages supported by the adapter | Code |
|---|---|---|
| **Sarvam Saaras v4** | `as, bn, brx, doi, en, gu, hi, kn, kok, ks, mai, ml, mni, mr, ne, or, pa, sa, sat, sd, ta, te, ur` (23) | `app/stt_sarvam.py:89` |
| **IndicConformer** | `hi, gu` only | `app/stt_indic.py:31` |
| **faster-whisper `small`** | Whisper's multilingual set (~99 languages), including every language in `SUPPORTED_LANGUAGES` plus `es, fr, it, ja, pt, zh` | `WHISPER_MODEL=small`; `app/voice_handler.py:2265` |

Live chain: `STT_PROVIDER=sarvam` -> `STT_FALLBACK_PROVIDERS=indic_conformer,whisper`
(`.env` and `app/stt.py`). A provider is only skipped when it raises; a
wrong-language transcript from Sarvam does **not** trigger a fallback.

### 5.3 TTS

| Provider | Languages mapped in code | Notes |
|---|---|---|
| **Sarvam Bulbul v4 Flash** | `hi, en, bn, gu, kn, ml, mr, or, pa, ta, te` (11) | `_LANG_TO_BCP`, `app/tts_sarvam.py:42`; streaming raw 8 kHz linear16 |
| **Kokoro ONNX** | `en, hi, es, fr, it, ja, pt, zh` (8) | Voices at `app/voice_handler.py:226`; phoneme languages at `:243` |
| **AI4Bharat Indic-TTS v1** | `as, bn, brx, gu, hi, kn, ml, mni, mr, or, raj, ta, te` (13) | `app/indic_tts.py:28`; optional local service |

Important routing facts:

- `TTS_PROVIDER=sarvam` routes **only** languages listed in
  `SARVAM_TTS_LANGUAGES` (currently `gu`) to Bulbul. Every other language goes to
  Kokoro.
- `TTS_PROVIDER=indic_tts` is the only way to use Indic-TTS, and it applies to
  **all** Indic languages in `_INDIC_TTS_LANGUAGES`; Sarvam and Indic-TTS cannot
  currently be mixed per language without a code change.
- Kokoro has **no Gujarati voice**. Before Sarvam was wired, Gujarati text was
  read with an English voice, which is why `gu` is the one Sarvam TTS language.
- `SARVAM_TTS_SPEAKER=pooja_gu_customer` currently overrides the per-language
  speaker map for every Sarvam TTS language. `_DEFAULT_SPEAKERS` only has
  personas for `gu`, `hi`, `en`; adding another Bulbul language needs a
  compatible persona ID added to that map (or a per-language override, which
  does not exist yet).
- Kokoro voices exist for the six foreign languages above, but the product does
  not currently select them in the IVR/UI.

---

## 6. End-to-end language readiness

"End to end" here means: STT -> LLM -> TTS plus a way to select the language, a
greeting, and localized fixed fallback lines.

### 6.1 Fully supported today (voice and text)

| Code | Language | STT | TTS | Greeting | Noise/closing fallback | Selection |
|---|---|---|---|---|---|---|
| `en` | English | Sarvam / Whisper | Kokoro | English | English | IVR 1, pinned URL, Streamlit |
| `hi` | Hindi | Sarvam / IndicConformer / Whisper | Kokoro | Hindi | Hindi | IVR 2, pinned URL, Streamlit |
| `gu` | Gujarati | Sarvam / IndicConformer / Whisper | **Sarvam Bulbul** | Gujarati | Gujarati | IVR 3, pinned URL, Streamlit |

### 6.2 The 14 languages in `SUPPORTED_LANGUAGES`

Live value: `en,hi,as,bn,brx,gu,kn,ml,mni,mr,or,raj,ta,te` (`.env`).

`SUPPORTED_LANGUAGES` only controls **auto language switching** in voice; it does
not by itself make a language speakable.

| Code | Language | Sarvam STT | Sarvam TTS map | Kokoro voice | Indic-TTS | Voice today | What is missing |
|---|---|---|---|---|---|---|---|
| `en` | English | yes | yes | yes | no | **yes** | — |
| `hi` | Hindi | yes | yes | yes | yes | **yes** | — |
| `gu` | Gujarati | yes | yes (live) | no | yes | **yes** | — |
| `bn` | Bengali | yes | yes | no | yes | no | speaker persona + `SARVAM_TTS_LANGUAGES` + greeting/fallback/selection |
| `kn` | Kannada | yes | yes | no | yes | no | same |
| `ml` | Malayalam | yes | yes | no | yes | no | same |
| `mr` | Marathi | yes | yes | no | yes | no | same |
| `or` | Odia | yes | yes | no | yes | no | same |
| `ta` | Tamil | yes | yes | no | yes | no | same |
| `te` | Telugu | yes | yes | no | yes | no | same |
| `as` | Assamese | yes | no | no | yes | no | TTS provider routing to Indic-TTS (global switch or per-language routing) + localized lines |
| `brx` | Bodo | yes | no | no | yes | no | same |
| `mni` | Manipuri | yes | no | no | yes | no | same |
| `raj` | Rajasthani | no* | no | no | yes | no | no STT provider reports `raj` (Sarvam/Whisper usually return Hindi) + TTS routing to Indic-TTS + localized lines |

\* `raj` is in the live `SUPPORTED_LANGUAGES`, but neither the Sarvam adapter nor
Whisper reports a `raj` language code. Sarvam is likely to transcribe
Rajasthani speech as Hindi, so auto-switch will select `hi`, and the `raj` TTS
path is never reached.

### 6.3 Other languages Sarvam STT already accepts

These are **not** in `SUPPORTED_LANGUAGES` today. Sarvam STT supports them, but
TTS coverage is incomplete:

| Code | Language | Sarvam TTS map | Voice today | Note |
|---|---|---|---|---|
| `pa` | Punjabi | yes | no | add to `SUPPORTED_LANGUAGES`, `SARVAM_TTS_LANGUAGES` and a persona |
| `ur` | Urdu | no | no | STT only; no TTS voice mapped |
| `sa` | Sanskrit | no | no | STT only |
| `kok` | Konkani | no | no | STT only |
| `ks` | Kashmiri | no | no | STT only |
| `mai` | Maithili | no | no | STT only |
| `ne` | Nepali | no | no | STT only |
| `sat` | Santali | no | no | STT only |
| `sd` | Sindhi | no | no | STT only |
| `doi` | Dogri | no | no | STT only |

The Sarvam TTS adapter maps only 11 languages. The Sarvam API may offer more, but
the code would need a mapping, a compatible persona, and a test before it could
be enabled.

### 6.4 Foreign languages (non-Indian)

Foreign languages are the six Kokoro voices plus anything else the LLM/Whisper
know. The current stack splits them cleanly:

| Language | LLM text | Kokoro TTS | Whisper STT | Voice end to end today | Why not |
|---|---|---|---|---|---|
| Spanish `es` | yes | yes (`ef_dora`) | yes | **no** | Sarvam is tried first and does not support `es`; `SUPPORTED_LANGUAGES` excludes it; no localized greeting/fallback/selection |
| French `fr` | yes | yes (`ff_siwis`) | yes | **no** | same |
| Italian `it` | yes | yes (`if_sara`) | yes | **no** | same |
| Japanese `ja` | yes | yes (`jf_alpha`) | yes | **no** | same |
| Portuguese `pt` | yes | yes (`pf_dora`, pt-BR phonemes) | yes | **no** | same |
| Chinese `zh` | yes | yes (`zf_xiaoxiao`, `cmn` phonemes) | yes | **no** | same |
| German, Russian, Arabic, Korean, etc. | yes (LLM) | **no mapped voice** | yes (Whisper) | **no** | no TTS voice; `_tts_voice_for` would fall back to the English `af_heart` and log a warning |

**Plain answer:** with the current stack, the foreign languages we can support
**end to end in voice are none**. We can support them in **text** today (the LLM
answers; WhatsApp can pass a `Language` code; Streamlit needs its selector
extended). The six languages above are the shortest path to foreign-language
voice because Kokoro already has voices and phoneme maps for them.

### 6.5 Text-only channels

| Channel | Can answer foreign languages? | Constraint |
|---|---|---|
| WhatsApp | yes, if Twilio sends `Language` (or the default is set) | no STT/TTS; `language_instruction` names 20 codes and passes unknown codes through |
| Streamlit | yes, after a one-line selector change | selector currently hard-codes English/Hindi/Gujarati |
| CLI | yes, by prompt only | no language switch |

---

## 7. What it takes to add a language

### 7.1 Config-only path (no code) for `hi`-style Kokoro languages

1. Add the code to `SUPPORTED_LANGUAGES` (voice auto-switch).
2. Ensure `TTS_PROVIDER` routes correctly:
   - Kokoro: nothing to do for `en, hi, es, fr, it, ja, pt, zh`.
   - Sarvam: add the code to `SARVAM_TTS_LANGUAGES` and add a compatible speaker
     persona (code change; see 7.2).
3. Add the greeting to `_INBOUND_GREETING` / `_OUTBOUND_GREETING` and the
   noise/closing lines to `_NOISE_LADDERS` / `CLOSING_REPLY` (code change;
   otherwise English text is spoken in the new voice).
4. Expose selection: IVR digit in `_LANGUAGE_BY_DIGIT` or a pinned
   `?language=<code>`; Streamlit selector list; outbound call-queue language
   (currently not sent).

### 7.2 Code changes required for Sarvam TTS languages beyond `gu`

1. Add the code to `SARVAM_TTS_LANGUAGES`.
2. Add a compatible Bulbul persona to `_DEFAULT_SPEAKERS` in
   `app/tts_sarvam.py:36` (currently `gu`, `hi`, `en` only).
3. Clear or per-language-scope `SARVAM_TTS_SPEAKER`, which currently overrides
   every language.
4. Add localized greeting/fallback lines and a selection path.

### 7.3 Code changes required for foreign-language voice

1. Add the code to `SUPPORTED_LANGUAGES` (or pin it explicitly).
2. Route STT to Whisper for that language: skip Sarvam (which does not support
   it) or add per-language provider routing. Test Whisper `small` on 8 kHz
   telephony before shipping; `medium` is the next quality step if needed.
3. TTS is already available through Kokoro for `es, fr, it, ja, pt, zh`.
4. Add localized greeting/closing/noise lines, or explicitly accept English
   fixed lines.
5. Expose selection (IVR/URL/Streamlit) and pass the language on outbound calls.
6. Verify the grounding boundary: numbers remain enforced, but name checking is
   relaxed for non-English (`GROUNDING_LANGUAGE_MODE=relaxed`).

### 7.4 Adding a language whose TTS provider is Indic-TTS

`TTS_PROVIDER` is global. Today `indic_tts` would take over **all** Indic
languages, so Gujarati would move off Sarvam. To mix providers per language you
need a small code change in `synthesise_stream` / `_synthesise_with_rate` (and
the greeting path) to choose a provider per language instead of one global flag.
The Indic-TTS service also needs the per-language checkpoint downloaded
(`tts_service/download_checkpoints.ps1`; ~1.5 GB per language, ignored by git).

---

## 8. Configuration reference (live values)

| Key | Live value | Meaning |
|---|---|---|
| `LLM_PROVIDER` | `auto` | Resolves to OpenAI; `ollama`/`mlx` are configuration errors |
| `LLM_MODEL` | `gpt-6-luna` | Single model for every LLM call |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible base URL |
| `LLM_MAX_TOKENS` | `192` | Spoken-answer brevity |
| `LLM_REASONING_EFFORT` | `none` | Passed to OpenAI |
| `LLM_PROMPT_CACHE_KEY` | `meridian-kb-v1` | Stable prompt-cache key |
| `LLM_TEMPERATURE` | empty | Omitted (model rejects non-default) |
| `LLM_STREAM` | `1` | Clause streaming for voice |
| `CONTEXT_MODE` | `doc` | Full-document context, no retrieval |
| `DOC_PATHS` | `content/meridian/meridian_knowledge_base.md` | Knowledge base (~15.6 KB / ~3.2k tokens) |
| `DOC_MAX_BYTES` / `DOC_MAX_TOKENS` | `2000000` / `500000` | Guard rails |
| `STT_PROVIDER` | `sarvam` | Primary STT |
| `STT_FALLBACK_PROVIDERS` | `indic_conformer,whisper` | Fallback order |
| `STT_LANGUAGE` | `auto` | Detect and switch language |
| `STT_LANGUAGE_MIN_PROB` | `0.5` | Minimum detection probability to switch |
| `WHISPER_MODEL` | `small` | Multilingual last-resort STT |
| `TTS_PROVIDER` | `sarvam` | Sarvam for `SARVAM_TTS_LANGUAGES`, Kokoro for the rest |
| `SARVAM_TTS_LANGUAGES` | `gu` | Only Gujarati routes to Bulbul today |
| `SARVAM_TTS_MODEL` | `bulbul:v4-flash` | Bulbul model |
| `SARVAM_TTS_SPEAKER` | `pooja_gu_customer` | Overrides per-language personas |
| `SARVAM_TTS_PACE` | `1.1` | Speaking rate |
| `SARVAM_TTS_SAMPLE_RATE` | `8000` | Twilio carrier rate; no resampling needed |
| `INDIC_TTS_URL` | `http://127.0.0.1:8020` | Optional Indic-TTS service |
| `INDIC_TTS_LANGUAGE` | `hi` | Default Indic-TTS language |
| `SUPPORTED_LANGUAGES` | `en,hi,as,bn,brx,gu,kn,ml,mni,mr,or,raj,ta,te` | Auto-switch allow-list |
| `DEFAULT_LANGUAGE` | `en` | Fallback when nothing is selected |
| Keys | `LLM_API_KEY`, `SARVAM_API_KEY`, `TWILIO_*`, `DATABASE_URL` | Never commit `.env` |

---

## 9. Verification notes and caveats

- Full test suite after the current fix: **742 passed**.
- Live call evidence on 2026-10-17:
  - Sarvam STT Hindi/Gujarati ~420-640 ms per utterance.
  - Sarvam TTS Gujarati first audio ~520-590 ms, 8 kHz streaming.
  - Kokoro Hindi first audio ~0.6-1.1 s, 24 kHz.
- Stale comments to ignore when reading the code:
  - `app/pipeline.py` module docstring still says Ollama/Chroma/Qwen; the actual
    service is `OpenAILLMService`.
  - Several "Ollama client" comments in `app/database.py` and
    `app/leads/service.py` are historical; the call is OpenAI.
- WhatsApp audio/voice notes are unsupported by design; the bot asks the user to
  type.
- Outbound calls do not send a language in the call-queue URL; they use
  `DEFAULT_LANGUAGE=en` unless the TwiML URL is built with `language`.
- Fixed lines (`_NOISE_LADDERS`, `CLOSING_REPLY`) exist only in `en`, `hi`, `gu`.
  Any new language will hear English fixed lines until they are localized.
- The LLM is multilingual, but that is not the same as product support. Voice
  support also requires STT route, TTS voice, selection, greeting and fallback
  coverage.

---

## 10. Quick answers

**Which models are used?**
OpenAI `gpt-6-luna` for every text generation; Sarvam Saaras v4 for primary STT;
IndicConformer for offline hi/gu STT; faster-whisper `small` as last-resort STT;
Sarvam Bulbul v4 Flash for Gujarati TTS; Kokoro ONNX for English/Hindi and six
foreign-language voices; AI4Bharat Indic-TTS v1 optional.

**Which model supports which language?**
- LLM: language-agnostic; prompt names 20 codes.
- Sarvam STT: 23 codes (Indian languages + English).
- IndicConformer STT: `hi`, `gu`.
- Whisper STT: ~99 languages.
- Sarvam TTS: 11 codes (Indian languages + English).
- Kokoro TTS: `en, hi, es, fr, it, ja, pt, zh`.
- Indic-TTS: 13 Indic codes.

**Which languages work end to end today?**
Voice: **English, Hindi, Gujarati**.
Text: any language the LLM can answer; the UI exposes English/Hindi/Gujarati in
Streamlit, and WhatsApp accepts a `Language` code.

**Which foreign languages can we support with the current stack?**
- Text today: all, including `es, fr, it, ja, pt, zh`.
- Voice with code/config work: `es, fr, it, ja, pt, zh` (Kokoro TTS + Whisper STT
  + localized fixed lines + selection).
- Voice without adding a TTS voice: no other foreign language.
