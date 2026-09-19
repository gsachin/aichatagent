> **Lens:** BA · **Decided by:** BA, confirmed by PO · **Inputs:** `00-product-intent.md` · **Engagement:** Brownfield — `D:\project\universityDemo` · **Defines:** BRD-01 … BRD-21

# BRD — Admissions Voice Assistant Performance & Concurrency Program

## 0. Discovery Matrix — first completeness gate

| Dimension | Questions | Evidence | Gaps |
|---|---|---|---|
| Personas | Who uses it? | Document-observed — `00-product-intent.md` §3: caller, counselor, operator, developer | None |
| Actors | Who interacts with the system? | Code-observed — Twilio (PSTN + Media Streams), caller, counselor (handoff), operator, Salesforce CRM `:8098`, Ollama `:11434`, ERC MCP `:8010` (`app/main.py`, `app/rag_mcp.py`) | Counselor-side UI not inspected — handoff is outbound only |
| Business goals | Why does it exist? | Document-observed — `00-product-intent.md` §1–§2 | None |
| Capabilities | What must the system do? | Derived — BRD-01…21 below, from the 28-intent surface (`09-intent-catalog.md`) + the performance investigation | Intent coverage of the golden set is not yet proven (`DG-03`) |
| Workflows | How does work move end-to-end? | Code-observed — `main.py:1100→1117→495` IVR → TwiML → WS; per-frame `main.py:624–630`; `voice_handler.py:374` `process_utterance` | **Two voice entry paths exist** (`voice_handler` live; `pipeline.py` dead) — which is authoritative is resolved, but `/ws/voice/text` shares code |
| States | What states can entities enter? | Code-observed — call lifecycle via `_active_call_sids`, `session.log_event`, `end_call`; lead lifecycle in `app/leads/models.py` | No explicit call state machine in code — must be derived in Stage 4 |
| Data | What data is required? | Code-observed — `.env:107` `CHROMA_DB_PATH`; `content/meridian/meridian_knowledge_base.md` (11,214 bytes, 16 sections); Postgres leads; `logs/perf_turns.jsonl` (new) | **Two divergent KB stores** — local `langchain` (~47 recs) vs ERC `meridian-kb` (37 chunks); reconciliation undecided |
| Integrations | What external systems exist? | Code-observed — Twilio (telephony), Salesforce CRM API `:8098`, Ollama, ERC MCP, Postgres, Redis Stack, SMTP | **Twilio RTT unmeasured** — the one hop the local harness cannot see |
| Security | What can go wrong? | Code-observed — student PII + CRM data; `MUTE_STT_DURING_TTS` drops caller audio; `_tts_cache` is class-level and shared across concurrent calls | Cross-call audio-cache sharing not yet proven safe under N=2 |
| Failure | What happens when dependencies fail? | Code-observed — `rag.py:103–114` MCP→legacy fallback; `rag_mcp.py:270–301` binary breaker, 30 s, no half-open probe; failures cost the 2.5 s timeout **plus** a full re-retrieval | Failure behaviour at N=2 (one caller's dependency timeout vs the other's call) untested |
| Scale | What happens at 10×/100×? | Derived — measured: 448 GB/s ceiling, decode at 77% of it, VRAM ~90% at N=2. 10× is not achievable on this box at any quality | **Arrival rate unmeasured (AS-01, AS-06)** — peak concurrency is an assumption |
| Compliance | What constraints exist? | User-provided — student leads and personal data; hosted inference prohibited on residency grounds (`AS-03`) | Retention/consent policy for call recordings not stated |
| Operations | How is it monitored/recovered? | Code-observed — `start_services.ps1` orchestration, `/health`, `voice_events` structured log, two Streamlit dashboards | **No per-stage latency metric exists today**; no alerting on degradation |

## 1. Objectives

| # | Objective | Traces to |
|---|---|---|
| O1 | Make per-turn latency measurable per stage, so decisions rest on measurement | North Star + "per-stage visibility" guardrail |
| O2 | Reduce turn latency to the tightest defensible SLO, or prove the target unreachable with arithmetic | North Star + p95 guardrail |
| O3 | Sustain two simultaneous independent callers without degradation | 2-caller guardrail |
| O4 | Hold answer quality at or above the current baseline on all critical intents | quality guardrail |
| O5 | Keep the fix reversible and the config honest | operability |

## 2. Scope

| In scope | Out of scope |
|---|---|
| Per-stage instrumentation of the live voice turn | Replacing the application architecture |
| Streaming LLM + TTS where the runtime supports it | Hosted inference of any kind |
| Endpointing/prefix-cache/cold-start optimisation | Changing the 28-intent behaviour surface |
| De-serializing retrieval for concurrency | Multi-region or cloud deployment |
| Frozen golden set + regression gate | Chat/WhatsApp path optimisation |
| Reproducible N=1/N=2 load harness | New user-facing features |
| Model/quantization change **only** if gates demand it | Barge-in (Class C — separately signed off) |

## 3. Stakeholders

| Stakeholder | Role | Responsibility | Interest |
|---|---|---|---|
| Product Owner (user) | Decision maker | Approves SLO, scope, Class C changes, golden-set ground truth | Predictable call quality; demo reliability |
| Caller | End user | — | Fast, correct, interruptible conversation |
| Admissions counselor | Handoff recipient | Picks up warm leads | Context completeness |
| Operator | Runs the stack | Keeps 8 services alive | Early warning; one-command start |
| Developer | Maintains code | Implements + reverts changes | A codebase that doesn't lie about what runs |

## 4. Business Requirements

### BRD-01 — Per-stage turn latency is measured, not inferred
Every voice turn shall emit a machine-readable record containing, at minimum, the elapsed time for endpointing, transcription, retrieval, generation, synthesis and first-audio-to-carrier, plus the inference engine's own prompt and generation counters.

### BRD-02 — Turn latency target
The system shall meet p50 ≤ 700 ms and p95 ≤ 1,200 ms. Where measurement proves this unreachable on the specified hardware, the program shall state the tightest defensible SLO with its arithmetic and obtain PO approval — never silently relax the target.

**One canonical clock.** The metric above is `TURN_E2E_MS`, defined exactly once, as:

```
TURN_E2E_MS = first_audio_frame_written_to_carrier - caller_speech_end_reference
            = endpointing_ms + processing_ms
  where endpointing_ms = vad_end              - caller_speech_end_reference
        processing_ms  = first_audio_frame   - vad_end
```

Every other artifact measures **processing** time from `vad_end` (the end-of-speech trigger), which excludes the ~600 ms endpointing floor. That is a legitimate sub-metric but it is **not** `TURN_E2E_MS`, and the two must never be reported under the same name. Any figure quoted against this requirement states which clock it used.

### BRD-03 — Cold-start first turn
The first turn of a call arriving after a long idle period shall fall within the same p95 as a warm turn.

### BRD-04 — Endpointing reconciliation
The system shall have exactly one live end-of-speech decision, its delay documented, and its value reconciled explicitly against BRD-02. Configuration that does not execute shall not be presented as the live setting.

### BRD-05 — Two simultaneous callers
The system shall serve two concurrent inbound voice calls, each meeting BRD-02, with no single turn exceeding 3 seconds. This requirement is decomposed three ways, because "caller B unaffected by caller A" is not achievable on one shared GPU and is therefore not a testable requirement:

| Dimension | Requirement | Nature |
|---|---|---|
| **Isolation** | No caller receives another's history, context, prompt, cached audio or session metadata | **Absolute** — binary, zero tolerance (`BRD-06`) |
| **Performance interference** | p95 at N=2 ≤ 1.5× p95 at N=1 per caller; no turn > 3 s | **Bounded degradation** |
| **Resource interference** | No OOM, no sysmem spill, no core pinned by the event loop, no caller starved in a queue | **Threshold** (`BRD-11`, `BRD-12`) |

Only the first is absolute. The second and third are quantitative budgets, and a run that meets them is a pass even though caller B is measurably slower than caller A alone — that is what sharing one GPU costs, and pretending otherwise would make the gate unfalsifiable.

### BRD-06 — Caller isolation
A caller shall never receive another caller's conversation history, retrieved context, prompt, cached audio or session metadata. Isolation shall be demonstrated under concurrent load, not asserted.

### BRD-07 — Retrieval does not serialize callers
A slow or hung retrieval for one caller shall not delay another caller's turn.

### BRD-08 — Frozen quality baseline
Before any change that alters what the model sees or says, a task-representative evaluation set shall be frozen and scored against the current configuration, with ground truth for fees, deadlines, eligibility and escalation approved by the Product Owner.

### BRD-09 — No quality regression, judged by non-inferiority
No change shall be adopted that regresses a critical intent (fees, deadlines, eligibility, escalation, lead capture). Aggregate scores may not improve at the cost of any single critical intent.

**Non-inferiority, not a point margin.** A fixed "≤2 point aggregate drop" rule is unsound on a finite evaluation set: a 1–2 point movement may be evaluation noise rather than a real regression, and it gives no way to tell the two apart. Adoption is therefore decided by a **non-inferiority test** on the frozen set, reported per intent:

- each intent reports **baseline score, candidate score, sample count, delta, and a confidence interval**;
- deterministic failures (missing required fact, forbidden claim present, wrong escalation) are **absolute blockers**, outside the statistical comparison;
- judge-versus-deterministic disagreements and human-versus-judge disagreements are reported separately and never averaged away;
- the candidate is adopted only if the critical-intent non-inferiority bound is met **and** no deterministic failure is introduced.

Where the evaluation set is too small to support a confidence interval for an intent, that intent is reported as **underpowered** and its result cannot be used to justify adoption — which is itself a reason to grow the set before tuning against it.

### BRD-10 — Retrieval grounding
The system shall define, and enforce, a threshold below which retrieved context is treated as not relevant, so the assistant says it does not know rather than answering from an unrelated chunk.

### BRD-11 — VRAM budget at two callers
Peak VRAM with two concurrent calls shall remain at or below 90% of device memory, with no silent spill into shared system memory.

### BRD-12 — CPU and RAM budget
Sustained CPU shall remain at or below 80% and RAM at or below 80% with two concurrent calls; no single core shall be pinned by the event loop.

### BRD-13 — Graceful degradation, per a defined failure-capability matrix
Failure of any dependency shall degrade to the **best response the surviving components can still produce**, and shall never fail the other caller's call. "Polite spoken fallback" is **not** achievable for every failure — if the text-to-speech path is what failed, nothing can speak — so this requirement is specified as a matrix rather than an absolute.

| Failed component | Fallback | Can still speak? |
|---|---|---|
| Retrieval (ERC/MCP) | Local store, then keyword-only | Yes |
| Vector store | Keyword-only retrieval; answer ungrounded with disclosure | Yes |
| Inference engine (LLM) | Deterministic fixed response; **needs `US-016`-adjacent admission policy** — see scope note | Yes |
| Speech synthesis (TTS) | Alternate execution provider (CPU), then **pre-synthesised critical audio** | Only via pre-synthesised audio |
| Speech recognition (STT) | Fixed clarification prompt | Yes |
| LLM **and** TTS | **Pre-synthesised critical audio only**; otherwise clean call termination | Only pre-synthesised |
| Database / cache | Continue the call; queue persistence | Yes |
| CRM | Local outbox; retry | Yes |
| Carrier / whole app | Call termination; carrier-level fallback | No |

**Scope note — the contradiction this resolves.** The implementation plan records that a total inference-engine loss has **no fallback** and classes that as an accepted gap (`04-coverage-gap-analysis.md` Q7, owned by TPO). That cannot coexist with an unqualified "every dependency produces a spoken fallback". This BRD now requires a fallback for **every row above that is physically capable of one**, and requires the inference-engine row to be satisfied by a deterministic fixed response plus a pre-synthesised critical-audio path — which is a build item, not an accepted gap. `US-016` owns the policy; the pre-synthesised audio set is a dependency of it.

### BRD-14 — Bounded timeouts and recovery
Every outbound dependency call shall carry a timeout justified against the latency budget. A tripped circuit shall probe for recovery rather than paying the timeout on every subsequent request.

### BRD-15 — Reversible change
Every adopted change shall be revertible by configuration or a single revertable commit, with a documented rollback.

### BRD-16 — Single source of configuration truth
Configuration that is written but never read shall be removed or wired; the effective value of every runtime setting shall be unambiguous.

### BRD-17 — Boot-time readiness
The inference model and its prompt prefix shall be resident before the first call is accepted, so that the first call after readiness meets `BRD-03`. GPU clock state is an **implementation means, not a product invariant**: it is monitored as a diagnostic, but the requirement is the user-facing outcome (first turn within the p95), never a mandated clock state.

### BRD-18 — Behaviour surface preserved
All 28 supported caller intents defined in `09-intent-catalog.md` shall remain functional after optimisation, judged on end-to-end task success and not on per-stage text quality alone. That catalog is the authoritative definition of the set; the **twelve** Critical intents listed there are non-negotiable under `BRD-09`.

> **Correction.** This requirement previously said "eleven", while the catalog table marks **twelve** rows Critical. The table is authoritative — the count is twelve. Recorded rather than silently reconciled because the mismatch mattered: the golden set was built against the table, and a reader trusting the prose would have expected one fewer protected intent.

### BRD-19 — Interruption behaviour is a decision, not an accident
The system shall either support caller interruption or explicitly document that it does not and why; instructions in the agent's prompt that describe unreachable behaviour shall be removed or the behaviour implemented.

### BRD-20 — Voice takes priority over background load
The system shall sustain **two concurrent voice calls plus one background chat/admin request** — the operating scenario in which the same GPU and CPU serve both. Under that mix:

- both voice callers continue to meet `BRD-02` and `BRD-05`;
- the **background request degrades first** and may be queued or slowed without limit, because it has no human waiting on a live audio stream;
- no caller is starved by background work, and the background request is never served at the expense of a voice turn.

This scenario is an **acceptance condition, not a footnote**: it is the realistic peak for this box, and a configuration that passes at N=2 voice alone while collapsing at N=2 + 1 chat has not met the requirement. Priority is enforced by policy, not by hope — a background request that cannot be admitted within its budget is deferred, not interleaved into a caller's turn.

### BRD-21 — The RAG configuration ships in two stages
The system shall define and deliver **two RAG configurations**, so that a working configuration exists from day one while final tuning remains gated on evidence:

| | RAG-Baseline | RAG-Optimized |
|---|---|---|
| **What it is** | The current production configuration, characterised and safely frozen | The candidate produced by the retrieval evaluation (`US-015` funnel and the RAG sweep) |
| **When it applies** | Immediately — it is what runs today | After retrieval metrics and the frozen golden set agree |
| **Inputs** | `TOP_K=5`, `FETCH_K=20`, 600/90 chunking, hybrid on, reranker unused, threshold disabled | Decided from recall@k, MRR, nDCG, tokens injected and latency |
| **Adoption rule** | Already in force | Must pass `BRD-09`'s non-inferiority gate |

**The point of the split** is that `DG-03` (no golden set) genuinely blocks final RAG tuning, and without this split the whole program waits on it. `RAG-Baseline` is honest about being unoptimised rather than pretending the current values were chosen; `RAG-Optimized` is what the evidence later selects. Neither is the "final answer" by default (`00-product-intent.md` §6b).

## 5. Business Rules

| Rule | Condition → Action | Source |
|---|---|---|
| Critical intents are non-negotiable | Change regresses fees/deadlines/eligibility/escalation/lead-capture → reject regardless of aggregate gain | PO |
| Quality gate precedes speed | Any change altering model input or output → frozen-set evaluation before adoption | PO / performance discipline |
| Local inference only | Any proposal requiring a hosted model → rejected on residency grounds unless the PO reclassifies | PO (AS-03) |
| Behaviour-changing work needs sign-off | Change alters user-perceivable turn-taking or model identity → PO sign-off before adoption | PO |
| One variable at a time | Experiment changes more than one coupled variable → result is not attributable; split it | Performance discipline |
| Measurement beats consensus | A number about this machine comes from this machine; model agreement is not evidence | Performance discipline |

## 6. Evidence Register

| Fact | Value | Classification | Evidence |
|---|---|---|---|
| AS-01: peak concurrency assumed 2 | 2 concurrent callers | Assumed | `00-product-intent.md` §7 — validate in Stage 5 |
| AS-02: quality bar = current behaviour | no-regression | Assumed | pending frozen golden set |
| AS-03: local-only inference | hosted prohibited | User-provided | product intent §6 |
| AS-04: 700 ms may be unreachable | 600 ms endpointing floor alone = 86% of budget | Derived | measured endpointing + budget arithmetic |
| AS-05: golden-set ground truth needs a human | not yet supplied | Assumed | Stage 5 validation target |
| AS-06: seasonal bursts above 2 | shape unmeasured | Unknown | — |
| AS-07: decode is bandwidth-bound | 38.5 of 49.8 tok/s = 77% of 448 GB/s | Derived | measured on this box, 2026-09-18 |
| AS-08: two KB stores reconcilable | local ~47 recs vs ERC 37 chunks | Assumed | `chroma.sqlite3` / `chroma_data` string-grep |
| Endpointing floor | 600 ms (30 frames × 20 ms, RMS gate) | Code-observed | `voice_handler.py:282, 341, 367` |
| Pipecat VAD setting | inert — never executes | Code-observed | `pipeline.py:174–180`; analyzer never referenced |
| System prompt size | 3,558 tokens static | Derived (measured via Ollama counters) | `prompt_eval_count`, 2026-09-18 |
| Prompt with 5 RAG chunks | 5,666 tokens | Derived (measured) | same |
| Prefill rate | 1,588–1,990 ms; uncached ≤4,291 tok/s | Derived (measured) | same |
| Decode rate | 36.6–38.9 tok/s | Derived (measured) | same |
| Cold model load | 32,919 ms; 67,349 ms to first token | Derived (measured) | same |
| **Prefix caching works** | identical repeat 2,909 ms → 50 ms at same token count | Derived (measured) | `doc/perf/tools/a4_prefix_cache_probe.py`, 2026-09-18 |
| Prefix cache break point | at `{context}` insertion (`voice_system_prompt.py:600`) | Derived (measured) | same — changed-context case |
| LLM call is non-streaming | full completion returned before TTS | Code-observed | `llm_backend.py:145` |
| TTS uses batch API | `create()`, not `create_stream()` | Code-observed | `voice_handler.py:673` |
| FastAPI process count | 1 — `FASTAPI_WORKERS` never read | Code-observed | `start_services.ps1:559`; refs only in `hardware_profile.py` |
| ERC workers | 1, no `workers` argument | Code-observed | `enterprise-rag-core/cli.py:83` |
| ERC hybrid legs | synchronous CPU-bound in one event loop | Code-observed | `hybrid.py:141–144` |
| Reranker | loaded at boot, never invoked | Code-observed | `config.py:196–198`; `execute_agent_context` absent from repo |
| Semantic cache | configured, bypassed | Code-observed | same |
| Relevance threshold | `RAG_SIMILARITY_THRESHOLD=0.0` — gate disabled | Code-observed | `rag.py:163` |
| Context trimming | `RAG_MAX_CONTEXT_CHARS=0` — warn only, never trims | Code-observed | `rag_legacy.py:337` |
| Barge-in | impossible — caller audio discarded during TTS | Code-observed | `main.py:624–627`, `MUTE_STT_DURING_TTS` default on |
| GPU bandwidth | 448 GB/s (GDDR7, 128-bit, 28 Gbps) | Verified-source | vendor spec sheets |
| Cold-start idle state | P5, memory clock 405 of 14,001 MHz | Derived (measured) | `nvidia-smi`, 2026-09-18 |

## 7. Assumptions

- `Assumed: peak concurrency is 2` — AS-01; no arrival-rate measurement exists.
- `Assumed: the quality bar is no-regression` — AS-02; pending a frozen set.
- `Assumed: hosted inference stays prohibited` — AS-03; user-provided but re-confirmable.
- `Assumed: 700 ms may be unreachable` — AS-04; arithmetic supports this today.
- `Assumed: a human supplies critical-intent ground truth` — AS-05; on the critical path.
- `Assumed: admissions bursts exceed 2 callers` — AS-06; shape unknown.
- `Assumed: the two KB stores can be reconciled` — AS-08; content overlap unverified.

## 8. Constraints

- Fixed hardware: RTX 5060 Ti 16 GB (448 GB/s), Ryzen 5 3500 (6c/6t, no SMT), 32 GB RAM.
- 8 co-resident services on one Windows box.
- Student PII and CRM data — no hosted inference (AS-03).
- Windows/PowerShell toolchain; no Linux migration assumed.
- Existing 28-intent behaviour surface must survive (BRD-18).
- Twilio telephony hop cannot be removed, only measured.

## 9. Dependencies

- Twilio Media Streams (telephony + framing) — external, RTT unmeasured.
- Salesforce admission API `:8098` — **post-call and fire-and-forget only; never on the first-audio path.** This line previously read "inline on some turns", inherited from an earlier system description; the code contradicts it (`main.py:655–661` spawns CRM linking as a background task that is never awaited), and `06-architecture.md` §2 confirms MOD-05 runs strictly post-call. The earlier phrasing would have wrongly pulled CRM latency into the per-stage budget and the failure matrix. Evidence: Code-observed.
- Ollama `:11434` — inference + embeddings; no model resident at rest.
- ERC MCP `:8010` — retrieval; single-threaded.
- Postgres + Redis Stack via Docker.
- `content/meridian/meridian_knowledge_base.md` — single source for both KB stores.

## 10. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| 700 ms p50 is arithmetically unreachable | H | M | BRD-02's evidence clause: propose the tightest defensible SLO |
| VRAM at N=2 (~90%) breaches BRD-11 | H | H | KV-cache quantization; model sweep as a gated fallback |
| Quality gate blocked by missing ground truth | M | H | Raise early (AS-05); human task on the critical path |
| Streaming change under-delivers vs prediction | M | M | Phase gate: stop and debug if measured gain < 50% of predicted |
| Seasons burst above 2 callers (AS-06) | M | H | Measure arrival rate; document the N=3 break point |
| Config drift misleads the work | M | M | BRD-16: treat "set" ≠ "live" as a standing rule |

## 11. Success Criteria

1. A real call produces a per-stage trace with ≥6 stages plus engine counters, with `retrieval_ms ≥ 0` and the decomposition reconciling to within 5 ms (BRD-01).
2. p50/p95 measured over ≥100 turns per condition, cold and warm reported separately (BRD-02, BRD-03).
3. Two concurrent callers each within 1.5× of their solo p95, no turn > 3 s, on three consecutive runs (BRD-05, BRD-07).
4. Zero cross-call contamination observed under concurrent load (BRD-06).
5. Frozen golden set shows no critical-intent regression and ≤2 pt aggregate movement (BRD-08, BRD-09).
6. Peak VRAM ≤ 90% at N=2 with no sysmem spill; CPU/RAM ≤ 80% (BRD-11, BRD-12).
7. One dependency killed mid-call yields a polite fallback and does not disturb the other caller (BRD-13).
8. Every adopted change has a one-command rollback, demonstrated (BRD-15).

## 12. Compliance / Regulatory Notes

Student leads and Salesforce CRM data are personal data. Hosted inference is excluded on residency grounds (AS-03). Retention and consent policy for call transcripts is **not stated in the repo** and is recorded here as an open item, not invented — if transcripts are persisted, a retention rule is required (`app/database.py:224–258` writes transcripts to Postgres).
