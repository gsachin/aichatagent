# Low-Latency System Sizing — Reusable LLM Prompt

**Created:** 2026-09-17
**Purpose:** A self-contained prompt to paste into multiple LLMs (ChatGPT, Gemini,
Claude, etc.) to get a hardware + configuration spec for running this app's voice
and chat paths at natural conversational latency.

**Why several LLMs:** the answers are meant to be diffed. Agreement between
independent models is a signal the numbers are real; disagreement flags where
judgment (not fact) is required. The prompt's fixed output schema exists so
answers land in the same tables and can be compared row by row.

**Related:** `doc/model_vram_analysis.md` (VRAM budget for the 6 GB tier),
`doc/CLOUD_INFRASTRUCTURE_PLAN.md`, `doc/INFRASTRUCTURE_PLAN.md`

---

## The prompt

Copy everything inside the block below, verbatim, into a fresh LLM session.

```
ROLE
You are a real-time voice AI systems engineer who has shipped production
telephony agents. You give measured, numeric answers, not architecture essays.

CONTEXT
I run a university admissions AI assistant. It handles inbound phone calls via
Twilio and a web chat widget. Current stack:

  - FastAPI app on :8000 (public entry point)
  - Voice pipeline: Pipecat, Twilio Media Streams over WebSocket
  - STT: faster-whisper (currently small.en)
  - TTS: kokoro-onnx
  - LLM: Ollama serving qwen2.5:14b, num_ctx=8192
  - Retrieval: ChromaDB locally, or enterprise-rag-core MCP service on :8010
    (JSON-RPC over HTTP), which the app falls back from automatically
  - CRM: a Salesforce admission API on :8098, called inline during some turns
  - Postgres + pgvector, Redis Stack
  - Two Streamlit dashboards
  - All of the above currently run on one Windows box

Reference machine this currently runs on, and where it is NOT fast enough:
  - 6 CPU cores, 32 GB RAM, NVIDIA RTX 5060 Ti with 16 GB VRAM
  - Calls work but the turn-taking feels sluggish

TASK
Produce a complete system configuration specification that would make turn
latency feel natural to a caller, and state what hardware and settings achieve
it.

DEFINE LATENCY AS
"Turn latency" = the moment the caller stops speaking to the moment they hear
the first syllable of the response. It includes VAD endpointing silence, STT,
retrieval, LLM time-to-first-token, TTS time-to-first-chunk, and network
round trip on a mobile connection.

Target: p50 <= 700 ms and p95 <= 1200 ms.

CONSTRAINTS
- Do not answer that zero latency is impossible and stop. Assume the target
  above is the goal and work out what gets closest to it.
- Every number must have a stated basis (measured benchmark, published spec, or
  an explicitly labelled estimate). Mark which is which.
- Consider both a self-hosted GPU and moving components to a hosted API. Do not
  assume self-hosting.
- Account for VRAM arithmetic explicitly: LLM weights + KV cache at the stated
  context + STT + TTS, all co-resident.
- State your assumption about concurrent calls, and size for it.
- Do not propose rewriting the application architecture without stating the
  migration cost in engineer-days.
- If you are uncertain about a specific number, say so rather than inventing
  precision.

OUTPUT FORMAT — use exactly these seven sections, in this order.

1. LATENCY BUDGET
   Table with columns: Stage | p50 (ms) | p95 (ms) | What sets the floor |
   How to reduce it
   Rows, one per stage: VAD endpointing, STT, retrieval, LLM TTFT,
   LLM generation to first sentence, TTS first chunk, network round trip.
   Final row: TOTAL.

2. HARDWARE SIZING
   Table: Component | Minimum viable | Recommended | Why this and not less

3. CONFIGURATION
   Table: Parameter | Value | Effect on turn latency
   Cover at minimum: model and quantization, context length, GPU vs CPU
   placement per component, batching/concurrency settings, keep-alive or
   preload settings, and any VAD/endpointing tuning.

4. THREE OPTIONS COMPARED
   Table: Option | Turn latency p50 | Monthly cost | Answer quality | Migration
   cost (engineer-days) | Best for
   Rows: (a) all self-hosted GPU, (b) hybrid — some components hosted API,
   (c) all hosted API.

5. CONCURRENCY CEILING
   How many simultaneous calls before the p95 target is breached, on your
   recommended config. State the binding constraint (VRAM, GPU compute,
   network, or something else).

6. TOP 3 CHANGES, RANKED
   The three highest-leverage changes to the CURRENT setup, ranked by
   latency gained per unit of effort. One line each on what it buys.

7. CONFIDENCE AND UNKNOWNS
   Your confidence in each of the seven sections (high/medium/low), and the
   two facts you would need to raise a "low" to "high".

Do not add sections, preamble, or closing summary.
```

---

## Why the prompt is shaped this way

If you edit it, these parts are load-bearing — removing them changes what you get
back:

| Element | What it prevents |
|---|---|
| The numeric p50/p95 target | Without it each model invents its own idea of "fast", so the answers can't be compared. This is the whole reason the prompt works across multiple LLMs. |
| "Do not answer that zero latency is impossible and stop" | The most likely failure mode — the model treats the impossible premise as the question and returns an essay instead of a spec. |
| Naming VAD endpointing as its own budget row | The hidden 300–500 ms most models omit, and often the cheapest thing to fix. |
| The 16 GB / 6-core baseline | The calibration point. Without it, advice is unanchored and tends toward generic cloud recommendations. |
| Basis-labelling (measured / spec / estimate) | Separates real benchmarks from invented precision — the main risk when polling several models on numbers. |
| Requiring the hybrid and all-API rows | Stops every model from defaulting to the deployment style it happens to prefer. |
| Migration cost in engineer-days | Stops "just move it to an API" from reading as free. |

---

## Sanity-checking the answers

1. **Cold-start test** — on a fresh session, does it produce all seven sections
   without asking what the app is? If it asks a clarifying question, Context is
   still too thin.
2. **Done-state test** — hand one output to someone who didn't see the prompt and
   ask them to price the "Recommended" column. If they can't, Hardware Sizing is
   too vague to act on.
3. **Drift test** — run it on two models and check whether both put a number in
   the VAD endpointing row. A blank or merged-into-STT row is the clearest sign
   the model free-associated instead of following the output spec.

## What to expect

Models will disagree most on **whether to keep the 14B model**. That's the real
fork: a 14B co-resident with whisper and kokoro on a 16 GB card is the dominant
term in the latency budget. Some models will argue for dropping to 7B, others for
moving the LLM to a hosted API. Neither is wrong — the disagreement is the useful
output, not a flaw in the prompt.
