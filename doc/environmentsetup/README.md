# Production Re-Architecture — Working Folder

**Read this file first.** It is the entry point for resuming this work from scratch,
with or without the original chat session.

**Last updated:** 2026-08-23

---

## Resuming the Claude Code session

```
Session ID:  9f5b3a8a-1f5a-4dcc-a0e0-51bb57faf2c6
```

From `D:\project\universityDemo`:

```bash
claude --resume 9f5b3a8a-1f5a-4dcc-a0e0-51bb57faf2c6
```

Or `claude -r` to pick from a list, or `/resume` inside a running session.
Transcript: `C:\Users\ADMIN\.claude\projects\D--project-universityDemo\9f5b3a8a-1f5a-4dcc-a0e0-51bb57faf2c6.jsonl`

**The files in this folder are self-sufficient.** If the session is gone or its context has
been summarized, hand `CODEBASE_ANALYSIS.md` + `OPEN_QUESTIONS.md` + this file to any
assistant and the work can continue without re-running the analysis.

---

## The task

> Create a production version of this app: separate each module and make it plug-and-play;
> improve latency for inbound calls, outbound calls, WhatsApp and Streamlit; and build a RAG
> system where the data can be swapped easily so it works for **any** university's data.
>
> To choose the best approach, produce a **detailed, self-contained prompt** that can be
> given to several different LLMs, each of which returns a full architecture plan. The plans
> are then compared and the best one chosen.
>
> The prompt must contain complete system information: what exists today, what its features
> are, and what upgrades are needed. Written from the perspective of a product owner + AI
> expert + prompt engineer.
>
> **Deliverable location:** `d:\project\universityDemo\doc\prod_enhancemnt`

### Design requirements for that prompt (decided, not open)

1. **Self-contained** — receiving LLMs have **no access to this codebase**. Every fact they
   need must be carried in the prompt. `CODEBASE_ANALYSIS.md` is the source for this.
2. **Rigidly structured output** — this is the single most important property. Without a
   mandated response schema, the returned plans cannot be compared. Apples-to-apples beats
   depth.
3. **Explicit decision points** — each plan must take a position and justify it with
   trade-offs, not survey options neutrally.
4. **A scoring rubric** — so the returned plans can be scored objectively rather than by
   vibes. This is what turns "get several plans" into "choose the best one".
5. **Concrete latency baseline** — real numbers from §14 of the analysis, so proposals are
   measurable rather than aspirational.
6. **Explicit non-goals** — to stop scope drift.

---

## Files in this folder

| File | What it is |
|---|---|
| `README.md` | This file — entry point, task brief, status |
| `CODEBASE_ANALYSIS.md` | **The expensive artifact.** Verified current-state analysis of the whole repo with `file:line` citations. Produced by 5 parallel agents over ~21.8k LOC / 218 files. This is the raw material for the RFP prompt. |
| `OPEN_QUESTIONS.md` | 21 decisions needed before the prompt can be written, each with evidence, options and a recommendation. **Currently unanswered.** |

---

## Status

| Step | State |
|---|---|
| 1. Analyze the codebase | ✅ **Done** → `CODEBASE_ANALYSIS.md` |
| 2. Surface open decisions | ✅ **Done** → `OPEN_QUESTIONS.md` |
| 3. Get answers to blocking questions | ⛔ **Blocked on you** |
| 4. Write the multi-LLM RFP prompt | ⬜ Not started → target `doc/prod_enhancemnt/` |
| 5. Send to several LLMs, score, choose | ⬜ Not started |

### To unblock step 4

Answer **Q1, Q2, Q3, Q9, Q12** in `OPEN_QUESTIONS.md` — that is the minimum viable set:

- **Q1** Deploy target (cloud / on-prem GPU / hybrid)
- **Q2** Inference layer (keep local / managed APIs / hybrid)
- **Q3** Tenancy (multi-tenant SaaS / white-label per deploy / single)
- **Q9** Target latency per channel — *the most important number in the whole RFP*
- **Q12** What "any university data" means concretely (formats, source, cadence, size)

The remaining 16 questions sharpen the prompt but do not block it. Alternatively, say
"leave them all open" and the prompt will require each LLM to argue its own position on
every one — which yields more to compare, at the cost of less focus.

---

## The three findings that drive everything

Short version of `CODEBASE_ANALYSIS.md`, in case you only read one section:

1. **`app/main.py` is 2,793 lines and changed in 44% of all commits.** It owns ~50 REST
   routes, 4 WebSockets, the WhatsApp state machine and TwiML. Nothing imports it, so it is
   a god *module*, not a dependency cycle — decomposition is tractable.

2. **Latency is 12–35s per voice turn against a ~1s design goal**, and the causes are
   structural, not tuning: zero streaming anywhere (LLM is `stream=False`; TTS synthesizes
   the *entire* answer before the first byte ships), synchronous psycopg2/Ollama/Twilio/SMTP
   calls made directly inside `async def` handlers, an Ollama `/api/tags` round-trip on
   *every* utterance, and 3–4 sequential 7B inferences run inline in the WebSocket `finally`
   on every hangup. TTS alone is ~80% of the turn.

3. **"Any university data" is blocked by a hard gate, not just strings.** `app/rag.py:78`
   sets `EXPECTED_MARKERS = ["meridian university"]` as a **build-time validation gate** —
   ingesting any other institution's corpus exits 1 and writes nothing. Behind that sit ~40
   hardcoded Meridian references across prompts, canned replies, a program-alias map, STT
   spelling corrections, config defaults, DB seed data and ~10 test files. There is no tenant
   field in chunk metadata and no `where=` filter at retrieval. The one asset: exactly one
   retrieval choke point to refactor against.

Also worth knowing before any production conversation: **no Twilio webhook signature
validation exists anywhere**, **no auth on any `/api/*` endpoint** (including
`/api/demo/reset`, which drops all tables), student documents are served unauthenticated at
guessable URLs, and there is **no CI and no observability of any kind**.
