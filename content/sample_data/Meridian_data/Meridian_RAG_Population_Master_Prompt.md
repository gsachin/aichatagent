# Meridian University RAG — Population & Optimization Master Prompt

> **What this is:** a prompt for a coding agent (Claude Code or similar) that runs **on your 16 GB VRAM machine** with read/write access to both repos and your data files. It forces *discovery → design → approval → build → measure*, so nothing gets ingested until the agent knows your hardware, your repos' real behaviour, and your data's real shape.
>
> **How to use:** (1) open the agent in a parent folder containing both repos and the data files, (2) fill the three placeholders in §0, (3) paste everything between `PROMPT START` and `PROMPT END`.
>
> **Provenance of Appendix A:** the **[F-n]** items come from a static read of both GitHub repos (default branch, 2026-09-19), a profile of your 8 uploaded files, and three small sandbox experiments (Chroma 1.5.9 distance space, Chroma `None` metadata, the repo's own PDF ingester run on your Reference PDF). They are *leads*, not facts about your machine — the agent must re-verify each one locally.

---

## PROMPT START

# ROLE
You are a senior retrieval and real-time voice-AI engineer. You will plan, and after approval build, the knowledge base that powers a university admissions assistant answering **voice calls and text chat** from a **local LLM on a single 16 GB VRAM machine**. Correctness and latency both matter; a wrong fee spoken aloud is worse than a slow answer, and a 4-second silence is worse than a slightly shorter answer.

# 0. INPUTS (fill before pasting)
| Placeholder | Value |
|---|---|
| `AICHATAGENT_PATH` | `<path to checkout of github.com/gsachin/aichatagent>` |
| `ENTERPRISE_RAG_CORE_PATH` | `<path to checkout of github.com/gsachin/enterprise-rag-core>` |
| `DATA_DIR` | `<folder holding the 8 data files listed in Appendix B>` |

Write all your outputs under `<parent>/_rag_population/` with sub-folders `discovery/`, `kb/`, `eval/`, `reports/`. Never write into the repos until Gate 1 is approved.

# 1. MISSION AND DEFINITION OF DONE
Populate ChromaDB — through enterprise-rag-core and consumed by aichatagent — with the Meridian admissions knowledge in `DATA_DIR`, so that **on this machine** voice and chat both get correct, short, speakable answers with minimal latency, while LLM + STT + TTS + embedder are simultaneously resident without exhausting VRAM.

Done means all of: (a) `DISCOVERY_REPORT.md`; (b) `INGESTION_PLAN.md` approved by me; (c) a reproducible, idempotent ingest pipeline writing a versioned collection; (d) `EVAL_REPORT.md` showing the result beats the baselines defined in B11; (e) a runbook for re-ingesting **real** data later, because every figure in the sources is self-declared dummy data.

# 2. HARD RULES
1. **Discovery is read-only.** No installs, model pulls, config edits, or writes into the repos or existing Chroma directories until I approve the plan (Gate 1). Non-destructive measurement is allowed (timing queries against running local services, `nvidia-smi`, `pytest` runs that don't write into repos).
2. **Evidence labels on every claim:** `[VERIFIED: <command or file:line>]`, `[INFERRED: <reason>]`, or `[UNKNOWN: <how to find out>]`. No unlabeled statements about my system.
3. **Code and runtime beat documentation.** Both repos contain stale or conflicting docs (Appendix A: F3, F4). If a doc and the code/runtime disagree, report both and trust what you measured.
4. **Re-verify every Appendix A finding** on my machine and mark it CONFIRMED / REFUTED / CANNOT-TEST. Do not assume any of them are true.
5. **No invented facts.** Every knowledge-base record traces to a source file + section. If sources conflict or a fact is absent, log it in `discovery/data_conflicts.md`; do not silently pick or fabricate. Rank on conflict: Reference Part 2 > Answered FAQ > Master-QA sample answers.
6. **Backups and branches.** Before Gate 1 approval you touch nothing. After it: create a git branch in each repo you modify, copy any existing Chroma directory to a timestamped backup, and never delete an existing collection — build a new versioned one (`meridian_kb__v<N>`) and switch by configuration.
7. **Held-out discipline.** The evaluation split is frozen *before* any tuning and never used to build the index (see B11).
8. **PII never enters Chroma.** The conversation script collects name, phone and email (they belong in the leads database). The vector store holds only university knowledge.
9. **If a command can't be run** (no shell on the target machine, permissions, policy block), emit one copy-paste script (`discover.ps1` or `discover.sh`) for me to run and paste back. Never guess hardware numbers.
10. **Ask me at most once,** in a single batched list, at Gate 1. Otherwise choose the recommended default and record the decision.

# 3. PHASE A — DISCOVERY (read-only) → `discovery/DISCOVERY_REPORT.md`

## A1. Machine profile (hardware + software), *measured*
Capture with the commands below (use the PowerShell or bash form for the detected OS). Report as a table plus raw output in `discovery/machine_raw.txt`.

| Area | Capture |
|---|---|
| GPU | `nvidia-smi`; `nvidia-smi --query-gpu=name,driver_version,memory.total,memory.used,compute_cap --format=csv`. Is the 16 GB the *whole* GPU or shared with display/other apps? Record **idle baseline VRAM used**. |
| GPU per process | `nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv` (idle, then again in A3 under load) |
| CPU / RAM / disk | Win: `Get-CimInstance Win32_Processor`, `Win32_ComputerSystem` (RAM), `Get-PSDrive`. Linux: `lscpu`, `free -h`, `df -h`. Is the Chroma path on SSD/NVMe? |
| OS / runtime | OS + build; native Windows vs WSL2 vs Docker (affects GPU access and Chroma file I/O speed — a Chroma dir on `/mnt/c` inside WSL2 is slow) |
| Python | version per venv (repos declare 3.11–3.13); `pip freeze`; `pip check` for each environment |
| Accelerators | `python -c "import torch;print(torch.__version__,torch.cuda.is_available(),torch.version.cuda)"`; `python -c "import onnxruntime as o;print(o.__version__,o.get_available_providers())"` — is `CUDAExecutionProvider` present? |
| Ollama | `ollama --version`; `ollama list`; `ollama ps`; `ollama show <each model>` (params, quant, context length); all `OLLAMA_*` env vars; server log location; which models are resident right now |
| Policy blocks | Try importing `chromadb, onnxruntime, tokenizers, pypdf, httpx, mcp, langchain_community, pipecat`. Record any DLL/AppLocker/WDAC failure (this project's history contains several) |
| Docker | `docker version` (optional infra only; do not start anything) |
| Other GPU consumers | browser, Teams, games, other Python processes holding VRAM |

## A2. The two solutions and their internal modules
Neither repo appears to use git submodules; treat each **internal package/module** as a "submodule". For each repo capture:
- `git remote -v`, branch, `git log -5 --oneline`, `git status`, and `.gitmodules` (expected absent — confirm).
- A module map: path → purpose → key public functions/classes → who calls it.
- **Build and environment:** dependency pins vs what is actually installed; every `os.environ`/`getenv` key the code reads (table: key, default in code, default in `.env.example`, value on this machine); launch/setup scripts; Docker/compose files; ports.
- **Test baseline:** run each repo's test suite, record pass/skip/fail counts and *why* anything fails or skips. (enterprise-rag-core's own status doc claims 69 passing with Redis running; verify rather than trust.)
- **Data flow diagrams** (Mermaid) for: (i) chat turn, (ii) voice turn (VAD → STT → retrieval → LLM → TTS), (iii) the write/ingest path.
- **Integration seam.** Determine whether aichatagent currently imports or calls enterprise-rag-core at all (F1/F11 suggest it does not). Enumerate options — **(A)** import `enterprise_rag` as an in-process library, **(B)** call its MCP server, **(C)** share one persisted Chroma store with enterprise-rag-core as sole writer — and compare on voice latency (network/MCP hop), failure modes, and code change size. Recommend one, with evidence.

## A3. Runtime state and baseline measurements
1. **Existing Chroma stores** (`aichatagent/chroma_local_db`, enterprise-rag-core's `chroma_data` or `RAG_CORE_CHROMA_PATH`, any others found): list collections; per collection record count, embedding dimension, distance space, metadata keys, duplicate-document count (by content hash), and which code path reads it.
2. **Baseline B1 — aichatagent as it runs today.** Cold vs warm timings for: query embedding, Chroma query, total retrieval, LLM time-to-first-token and tokens/s, STT per utterance, TTS time-to-first-audio, full voice-turn timeline. Reuse `run_pipeline_test.py`, `test_full_pipeline.py`, `test_rag_llm.py`, `test_audio_local.py` where they work; otherwise write a minimal timing harness in `discovery/`.
3. **Baseline B2 — enterprise-rag-core with defaults** on a small throw-away in-memory or temp-dir collection (not the real one).
4. **VRAM ledger (measured).** Load each component the way the real pipeline does and record resident VRAM per process (see template in Appendix D). Then run the full voice pipeline and record **peak** VRAM. This ledger replaces `doc/model_vram_analysis.md` and `app/memory_budget.py`, which are written for a 6 GB GPU (F4).
5. **Ollama truncation check.** Send a retrieval-sized prompt (top-k chunks + system prompt + question) at the configured `num_ctx` and check the Ollama server log for prompt truncation (F4).

## A4. Data inventory → `discovery/data_inventory.md`
Parse each file in Appendix B; record counts, encoding, parse warnings, duplicate content across formats (hash/diff), and a role assignment. Re-verify Appendix A data findings F14–F20.

## A5. Findings register → `discovery/findings.md`
One row per Appendix A finding: CONFIRMED / REFUTED / CANNOT-TEST, evidence, and impact on the plan. Add any new problems you find.

# 4. PHASE B — DESIGN → `reports/INGESTION_PLAN.md`
For each decision D1–D12 give: options considered, experiment/measurement, result, **choice**, rollback. Where I give a **Default**, keep it unless your evidence contradicts it — and say so if it does.

**D1. Source roles.** Default: *Reference Part 2* = canonical facts. *Reference Part 1* (conversation script) = agent flow/system prompt, **not** knowledge (if retained at all, separate `doc_type=script`, excluded from default retrieval). *Answered FAQ* = objection/abstention behaviour. *University_Q* = intent taxonomy / coverage checklist. *Question_Set* = unlabeled query variants + test queries. *Master QA (csv≡xlsx)* = **evaluation set** and paraphrase source (its README says so); xlsx *Program Matrix* and *Objection Handling* sheets are cross-checks. PDFs = duplicates of the .md files — skip them (F9). Do not ingest the same content twice.

**D2. Record types.** Default: (1) `fact_card` — one atomic, self-contained fact; (2) `program_card` — one per program/doctoral area combining duration, eligibility, tuition, level, school; (3) `faq_card` — canonical Q + student-facing answer + handling pattern; (4) `unknown_card` — an explicit "not specified in our materials" record per known gap (refund policy, average salary, transport, campus tour, spring dates…) so retrieval can *find the absence* and the bot can abstain and hand off gracefully. Schema example in Appendix C.
- Every card is **self-contained**: name the entity, attribute, value and qualifier ("B.Tech AI & Machine Learning — tuition $15,200 per year"), never "It costs $15,200".
- Wording is **student-facing**. Strip meta-language like "the reference does not provide…" (F19); replace with natural phrasing plus the admissions hand-off (email/phone/hours from Reference §2.15).
- Keep a `spoken` field (≤ 2 short sentences, TTS-friendly: no markdown, "ten plus two", "fourteen thousand five hundred dollars") beside `text` for chat.
- If the ONNX reranker stays enabled, cards must be ≈ ≤ 90 tokens (the pair is truncated at 128 tokens — F12).

**D3. Question-key (multi-vector) index.** Default: embed *question variants* as retrieval keys that point to a parent card id, and return the parent's text. Rationale: Master-QA answers are terse and context-free (17 are < 15 chars, e.g. "No.", "$300.") so they must not be indexed as standalone chunks. Only questions from the KEY side of the split (B11) become keys. Cluster paraphrases by canonical fact id (598 unique answers cover 827 questions — F16).

**D4. Metadata schema and adapter change.** Chroma metadata must be scalar. Minimum fields: `doc_type, source_file, source_section, kb_version, data_status("dummy"), content_hash, canonical_id, level(UG|PG|PHD|ALL), school, program_id, topic, audience(domestic|international|parent|any), answerability(direct|unknown|objection), tenant_id, required_clearance, department`. enterprise-rag-core persists only five fields today (F6): propose the smallest **backward-compatible** change (optional `metadata: dict` on `UpsertRecord` passed through the Chroma adapter) with tests, following the repo's rule that each phase ends on a green suite.

**D5. Embedding model, prompt format and placement.** Benchmark candidates on the frozen eval set: the current `nomic-embed-text` *with* its required `search_document:` / `search_query:` prefixes (F10), plus 2–3 alternatives **that exist in the current Ollama library on the day you run this** (check it — do not rely on memory; examples to check: `bge-m3`, `mxbai-embed-large`, `snowflake-arctic-embed`, `embeddinggemma`, a Qwen3-embedding variant). Also decide: batch embedding (`/api/embed` vs the legacy per-text `/api/embeddings` — verify what your Ollama supports), embedder on **GPU vs CPU** (a 300 M-param embedder on CPU may cost ~nothing in VRAM for a few ms), and `keep_alive` so the query embedder is never cold (F10: 5 s timeout). Store `embed_model`, `dim`, `prefix_scheme`, `kb_version` in collection metadata.

**D6. Collection setup.** Default: create the collection with **cosine** space — `get_or_create_collection(name, configuration={"hnsw": {"space": "cosine"}})` (works on chromadb 1.5.9; use `metadata={"hnsw:space":"cosine"}` on older versions). Use a **unique collection name** (not `langchain`, F5). Pass batches under `client.get_max_batch_size()`.

**D7. Retrieval configuration.** Sweep on the eval set, holding everything else fixed: dense-only vs hybrid; **alpha** (in this code `alpha` is the *dense* weight, so the default 0.3 is keyword-heavy — F8); `fetch_k`/`top_k`; MMR on/off; reranker on/off (CPU, English-only MS-MARCO MiniLM — F12); semantic cache off/`memory` (F13). Also: **persist or rebuild the BM25 leg at startup** from Chroma (F7 — the corpus is tiny, a rebuild takes milliseconds); add an **alias/normalisation layer** so keyword matching survives `B.Tech/BTech/B Tech`, `M.Sc/MSc`, `10+2/ten plus two`, `hostel/residence hall/accommodation`, `fees/tuition/cost` (the BM25 tokenizer splits `B.Tech` into `b`,`tech` and drops `+` — F7).

**D8. Context assembly for the LLM.** Default: 3–4 cards, no `[chunk_id | parent | score]` headers, no U-shape reordering unless it measurably helps (F11), a *static* system-prompt prefix so Ollama's prompt/KV cache can reuse it between turns, and a hard token budget. Chat and voice use different formatters (`text` vs `spoken`).

**D9. Conversation-aware retrieval.** Voice queries are short, unpunctuated and anaphoric ("and for the MBA?"). Use the slots the script already collects (level, program, intake, domestic/international) as **metadata filters or boosts**, and carry the last-mentioned entity forward with a rule-based rewrite first; only add an LLM query-rewrite if the eval shows it is needed *and* it fits the latency budget.

**D10. Abstention.** Calibrate a retrieval-score/margin threshold so questions with no support (Master-QA `Graceful Unknown`, Question_Set §35 out-of-scope) route to `unknown_card` handling instead of a plausible-sounding invention. Report abstention precision and recall.

**D11. 16 GB VRAM and Ollama configuration — derived from the A3 ledger, not from the 6 GB documents.** Fill the ledger; keep **≥ 1.5–2 GB headroom** at measured peak. Decide and justify: LLM model + quantisation; `num_ctx` sized to the real need (system prompt + ≤ 4 cards + history + answer) — KV cache scales with `num_ctx × parallel slots`; `OLLAMA_NUM_PARALLEL`; `OLLAMA_MAX_LOADED_MODELS` (make sure the embedder does not evict the LLM — check `ollama ps` under load); `OLLAMA_KEEP_ALIVE`; flash-attention and KV-cache quantisation env vars (**verify each exists in the installed Ollama version before recommending**); Whisper size/compute type; Kokoro provider (CUDA vs CPU). Propose 2–3 LLM candidates that fit (check the current Ollama library; do not assume the model list in the repo docs is current) and run the *same* eval through each — **recommend, do not switch, without my approval**. Note `.env.example` names `qwen2.5:6b-instruct-q4_K_M`; confirm whether that tag exists (F3).

**D12. Versioning, idempotency and startup guards.** Deterministic ids (`<doc_id>:<type>:<slug>`), `content_hash` to skip unchanged records, blue/green collections (`meridian_kb__vN` → flip via config), a `kb_manifest.json` (sources + hashes + model + dim + counts), and a **startup guard** that refuses to serve if collection dim/model/space ≠ configuration — replacing the current "take `collections[0]`" behaviour (F1) and the rebuild-on-start duplication (F2).

## B11. Evaluation design (freeze before tuning)
- **Split:** group Master-QA questions by canonical fact id; split **by fact group** (~70% KEY / ~30% BLIND, stratified by topic) so no paraphrase of a test question is ever indexed. Report two numbers: *BLIND* (card-only retrieval) and *held-out paraphrases of keyed facts*. Additionally map the ~676 Question_Set questions not in Master-QA to fact ids with human review — **extractive mapping, not generated answers**.
- **Voice robustness set:** ~100 questions run through the real Kokoro→Whisper round trip on this machine (or, failing that, programmatic ASR-style noise: lowercase, no punctuation, "b tech", "m b a", "ten plus two").
- **Metrics:** fact-level Recall@1/3/5 and MRR; **wrong-entity rate** (e.g. BBA tuition returned for an MBA question); numeric/date exact-match on the final spoken answer; abstention precision/recall; per-stage latency p50/p95/p99 (embed, Chroma, BM25, rerank, context build, LLM TTFT, TTS TTFA); peak VRAM at 1 and N concurrent sessions; 30-minute soak without OOM or model eviction.
- **Baselines:** **B0** full Reference in the prompt with no retrieval (the corpus is only a few thousand tokens — this checks whether RAG is earning its keep for accuracy and latency); **B1** aichatagent today; **B2** enterprise-rag-core defaults; then your candidates. Adopt a candidate only if it wins on accuracy *and* meets the latency budget.
- **Proposed acceptance targets (I will confirm):** direct-question Recall@3 ≥ 0.97; wrong-entity ≤ 1%; abstention accuracy ≥ 95%; warm retrieval p95 ≤ 100 ms; no OOM and ≥ 1.5 GB VRAM headroom at peak; voice time-to-first-audio target set from the B1 measurement (≈ 1.5 s is a common conversational goal).

## Plan document contents
`INGESTION_PLAN.md` must contain: executive summary (≤ 15 lines); machine profile and VRAM ledger; findings register summary; decision log D1–D12; the record/metadata schema; the eval design and baseline numbers; a phased task list with a **pass/fail gate per phase** (Phase C below); risk register; effort estimate; and a **single batched list of questions** for me.

# 5. GATE 1 — STOP
Finish Phase B, then **stop** and present: what you verified, what you assumed, what you need from me. Do not modify repos, pull models, or write to Chroma until I reply **`APPROVE PLAN`** (or with changes).

# 6. PHASE C — BUILD (only after approval; each step ends on a green gate)
- **C0** branch per repo; timestamped backup of existing Chroma dirs; pin the environment you tested.
- **C1** parse sources → canonical, human-reviewable JSONL in `kb/` (nothing embedded yet).
- **C2** generate cards, aliases and question keys; **automated cross-check**: every number, `$` amount, percentage and date in a card must appear in the Reference (or be flagged); Program Matrix sheet must agree with Reference §2.6–2.7.
- **C3** adapter changes (metadata pass-through, cosine collection, BM25 rebuild-on-start, startup guard) with unit tests; existing suites stay green.
- **C4** batch-embed and ingest into `meridian_kb__v1`; idempotency test (re-run = zero changes); restart test (hybrid still returns keyword hits after restart).
- **C5** wire aichatagent to the single retrieval path behind an env flag (unify `app/rag.py` and `app/pipeline.py:retrieve_context`), keeping chat and voice on identical retrieval.
- **C6** run the eval, tune within the sweeps in D5–D8, freeze the config.
- **C7** 30-minute voice soak + concurrency test; record VRAM/latency.
- **C8** write `EVAL_REPORT.md` and a `REINGEST_RUNBOOK.md` (how to replace dummy data with real data, bump `kb_version`, re-evaluate, roll back).

# 7. REPORTING STYLE
Concise. Tables over prose. End every phase with **Verified / Assumed / Need from you**. Never report a number you did not measure. Never say a check passed if you could not run it.


---

# APPENDIX A — FINDINGS TO RE-VERIFY (leads from a static read + small experiments, 2026-09-19)

## Repo and runtime findings
| ID | Claim | Where | Re-verify by |
|---|---|---|---|
| F1 | aichatagent has **no reference to `enterprise_rag`** (integration must be designed). Inside aichatagent there are **two retrieval paths**: `app/rag.py` (LangChain Chroma, MMR `k=5`, `fetch_k=20`) and `app/pipeline.py:retrieve_context` (raw chromadb, **`collections[0]`**, default `top_k=2`, hard-coded `nomic-embed-text`, 768-dim assumption). A prior RCA (`doc/RCA_RAG_UNIFICATION.md`) says these diverged. `pipeline.build_rag_prompt` now imports the unified `app.rag.retrieve_context`, but the **legacy `pipeline.retrieve_context` still exists and is what `test_full_pipeline.py` and `run_pipeline_test.py` call** — so those scripts may benchmark the wrong path. | `app/rag.py`, `app/pipeline.py`, `test_full_pipeline.py`, `run_pipeline_test.py`, `doc/RCA_RAG_UNIFICATION.md` | `grep -rn enterprise_rag` (none found on 2026-09-19); trace a chat turn and a voice turn with logging to see which path answers; benchmark **both** paths in A3 |
| F2 | `get_vector_store()` calls `_build_with_langchain()` → `Chroma.from_documents(...)` **on every process start when the Chroma dir is non-empty**, with no ids → chunks are re-added with new UUIDs (duplicates accumulate). The "dir empty → return None" branch means it never does a first build. | `app/rag.py` `get_vector_store`, `_build_with_langchain` | count docs → restart the app → count again |
| F3 | **Config/doc drift:** chunking is 1500/100 in `rag.py` but 800/150 in `admissions_bot.py` and `PROJECT_REFERENCE.md`; `RAG_TOP_K` is 5 (`rag.py`) vs 2 (`pipeline.py`, `.env.example`); `.env.example` sets `OLLAMA_MODEL=qwen2.5:6b-instruct-q4_K_M` (Qwen2.5 has no 6B size as far as I know — confirm the tag); `RAG_SIMILARITY_THRESHOLD` in `.env.example` is **not read by any Python file**. **The shipped KB, UI text and `SYSTEM_PROMPT` are for UMD & FDU, not Meridian** (source PDF `UMD_and_FDU_University_Profile_Report.pdf`). | listed files | `grep` each key; `ollama list` |
| F4 | `doc/model_vram_analysis.md` and `app/memory_budget.py` budget a **6 GB** GPU. Default `num_ctx=2048`: `k=5` chunks × 1500 chars ≈ 1.9k tokens **plus** system prompt and question can exceed it, so Ollama may silently truncate the prompt. | `doc/model_vram_analysis.md`, `app/memory_budget.py`, `app/rag.py` | A3.4 ledger; Ollama server log for truncation warnings |
| F5 | enterprise-rag-core creates the Chroma collection with `get_or_create_collection(name)` → **default space is L2** (verified on chromadb 1.5.9: identical vector → distance 0.0, orthogonal → 2.0), yet `ChromaVectorStore.search` reports `1 - distance` as "cosine similarity" (score −1 possible; ranking differs from cosine for non-unit vectors). Default collection name `langchain` is also LangChain's default (aichatagent passes no name) → collision risk. | `adapters/chroma_vector.py`, `config.py` | `col.configuration`; distance test; `client.list_collections()` |
| F6 | Only 5 metadata fields are persisted (`parent_id, tenant_id, section_title, required_clearance, department`); no program/level/topic. `department=None` is **silently dropped** by Chroma 1.5.9 (verified). | `adapters/chroma_vector.py` `upsert` | inspect `col.get(include=["metadatas"])` |
| F7 | The BM25 keyword leg is **in-memory per process**. `ingest` fills it only inside the ingesting process → a later `serve` starts with an empty keyword leg, so hybrid silently degrades to dense-only. Tokenizer `[a-z0-9$]+` splits `B.Tech`→`b`,`tech` and `10+2`→`10`,`2`. | `adapters/bm25_memory.py`, `cli.py` | ingest, restart, query, inspect sparse hits |
| F8 | `fuse_wrrf(dense, sparse, alpha)` weights **dense by `alpha` and sparse by `1-alpha`**; default `alpha=0.3` is keyword-heavy. | `hybrid.py`, `config.py` | read code; sweep alpha |
| F9 | The repo's PDF ingester **shatters your PDFs.** Run on the Reference PDF it produced 205 blocks (median 7 chars; 164 blocks < 40 chars), 9 spurious "tables" (e.g. headers `['1991 School','of','Management',…]`), and split program/fee tables across blocks. Use the `.md`/`.xlsx` sources instead. | `ingestion/__init__.py` | `extract_blocks(pdf)` on both PDFs |
| F10 | Embedding client posts **one text per call** to the legacy `/api/embeddings`, 5 s timeout, and applies **no `search_document:`/`search_query:` task prefixes** required for best `nomic-embed-text` quality. A cold/evicted embedder can exceed the timeout. | `hybrid.py` `OllamaEmbeddingClient` | time cold vs warm; check installed Ollama's `/api/embed` batch support |
| F11 | The orchestrator and MCP tool are **interview-shaped**: required inputs `resume_text`, `job_description`, `rubric_query`; default direct ids `resume:current`, `jd:target`; `top_k=5` hard-coded; formatter emits `[chunk_id | parent | score]` headers, U-shape ordering and truncates at 1200 chars. Admissions needs a thin retrieval entry point (library call or new tool). | `orchestrator.py`, `server.py`, `formatter.py` | read; call the tool with dummy args |
| F12 | Reranker = English MS-MARCO MiniLM INT8 on **CPU**, `max_length=128` tokens for the *(query, passage)* pair → long passages are silently truncated; needs `download-model`, otherwise falls back to a no-op reranker without warning. | `reranker.py`, `config.py` | check model file exists; tokenised length of a card |
| F13 | Semantic cache stores **retrieved chunks, not answers**; default `none`; `memory` is per-process. Near-identical questions with different entities ("MBA fees"/"BBA fees") can collide at high cosine similarity. | `cache.py`, `orchestrator.py` | entity-collision test before enabling |

## Data findings (files in Appendix B)
| ID | Claim | Re-verify by |
|---|---|---|
| F14 | **Duplicates across formats:** Master-QA `.csv` ≡ `.xlsx` "Master QA" sheet (same 827 rows, verified); the two PDFs are near-copies of their `.md` files (token Jaccard ≈ 0.99 Reference, ≈ 0.89 FAQ). Ingesting all formats would double-count content. | hash/diff |
| F15 | **Reference has two parts:** Part 1 = conversation script (flow, not facts; numbering glitch — two items "12."; offers a **Diploma** option although Part 2 lists no diploma program); Part 2 = facts (self-declared dummy data; 8 UG + 6 PG programs, 4 doctoral areas). | read both parts |
| F16 | **Master QA:** 827 rows, 6 columns; **61 distinct `Category` strings** (many one-offs like "Hostel Objection") → normalise to ~12 topics; `Answer Type` = 754 Direct / 38 Objection-Graceful / 35 Graceful Unknown; **598 unique answers** (305 rows share an answer with another row); median answer 89 chars; **17 answers < 15 chars** ("No.", "Yes.", "$300."). README says it is an *evaluation set*. | pandas profile |
| F17 | **Question_Set.md:** 825 numbered lines, ~775 unique after normalisation, **only 99 also in Master QA** → ~676 unlabeled questions across 36 sections (includes §35 out-of-scope, §36 program-listing tests). No answers. | parse + normalise |
| F18 | **University_Q.md:** 46 topic questions in 8 sections → intent taxonomy / coverage checklist. | parse |
| F19 | **Meta-language leakage:** 132 of 827 Master-QA answers contain "reference"; FAQ answers are written as "The reference document does not specify…". Spoken verbatim this is wrong for a student-facing bot. | grep |
| F20 | **Coverage gaps to encode as `unknown_card`s:** "50+ programs" but only 18 listed (README confirms); only Fall dates given (no Spring/Summer); no refund policy, average salary, transport, curriculum detail, safety detail, campus-visit policy, course-change policy. Verify each against the sources before creating the card. | keyword search over sources |

# APPENDIX B — DATA INVENTORY (role assignment; confirm in A4)
| File | Content | Role |
|---|---|---|
| `Meridian-University-Admission-Agent-Reference…docx.md` (+ `.pdf`) | Part 1 script; Part 2 facts (overview, programs, admission, fees, aid, dates, campus, contact) | **Canonical knowledge** (Part 2); script → agent flow. PDF = duplicate |
| `Meridian_Admissions_FAQ_Answered…docx.md` (+ `.pdf`) | 40 objection/decision-making Q&As | `faq_card` + abstention behaviour (rewrite to student-facing wording). PDF = duplicate |
| `Meridian_Admission_RAG_Master_QA_827.csv` / `.xlsx` | 827 Q + sample answer + type; xlsx adds README, Objection Handling (51 rows), Program Matrix (14 programs) | **Eval set** + paraphrase source; sheets = cross-checks. csv ≡ xlsx |
| `Comprehensive_Meridian_University_Admission_Chatbot_Question_Set.md` | 825 numbered questions, 36 sections | Unlabeled query variants, test queries |
| `University_Q.md` | 46 topic questions, 8 sections | Taxonomy / coverage checklist |

# APPENDIX C — EXAMPLE RECORD SHAPES (adapt; Chroma metadata must be scalar)
```json
{
  "id": "kb:program:btech-aiml",
  "doc_type": "program_card",
  "text": "B.Tech in AI & Machine Learning at Meridian University: 4 years, School of Computer Science. Eligibility: 10+2 with Physics, Chemistry and Mathematics (PCM). Tuition: $15,200 per year.",
  "spoken": "The B.Tech in A.I. and Machine Learning is a four-year program. You need ten plus two with physics, chemistry and maths. Tuition is fifteen thousand two hundred dollars a year.",
  "aliases": "BTech AI ML, B Tech artificial intelligence, bachelor of technology AI",
  "metadata": {
    "source_file": "Reference-Part2", "source_section": "2.6",
    "level": "UG", "school": "Computer Science", "program_id": "btech-aiml",
    "topic": "program", "audience": "any", "answerability": "direct",
    "data_status": "dummy", "kb_version": "1",
    "content_hash": "<sha256>", "tenant_id": "meridian", "required_clearance": 0
  },
  "question_keys": ["Does Meridian offer AI?", "How much is B.Tech AI per year?"]
}
```
```json
{
  "id": "kb:unknown:refund-policy",
  "doc_type": "unknown_card",
  "text": "Meridian's published materials do not include a refund policy. For a definitive answer, contact the Admissions Office at admissions@meridian.edu or +1 (555) 204-7890, Monday to Friday, 9 AM to 5 PM.",
  "metadata": {"topic": "fees", "answerability": "unknown", "source_file": "FAQ+MasterQA", "data_status": "dummy"},
  "question_keys": ["What is the refund policy?", "Can I get my money back if I withdraw?"]
}
```

# APPENDIX D — VRAM LEDGER TEMPLATE (fill with **measured** values; never copy from the 6 GB docs)
| Component | Process | Model / setting | Resident VRAM (GB) | Peak under load (GB) | Knob to shrink |
|---|---|---|---|---|---|
| Display / other apps (idle baseline) | — | — | | | close apps |
| LLM (Ollama) | ollama | model, quant, `num_ctx`, parallel slots, KV type | | | quant, `num_ctx`, slots, KV quant, flash-attn |
| Embedder | ollama or in-process | model, GPU/CPU | | | run on CPU |
| STT | python | Whisper size, compute type | | | smaller model / int8 |
| TTS | python | Kokoro, ONNX provider | | | CPU provider |
| VAD | python | Silero | | | (CPU) |
| CUDA context overhead per process | each | — | | | fewer GPU processes |
| **Total** | | | | **≤ 16 − headroom (≥ 1.5–2 GB)** | |

## PROMPT END
