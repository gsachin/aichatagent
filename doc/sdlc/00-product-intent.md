> **Stage 0 — Engagement [Lens: all]** · **Decided by:** all lenses (classification + scope mode) · **Engagement:** Brownfield — repo at `D:\project\universityDemo`, stack Twilio + Pipecat 1.6.0 (installed, bypassed on the live path) + faster-whisper + Ollama/qwen2.5:14b + Chroma/ERC-MCP RAG + Kokoro TTS, 8 services on one Windows box (evidence: `app/voice_handler.py`, `app/main.py`, `app/pipeline.py`, `app/rag.py`, `app/rag_mcp.py`, `app/rag_legacy.py`, `app/llm_backend.py`, `app/voice_system_prompt.py`, `.env`, `.machine_profile.json`, `start_services.ps1`, `D:\project\enterprise-rag-core`) · **Scope:** full delivery plan

# Product Intent — Admissions Voice Assistant Performance & Concurrency Program

> **Lens:** PO · **Decided by:** PO, consulted TPO/Architect on MVP feasibility · **Inputs:** user brief (master prompt §1–§4), brownfield evidence from Stage 0, completed performance investigation (measured baseline + code trace + RAG recon + prefix-cache experiment) · **Defines:** AS-01 … AS-08

## 1. Problem

The admissions voice assistant answers inbound calls correctly but feels sluggish: the caller waits several seconds between finishing a sentence and hearing a reply. The team cannot say precisely where that time goes, because the pipeline has never been instrumented. Separately, the system was built for one caller at a time; nobody has established whether a second simultaneous call degrades the first, and the service that performs retrieval is architecturally single-threaded.

The operational risk is concrete: admissions traffic is bursty and seasonal, and a demo or a peak day that puts two callers on the line simultaneously may silently degrade both.

## 2. Product Intent

Make the existing admissions voice assistant hold a natural conversation for **two simultaneous callers on the current single-box hardware**, without lowering answer quality — by first instrumenting the pipeline so per-stage latency is measured rather than inferred, then removing the serial, non-streaming stages that dominate response time, and finally proving concurrency safety under a reproducible two-caller load.

## 3. Target Users & Personas

| Persona | Role | Job-to-be-done | Pain today |
|---|---|---|---|
| **Prospective student / parent** | Inbound caller | Get a trustworthy answer about programs, fees, deadlines or eligibility, quickly, by voice | Waits seconds per turn; cannot interrupt the agent; a second caller may degrade their call |
| **Admissions counselor** | Human escalation target | Receive a warm handoff with the caller's context when the agent cannot help | Handoff quality depends on lead capture that runs only post-call |
| **Operator / admin** | Runs the stack on one box | Keep 8 services healthy and know when they are not | No per-stage latency visibility; no early warning before a call degrades |
| **Developer / maintainer** | Evolves the codebase | Change the pipeline without breaking quality or concurrency | Four config keys are inert, two KB stores diverge, one pinned framework is dead weight — the code lies about what runs |

## 4. Success Metrics

| Metric | Type | Target | Classification |
|---|---|---|---|
| **Turn latency p50** (caller stops speaking → first audio frame to Twilio) | **North Star** | ≤ 700 ms *if arithmetically reachable; else the tightest defensible SLO with evidence* | User-provided |
| Turn latency p95 | Guardrail | ≤ 1,200 ms (same caveat) | User-provided |
| **Per-stage latency visibility** | Guardrail | Every turn emits a trace with ≥6 stages + engine counters | Derived |
| **2-caller concurrency** | Guardrail | Split three ways, because "unaffected" is not physically meaningful on one shared GPU: **isolation** — zero cross-caller data contamination (absolute); **performance interference** — p95 at N=2 ≤ 1.5× p95 at N=1, no turn > 3 s; **resource interference** — no OOM, no runaway CPU, no queue starvation of either caller | Derived from User-provided |
| **Quality on frozen golden set** | Guardrail | Zero regression on critical intents; ≤2pt aggregate drop elsewhere; no hallucination increase | User-provided |
| **Peak VRAM at N=2** | Guardrail | ≤ 90% of 16,311 MiB with no sysmem spill | Derived |
| **Stability** | Guardrail | 30-min soak at N=2: zero crashes, latency drift <10% | Derived |

> **North Star caveat (PO, evidence-based):** the 700 ms target was set before measurement. Measured today: the endpointing floor alone is **600 ms [Measured]**, i.e. 86% of the budget before STT runs, and the pre-audio chain is serial. Whether 700 ms is reachable is an open question this program must answer with arithmetic, not assume. See `AS-04` and `AS-05`.

## 5. MVP Scope

| In | Out |
|---|---|
| Per-stage instrumentation of the live voice path | Rewriting the app architecture |
| Prefix-cache behaviour applied to prompt design | Migrating to vLLM/SGLang (deferred pending evidence) |
| Streaming the LLM call and the TTS synthesis | Hosted LLM/STT/TTS APIs (prohibited — student PII) |
| `OLLAMA_KEEP_ALIVE` + boot preload (cold-start) | Multi-region / cloud deployment |
| De-serializing ERC retrieval for 2 callers | Barge-in (Class C — requires separate sign-off) |
| Frozen golden set + quality gate | Changing the 28-intent behaviour surface |
| Reproducible N=1/N=2 load harness | WhatsApp/chat-path optimisation (shares retrieval only) |
| 2 voice + 1 background chat/admin as an acceptance condition (`BRD-20`) | Serving background work at voice latency |
| Staged RAG delivery: baseline now, optimized on evidence (`BRD-21`) | Final RAG tuning before the golden set exists |
| Model/quantization sweep **only if** streaming + concurrency do not meet the gates | Model swap without a quality gate |

## 6. Non-goals

- **Not** a rewrite. Pipecat is bypassed today; wiring it in is a candidate, not a premise.
- **Not** a model-downsizing project by default — but **not a model-preserving project either.** See the neutrality rule below.

## 6b. Solution-neutrality rule (governs every later stage)

> **No architecture component is the final solution until it has either passed a comparative evaluation against named alternatives, or been eliminated by a documented feasibility constraint. The existing implementation receives no preferential treatment for already existing.**

Applied to the four decisions this program previously pre-committed to:

| Component | Status under this rule |
|---|---|
| `qwen2.5:14b` @ ctx 8192 | **Baseline candidate.** Not the assumed final answer. Must be Pareto-compared against smaller size classes on quality, TTFT, prefill, decode, VRAM and N=2 concurrency. |
| Ollama | **Baseline candidate.** Must be compared against llama.cpp server and, if WSL2 + Blackwell support verify, vLLM/SGLang — on scheduling and prefix-cache behaviour under two active streams. |
| Current RAG configuration | **Baseline candidate.** `chunk size`, `overlap`, `TOP_K`, `FETCH_K`, threshold, reranker, context cap and hybrid weighting are all undecided pending retrieval evaluation. |
| Hand-rolled voice loop | **Baseline candidate** — with Pipecat excluded only by a *documented* feasibility cost (`REC-01`), which is a constraint, not a preference. |

**Correction to a claim this document previously made.** It stated that measured decode at 77% of the 448 GB/s ceiling makes model size "a **last** lever". That conflated two different questions. 77% describes the *efficiency* achievable **for this model** — it says nothing about changing to a model that reads fewer bytes per token. A 7B Q4 (~4.7 GB) moves the ceiling from 49.8 to ~95 tok/s; at the same measured efficiency that is roughly **2× decode**, and it halves weight bytes for prefill as well. Model size is therefore a **first-class, measured lever**, and `UC-10` is promoted from "fallback if gates fail" to a **gated comparison that runs regardless** of whether streaming and concurrency alone meet the targets.
- **Not** a quality-improvement project. The bar is "no regression", not "better answers".
- **Not** a cloud migration. Data residency is a hard constraint, not a preference.
- **Not** an observability platform build. Log files and a harness are sufficient at this scale.

## 7. Assumptions

| ID | Assumption | Classification |
|---|---|---|
| AS-01 | Assumed: peak concurrent callers is 2; no measured arrival rate exists yet | Unknown |
| AS-02 | Assumed: the quality bar is "current behaviour on critical intents", pending a frozen golden set | Assumed |
| AS-03 | Assumed: all components stay local; no hosted inference is acceptable | User-provided |
| AS-04 | Assumed: the 700 ms p50 target may be arithmetically unreachable given a 600 ms endpointing floor; the program is authorized to propose a tighter-but-achievable SLO with evidence rather than silently relax it | Derived |
| AS-05 | Assumed: a human will supply golden-set ground truth for fees, deadlines, eligibility and escalation before the quality gate can run | Assumed |
| AS-06 | Assumed: the admissions calendar creates bursts above 2 callers; the shape is unmeasured | Unknown |
| AS-07 | Derived: decode is bandwidth-bound at 77% of the 448 GB/s ceiling (38.5 of 49.8 tok/s measured), leaving ~30% headroom — so model size is a weak lever | Derived |
| AS-08 | Assumed: the two divergent KB stores (local `langchain` ~47 recs, ERC `meridian-kb` 37 chunks) can be reconciled to one without losing content | Assumed |
