# Session Log — RAG Architecture, Hosting Options, Latency Sizing

**Date:** 2026-09-17
**Branch:** `whatsapp-api`
**Scope:** Four questions answered in one session — where the RAG comes from,
whether this system can run on Hugging Face, which cloud to pick for a customer
demo and what it costs, and how to prompt multiple LLMs for a low-latency
sizing spec.

> Reference document. Nothing here was deployed or changed — all findings are
> from reading the codebase and verifying platform limits against current docs.
> Costs are vendor list prices as published at the time of writing and should be
> re-checked before committing money.

---

## Contents

1. [Q1 — Where does the RAG come from, and where is the RAG engine?](#q1)
2. [Q2 — Can this be hosted on Hugging Face?](#q2)
3. [Q3 — Which cloud for a customer demo, and what does it cost?](#q3)
4. [Q4 — The reusable low-latency sizing prompt](#q4)
5. [Appendix A — Environment variables per deployment target](#appendix-a)
6. [Appendix B — Key file and line references](#appendix-b)
7. [Appendix C — Sources](#appendix-c)

---

<a name="q1"></a>
## Q1 — Where does the RAG come from, and where is the RAG engine?

### The retrieval path

Two sources, MCP-first with a local fallback. The seam is `app/rag.py`.

| Layer | File | What it does |
|---|---|---|
| Entry | `app/voice_handler.py:649`, `app/main.py:1364` | voice call / chat text → `pipeline.run_rag_query_sync` |
| Pipeline | `app/pipeline.py:292` | → `app.rag.query_rag` (`app/pipeline.py:84` is the Pipecat/WS variant → `retrieve_context`) |
| **Dispatcher** | `app/rag.py:153` | threshold gate → `retrieve_context` (`:93`) → prompt → LLM |
| MCP client | `app/rag_mcp.py:163` | JSON-RPC over httpx, calls tool **`retrieve_context`** |
| Legacy fallback | `app/rag_legacy.py:446` | local ChromaDB + BM25 hybrid/MMR |

`app/rag.py:103` tries `rag_mcp.mcp_retrieve()` first; on failure in `auto` mode
it logs a warning, trips the circuit breaker, and drops to `rag_legacy`. Mode is
`USE_MCP_RAG` (`.env:192` → `auto`). LCEL chain sites (`app.py:219`,
`admissions_bot.py:34`) get `MCPRetriever` from `app/rag.py:117`.

**Only retrieval is decoupled** — prompt construction and the LLM call
(`app/llm_backend.chat`) stay in universityDemo.

### The engine location

The engine is **not in this repo**. It is the standalone service at
**`D:\project\enterprise-rag-core`** (package `enterprise_rag`, v0.1.0, from
`github.com/gsachin/enterprise-rag-core`):

- `enterprise_rag/server.py:228` — `build_mcp()` exposes two tools; the
  streamable-HTTP app is mounted at **`/mcp`** (`:272`)
- `enterprise_rag/cli.py:66` — `enterprise-rag-core serve --host --port`
- Engine internals: `orchestrator.py` (agent-context path), `hybrid.py`,
  `reranker.py`, `cache.py`, `formatter.py`, `security.py`, `ingestion/`,
  `adapters/`

**Wiring:**
- `start_services.ps1:507` (Step 4b) clones/syncs it to `$ERCRoot` — default
  sibling of the project root
- `start_services.ps1:704` (Step 6b) launches it on **port 8010** (engine CLI
  default is 8000), passing the KB path
  `content/meridian/meridian_knowledge_base.md`; failures are warn-only so the
  app degrades to Chroma
- `.env:194` → `RAG_MCP_URL=http://127.0.0.1:8010/mcp`

### Two findings worth remembering

1. universityDemo only ever calls the **`retrieve_context`** tool. The engine's
   `execute_agent_context` / orchestrator path exists but is unused from this
   repo.
2. `scripts/migrate_rag_to_erc.py` moves the KB from the legacy Chroma store into
   the engine — relevant if the KB was updated locally and the engine's copy is
   stale.

---

<a name="q2"></a>
## Q2 — Can this be hosted on Hugging Face?

**Verdict: yes, a Docker Space is the right vehicle, and more maps cleanly than
expected — but it is a re-architecture, not a config change.**

HF Spaces gives you **one public port, ephemeral disk, and UID 1000**. This repo
is currently a local GPU appliance spanning 8 processes and 5 ports.

### What maps cleanly

| Current | On a Space |
|---|---|
| 8 services on :8000, :8010, :8098, :8501, :8502, :11434, :5432, :6379 | Only `app_port` is public — **internally you can run as many ports as you want** (HF docs explicitly cite running Elasticsearch on 9200 internally). ERC on :8010 and the CRM API on :8098 stay as-is |
| Cloudflare tunnel (Steps 7–9) | **Deleted.** `.hf.space` is a stable HTTPS hostname — strictly better for Twilio webhooks than a rotating quick tunnel |
| `.env` (14 KB of secrets, already gitignored at `.gitignore:2`) | Space Secrets → runtime env vars, no code change |
| `chroma_local_db/` (730 KB) | Bake into the image |
| `USE_MCP_RAG=auto` | Keep as-is — the fallback still protects you |

### The three decisions that matter

**1. The LLM — the crux.** Ollama *can* run inside a Docker Space on :11434
internally. The problem is cost and cold starts: CPU Basic (2 vCPU) gives a few
tokens/sec — unusable for voice; a GPU tier works, but model weights sit on
**ephemeral disk wiped on every restart**, so you either re-pull gigabytes each
cold start or bake them into the image.

The cleaner move is **HF Inference Providers**, whose router at
`https://router.huggingface.co/v1` is OpenAI-compatible. `app/llm_backend.py:371`
**already has the OpenAI code path** — it is what the MLX provider uses. So this
is adding a third branch to `provider_name()`, not a rewrite.

> **Trap:** that OpenAI-compatible endpoint is **chat-completions only**.
> Embeddings must go through `InferenceClient.feature_extraction` instead — and
> embeddings are needed in two places: `app/llm_backend.py:311,338` and inside
> the ERC engine (its `docker-compose.yml` notes "ollama embeddings").

**2. Postgres+pgvector and Redis Stack.** Both currently run as compose services.
In a Space there is one image, so either they run in-container (data dies on
restart) or you point at managed services. The docs are explicit that the old
persistent-storage feature **is removed** — `suggested_storage: small|medium|large`
"will be ignored". Replacement is Storage Buckets mounted at `/data`, which is
**runtime-only, unavailable at build**.

**3. The voice stack.** Pipecat + faster-whisper + kokoro-onnx on CPU Basic will
not hold a real-time phone conversation.

- **CPU Basic (free hardware):** web chat, RAG, dashboards — yes. Phone line — no.
- **GPU (T4-small $0.40/hr, L4 $0.80/hr):** voice works, but ~$292/mo if always on.
- **Or:** move STT/TTS to APIs too, keeping everything on CPU.

### Two functional gotchas

- **Free Spaces sleep after 48h idle** and restart on visit. A real phone number
  ringing a sleeping Space = dead air. Paid hardware runs indefinitely by default.
- **Compute Spaces need a paid HF plan even on free CPU hardware** — only Static
  Spaces are free for everyone.

Also: Spaces are **public by default**, and this app carries student leads and
Salesforce CRM data. Make it private.

### Concrete changes

1. **New Dockerfile** — the existing one will not work: it runs as root, installs
   into `/root/.local`, and has no `--chown=user`. Spaces run as **UID 1000**, so
   you need `useradd -m -u 1000 user`, `WORKDIR` before `COPY`,
   `COPY --chown=user`. Also: **no GPU at build time**, so install CPU torch in
   the image.
2. **supervisord** to run FastAPI + ERC + (pg/redis if in-container) in one container.
3. **`README.md` with YAML** at the Space root: `sdk: docker`, `app_port`,
   `suggested_hardware`, and a raised `startup_duration_timeout` (default 30 min —
   torch + whisper import is slow).
4. **Replace `start_services.ps1`** with a container entrypoint. Steps 1–2 (kill
   stale processes, verify ports free) are meaningless in a container; Steps 7–9
   collapse away.
5. **ERC placement** — either vendor/clone at build, or run as a **second Space**
   and set `RAG_MCP_URL=https://<user>-erc.hf.space/mcp`. The MCP boundary makes
   that a one-line change.
6. **Streamlit 8501/8502** — one public port only, so behind nginx, or fold the
   dashboard into FastAPI.
7. **`preload_from_hub`** for `Systran/faster-whisper-tiny.en` and the ERC reranker.
8. **Twilio webhook** — static `.hf.space` hostname replaces the tunnel-derived URL.

---

<a name="q3"></a>
## Q3 — Which cloud for a customer demo, and what does it cost?

**Given constraints (confirmed by the operator):** voice is required, and the
customer calls only during scheduled demo windows — the system can be stopped
between demos.

### Verdict: GCP Cloud Run with an L4 GPU, scale-to-zero

This is the best-case shape for serverless GPU — scheduled demos plus the ability
to stop between them is exactly what it was built for, and it is the one thing
neither AWS nor Azure offers at the L4 tier.

### Verified pricing

| Resource | Rate |
|---|---|
| NVIDIA L4 (Cloud Run, no zonal redundancy) | $0.672/hr |
| vCPU | $0.0648/hr each |
| Memory | $0.0072/GiB/hr |

- 8 vCPU / 32 GiB / 1×L4 = **$1.42/hr** (matches the current 6-core/32 GB box,
  with 24 GB VRAM vs the 16 GB available locally — so the workload is already
  proven to fit)
- 4 vCPU / 16 GiB / 1×L4 = $1.046/hr (the documented minimum)

### Demo cost

| | |
|---|---|
| One 2-hour demo session | **~$2.84** |
| 4 sessions/month | **~$11** |
| 10 sessions/month | **~$28** |
| Idle between demos | **$0** |

Plus roughly: Artifact Registry image storage $1.50–2/mo, Twilio number $1–2/mo,
and ~$0.013/min for inbound + Media Streams (a 15-min call ≈ $0.19).

**Realistic total: $15–35/month.**

### Architecture: one service

Same pattern as the Space — only FastAPI is public:

```
Cloud Run service (public :8000)
├── FastAPI           :8000   ← public
├── Ollama            :11434  ← localhost
├── CRM API           :8098   ← localhost
└── ERC MCP           :8010   ← localhost (optional)
```

```bash
gcloud run deploy admissions-demo \
  --gpu 1 --gpu-type nvidia-l4 --no-gpu-zonal-redundancy \
  --cpu 8 --memory 32Gi \
  --min-instances 0 --max-instances 1 \
  --no-cpu-throttling --timeout 3600 \
  --port 8000 --region asia-south1
```

`--max-instances 1` is the cost guardrail. `--no-gpu-zonal-redundancy` gets the
lower per-GPU-second rate. The 60-minute request ceiling is fine — demo calls are
short.

### The runbook

Because demos are **scheduled**, cold starts are pre-empted rather than fought:

1. **~20 min before the call:** `gcloud run services update admissions-demo --min-instances=1`
2. **Verify warm:** hit `/health`, then fire one throwaway RAG query so Ollama
   actually loads the model into VRAM (container started ≠ model resident)
3. **Take the call**
4. **After:** `gcloud run services update admissions-demo --min-instances=0`

A 3-hour warm window costs ~$4.26.

### Four gotchas to handle before demo day

1. **Request GPU quota early.** GPU quota is per-project, **per-region**, and is
   often 0 by default. Can take a day or more — do it now, not the morning of the
   demo. GPU regions: us-central1, europe-west1, europe-west4, asia-southeast1,
   asia-south1 (Mumbai).
2. **`requirements.txt` will install CPU-only torch in a Linux container.** This
   is the one that would silently wreck the demo. `requirements.txt:18` says
   `torch>=2.7.0,<2.8` is the PyPI (CPU) build, with the cu128 variant installed
   separately by the Windows bootstrap for RTX 50-series Blackwell. **L4 is Ada
   (sm_89), so the cu128 workaround does not apply** — but a CUDA index must be
   added for the Linux image, or whisper runs on CPU and the demo falls over.
   This is a simplification, not extra work.
3. **Set `OLLAMA_KEEP_ALIVE=-1`.** Ollama unloads models after ~5 idle minutes by
   default. Between demo calls the 14B must stay pinned in VRAM.
4. **The 4-minute startup deadline.** A Cloud Run container must be listening
   within 4 minutes regardless of `--timeout`. `app/rag.py:205 warmup()` is
   already written to never raise — keep it off the critical path so heavy imports
   do not block the socket.

**Pre-pull the model in the Dockerfile** (`RUN ollama pull qwen2.5:14b`, ~9 GB).
Reported cold start drops to roughly 10s with the model baked in versus 60s+
without.

### Simplification worth taking

For the demo, set **`USE_MCP_RAG=off`** — local Chroma only (730 KB, baked into
the image). That drops Redis Stack entirely and shortens startup. The dispatcher
at `app/rag.py:100` already supports this cleanly, and ERC is internal
infrastructure the customer never sees. Bring it back for the full deployment.

**Postgres** remains the open item: managed Cloud SQL starts around $10–50/mo,
tripling the bill. If demo leads need not survive the session, run it in-container
and seed on boot. If they do, Neon's free tier is the cheap route.

### Why not the other two

**Azure — weakest fit.** Container Apps serverless GPU supports **only T4 and
A100; there is no L4 option**. Forced onto a 16 GB T4 (too tight for a 14B plus
the voice stack, and 3–5× slower — 30–180 s/request vs sub-10 s on A100) or an
A100 at ~$2.40/hr always-warm ≈ **$1,700/mo**. Azure's docs also state you **pay
the entire GPU cost even if the app uses only a fraction of it**. The one argument
for Azure is organizational — this toolchain is Windows/PowerShell-based, so if
the customer is Microsoft-committed it fits culturally.

**AWS — middling.** **Fargate has no GPU support at all** — `gpu` is explicitly
listed as invalid in Fargate task definitions, and the task-size model has no GPU
dimension.

| Instance | GPU | On-demand/hr | Spot/hr |
|---|---|---|---|
| `g4dn.xlarge` | T4 16 GB | $0.526 | $0.16–0.26 |
| `g6.xlarge` | L4 24 GB | $0.80 | $0.29–0.49 |

**Do not use spot for a live customer demo** — a reclaim mid-call means dead air
in front of the customer. ECS Managed Instances (GA Sept 2025) supports GPU if a
managed lifecycle is wanted.

### Honest framing

The current setup — desktop + Cloudflare tunnel + Twilio webhook update
(`start_services.ps1` Steps 7–9) — **already does a scheduled phone demo today for
$0**. What the cloud buys is removing "is my desktop free, and is the tunnel up?"
from demo day, plus a URL that survives a reboot. For a customer-facing call that
is worth $15–35/month, but that is the thing being bought — not capability that
does not already exist.

**Recommendation:** keep the local setup as the fallback for the first demo or two.
If Cloud Run misbehaves on the call, switch to the tunnel and keep going.

### Hardware detection — a corrected finding

Initially suspected that deploying to a cloud GPU VM would mis-detect and apply
the conservative `cloud_linux` tier. **Verified false.** `app/hardware_profile.py:185`
computes `cloud` only when `platform == "cpu"`, and the NVIDIA branch at `:172`
sets `platform = "nvidia"` first. So a cloud GPU VM correctly detects as `nvidia`
and applies the `nvidia_high` tier (`qwen2.5:14b`, `small.en` whisper) with no
changes needed.

The residual risk is narrower: if `nvidia-smi` is unavailable inside the container
(no GPU passthrough, or driver not visible), `_nvidia_gpu()` returns None, the
platform falls to `cpu`, and the conservative tier applies *while a GPU is present*.

---

<a name="q4"></a>
## Q4 — The reusable low-latency sizing prompt

**Saved separately: `doc/LOW_LATENCY_SIZING_PROMPT.md`** — the full copy-paste
block, a table of which elements are load-bearing if edited, three sanity-check
tests, and what to expect when comparing models.

Summary of what it does: a self-contained prompt to paste into multiple LLMs
(ChatGPT, Gemini, Claude) to obtain an independent hardware + configuration spec
for running the voice and chat paths at natural conversational latency.

Key design decisions embedded in it:

- **"0 lag" was replaced with a measurable budget** — turn latency defined as
  caller-stops-speaking → first syllable heard, target **p50 ≤ 700 ms,
  p95 ≤ 1200 ms**. This is the fix that makes multi-LLM comparison possible at
  all; without a shared number, each model invents its own target and the outputs
  cannot be diffed.
- **VAD endpointing is named as its own budget row** — the hidden 300–500 ms most
  models omit, and often the cheapest thing to fix.
- **Fixed seven-section output schema** so answers land in comparable tables.
- **Basis-labelling required** (measured / published spec / estimate) to separate
  real benchmarks from invented precision.

Expected outcome: models will disagree most on **whether to keep the 14B model**.
That is the real fork — a 14B co-resident with whisper and kokoro on a 16 GB card
is the dominant term in the latency budget. Some models will argue for 7B, others
for moving the LLM to a hosted API. Neither is wrong; the disagreement is the
useful output.

---

<a name="appendix-a"></a>
## Appendix A — Environment variables per deployment target

Only variables actually read from the repo during this session are listed.

| Variable | Current value / default | Changes on a hosted target? |
|---|---|---|
| `USE_MCP_RAG` | `auto` (`.env:192`) | Set `off` for the demo deploy; `auto` for full |
| `RAG_MCP_URL` | `http://127.0.0.1:8010/mcp` (`.env:194`) | Unchanged if ERC is in-container; points at the ERC Space/Cloud Run URL if split out |
| `RAG_MCP_TIMEOUT` | `2.5` (`.env:196`) | Possibly raise over a network hop |
| `RAG_MCP_COOLDOWN` | `30` (`.env:197`) | Unchanged |
| `OLLAMA_URL` | `http://localhost:11434` (`.env:26`) | Unchanged if Ollama is in-container |
| `OLLAMA_KEEP_ALIVE` | not set | **Set to `-1`** on any hosted GPU target |
| `MACHINE_CLOUD` | not set | Optional override forcing cloud detection |
| `DATABASE_URL` / `DB_USER` | read in `app/database.py:43,48` | Points at a managed DB, or omitted for in-container |

`app/config.py` reads these through `_env()` with defaults, so absent values fall
back cleanly.

---

<a name="appendix-b"></a>
## Appendix B — Key file and line references

| Reference | What it is |
|---|---|
| `app/rag.py:93,117,153` | MCP-first dispatcher: `retrieve_context`, `get_retriever`, `query_rag` |
| `app/rag.py:100` | `USE_MCP_RAG=off` short-circuit to legacy |
| `app/rag.py:103` | MCP-first attempt, `[]` treated as legitimate empty result |
| `app/rag.py:205` | `warmup()` — FastAPI lifespan, never raises |
| `app/rag_mcp.py:163` | The `retrieve_context` MCP tool call |
| `app/rag_legacy.py:446` | Local Chroma fallback retrieval |
| `app/llm_backend.py:311,338` | Ollama embedding functions (must move if LLM goes hosted) |
| `app/llm_backend.py:371` | Existing `ChatOpenAI` path (the MLX provider) — reuse for HF/OpenAI-compatible |
| `app/hardware_profile.py:172` | NVIDIA branch sets `platform = "nvidia"` |
| `app/hardware_profile.py:185` | `cloud` requires `platform == "cpu"` — why GPU cloud VMs detect correctly |
| `app/hardware_profile.py:280` | `cloud_linux` conservative tier |
| `requirements.txt:18` | torch pin — the CPU-build trap for Linux containers |
| `start_services.ps1:507,704` | ERC clone (Step 4b) and launch on :8010 (Step 6b) |
| `scripts/migrate_rag_to_erc.py` | KB migration from legacy Chroma into the engine |
| `.machine_profile.json` | Applied profile: `nvidia_high`, qwen2.5:14b, num_ctx 8192 |

---

<a name="appendix-c"></a>
## Appendix C — Sources

Platform limits and pricing were verified against current documentation rather
than recalled:

**Hugging Face**
- [Spaces configuration reference](https://huggingface.co/docs/hub/spaces-config-reference) — `app_port`, hardware flavors, persistent-storage removal note
- [Docker Spaces](https://huggingface.co/docs/hub/spaces-sdks-docker) — UID 1000, internal ports, data persistence
- [Spaces GPU and hardware](https://huggingface.co/docs/hub/spaces-gpus) — tier specs, 48h free-tier sleep, billing
- [Inference Providers](https://huggingface.co/docs/inference-providers/index) — OpenAI-compatible router, chat-only limitation

**Google Cloud**
- [Cloud Run GPU configuration](https://docs.cloud.google.com/run/docs/configuring/services/gpu)
- [gcloud run deploy reference](https://docs.cloud.google.com/sdk/gcloud/reference/run/deploy)
- [Ollama on Cloud Run GPU](https://ossaihub.com/code/cloud-run-llm-with-gpu/)
- [Cloud Run GPU lab quickstart](https://github.com/amitkmaraj/accelerate-ai-lab3-complete/blob/main/QUICKSTART.md)
- [Cloud Run GPU cost case study](https://zenn.dev/ccie26302/articles/zenn-gcp-cloud-run-gpu-ollama)

**AWS**
- [GPU on ECS](https://aws-samples.github.io/sample-apex-skills/docs/skills/ecs/ecs-genai/references/compute-hardware/)
- [Fargate service boundaries](https://aws-samples.github.io/sample-apex-skills/docs/skills/ecs/ecs-genai/references/service-boundaries/)
- [EC2 GPU pricing comparison](https://cloudprice.net/aws/ec2/compare/g4dn.xlarge-vs-g6.xlarge)

**Azure**
- [Container Apps GPU types](https://github.com/MicrosoftDocs/azure-docs/blob/main/articles/container-apps/gpu-types.md)
- [Serverless GPU on Container Apps](https://azurefeeds.com/2026/04/21/running-multimedia-ai-models-on-container-apps-with-serverless-gpu-a100-t4/)

---

## Status

Nothing was deployed, built, or changed in this session. All four questions were
answered from codebase reading plus external-doc verification. The two files
written are this log and `doc/LOW_LATENCY_SIZING_PROMPT.md`.
