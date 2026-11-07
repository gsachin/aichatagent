# Languages supported by the chatbot

Verified 2026-10-18 against the live `.env` (not `config.py` defaults) and the
language routing code. This is the operator-facing answer to "which languages
can a caller or chat user actually use?"

## Live settings

| Setting | Live value | Source |
|---|---|---|
| `SUPPORTED_LANGUAGES` | `en,hi,as,bn,brx,gu,kn,ml,mni,mr,or,raj,ta,te,es,fr,it,ja,pt,zh` | `.env:374` |
| `FOREIGN_LANGUAGES` | `es,fr,it,ja,pt,zh` | `.env:377` |
| `DEFAULT_LANGUAGE` | `en` | `.env:373` |
| `STT_PROVIDER` | `sarvam` | `.env:453` |
| `STT_LANGUAGE` | `auto` | `.env:345` |
| `TTS_PROVIDER` | `sarvam` (routes only `SARVAM_TTS_LANGUAGES`) | `.env:387` |
| `SARVAM_TTS_LANGUAGES` | `gu` | `.env:388` |

`SUPPORTED_LANGUAGES` only controls **auto language switching** in voice. It does
not by itself make a language speakable — a language also needs a TTS voice and
localized fixed lines.

## Fully supported end-to-end (voice + text) — 9 languages

| Code | Language | STT | TTS voice | Greeting / fallbacks | Selection |
|---|---|---|---|---|---|
| `en` | English | Sarvam / Whisper | Kokoro `af_heart` | English | IVR 1, pinned URL, Streamlit sidebar/in-chat, WhatsApp menu / `English` / `/en` |
| `hi` | Hindi | Sarvam / IndicConformer / Whisper | Sarvam Bulbul (`aparna_hi_customer`) | Hindi | IVR 2, pinned URL, Streamlit sidebar/in-chat, WhatsApp menu / `Hindi` / `/hi` |
| `gu` | Gujarati | Sarvam / IndicConformer / Whisper | Sarvam Bulbul (`pooja_gu_customer`) | Gujarati | IVR 3, pinned URL, Streamlit sidebar/in-chat, WhatsApp menu / `Gujarati` / `/gu` |
| `es` | Spanish | Whisper | Kokoro `ef_dora` | Spanish | IVR 9 → 1, pinned URL, Streamlit sidebar/in-chat, WhatsApp menu / `Spanish` / `/es` |
| `fr` | French | Whisper | Kokoro `ff_siwis` | French | IVR 9 → 2, pinned URL, Streamlit sidebar/in-chat, WhatsApp menu / `French` / `/fr` |
| `it` | Italian | Whisper | Kokoro `if_sara` | Italian | IVR 9 → 3, pinned URL, Streamlit sidebar/in-chat, WhatsApp menu / `Italian` / `/it` |
| `ja` | Japanese | Whisper | Kokoro `jf_alpha` | Japanese | IVR 9 → 4, pinned URL, Streamlit sidebar/in-chat, WhatsApp menu / `Japanese` / `/ja` |
| `pt` | Portuguese | Whisper | Kokoro `pf_dora` | Portuguese | IVR 9 → 5, pinned URL, Streamlit sidebar/in-chat, WhatsApp menu / `Portuguese` / `/pt` |
| `zh` | Chinese | Whisper | Kokoro `zf_xiaoxiao` | Chinese | IVR 9 → 6, pinned URL, Streamlit sidebar/in-chat, WhatsApp menu / `Chinese` / `/zh` |

The six non-Indian languages are gated by `FOREIGN_LANGUAGES`. Setting it to an
empty string disables the whole feature (IVR second menu, WhatsApp commands,
Streamlit selector, foreign greetings) without a code change.

Japanese and Chinese need the misaki G2P stack; without it those voices fall
back to espeak-ng and are not intelligible. The boot check reports this as a
degraded condition.

## In `SUPPORTED_LANGUAGES` but not speakable yet — 11 codes

These are only on the voice **auto-switch allow-list**. They have no enabled
TTS routing/persona and no localized greetings/fallbacks, so the bot does not
speak them today.

| Code | Language | What is missing |
|---|---|---|
| `bn` | Bengali | speaker persona + `SARVAM_TTS_LANGUAGES` + localized lines |
| `kn` | Kannada | same |
| `ml` | Malayalam | same |
| `mr` | Marathi | same |
| `or` | Odia | same |
| `ta` | Tamil | same |
| `te` | Telugu | same |
| `as` | Assamese | TTS provider routing to Indic-TTS + localized lines |
| `brx` | Bodo | same |
| `mni` | Manipuri | same |
| `raj` | Rajasthani | no STT provider reports `raj` (Sarvam/Whisper usually return Hindi); TTS routing + localized lines |

## Other languages

- **Sarvam STT accepts more** (`pa`, `ur`, `sa`, `kok`, `ks`, `mai`, `ne`,
  `sat`, `sd`, `doi`), but they are not in `SUPPORTED_LANGUAGES` and have no
  enabled TTS voice. Punjabi (`pa`) is the closest to ready: Sarvam has a TTS
  map; it needs a persona, `SARVAM_TTS_LANGUAGES` entry and localized lines.
- **The LLM/Whisper know many languages** (German, Russian, Arabic, Korean,
  etc.). The bot can *understand* them, but there is no mapped voice — TTS
  falls back to the English `af_heart` and logs a warning — and they are not
  selectable. They do not count as supported.

## Text-only channels

| Channel | Foreign languages | Constraint |
|---|---|---|
| WhatsApp | yes, for the six | A number without an explicit language choice gets a numbered menu (English, Hindi, Gujarati + the enabled foreign languages); the choice is persisted on the conversation with its source and returning numbers skip the menu. Legacy implicit-default rows are asked once. Whole-message commands (`Spanish`, `/es`, `/hi`, …) change it later; `/languages` lists them. Deterministic chat prompts (greeting, name, email, program, documents, offer responses, fallbacks) and RAG answers are localized (`app/whatsapp_text.py`); proactive offer-PDF/voice-path messages remain English in v1. |
| Streamlit | yes, for the six | The sidebar selector and the in-chat picker (shown until the first message) both list the six when enabled; RAG answers and main onboarding prompts are localized. |
| CLI | yes, by prompt only | No language switch. |

## Where this is enforced

- `app/languages.py:32` — foreign language list, normalization, command aliases
- `app/main.py:555-561` — IVR digit → language map
- `app/voice_handler.py:237-246` — Kokoro voice map
- `app/voice_handler.py:2636-2679` — auto-switch rules (`SUPPORTED_LANGUAGES` enforcement)
- `app/config.py:534-543` — `SUPPORTED_LANGUAGES`, `FOREIGN_LANGUAGES`
- `doc/API_MODEL_LANGUAGE_MATRIX.md:257-344` — full STT/TTS matrix per language

## How to add a language

1. Add a TTS voice/persona and route the code to a provider that has a voice
   (`SARVAM_TTS_LANGUAGES` or the Kokoro map).
2. Add the code to `SUPPORTED_LANGUAGES` (auto-switch) and, for the six-style
   treatment, to `FOREIGN_LANGUAGES`.
3. Add localized greetings, noise ladder, closing and failure strings.
4. Add a selection path (IVR digit, WhatsApp first-contact menu/alias,
   Streamlit sidebar/in-chat picker).
5. Build call assets: `.venv/Scripts/python.exe scripts/build_call_assets.py`.
6. Run the boot readiness check and confirm the language is not reported as
   degraded.
