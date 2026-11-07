# DSPMU chat acceptance set

This is the frozen behavioural gate for the navigation-grounded answer path.
It is separate from the content golden set in `eval/dspmu_golden_set.jsonl`:
that set checks whether the knowledge base is right; this set checks whether a
real chat turn reaches the right state, facts and citations.

## Files

- `cases.jsonl` — 30 tuned cases. Run these while fixing a general cause.
- `heldout.jsonl` — 20 held-out cases. **Do not run during development.** The
  runner refuses them unless `--release --include-heldout` is passed.
- `adversarial.jsonl` — 10 adversarial cases (injection, PII, stale dates,
  empty shells, wrong entity, hallucinated fee, nonsense). Run them with
  `--release --include-adversarial` (held-out is separate).

Each line is one case:

```json
{
  "id": "A01",
  "group": "programme list",
  "query": "which programs currently university offers",
  "expect": {
    "outcome": "answer",
    "required": ["Undergraduate", "Postgraduate", "BA History"],
    "forbidden": ["NEP", "NSS", "no verified information"],
    "required_urls": ["academic-programmes"],
    "no_negative_cache": true
  },
  "ci": true
}
```

Supported fields:

- `query` or `variants`: one utterance, or several that must all pass.
- `prelude`: earlier turns in the same conversation (conversation memory).
- `repeat`: run the same utterance N times in one conversation (cache check).
- `channels`: run each channel and compare the outcome and citation set.
- `language`: language code passed to the answer seam.
- `llm_response` / `llm_raises`: scripted model output or failure.
- `force_llm`: skip the deterministic fee builder so the verifier is exercised.
- `expect.outcome` / `outcomes`: `answer`, `handoff`, `clarification`,
  `refusal`, `error`, or `fallback`.
- `expect.required` / `forbidden`: case-insensitive facts; numeric commas are
  ignored, so `3410` matches `₹3,410`.
- `expect.required_urls` / `forbidden_urls`: URL fragments.
- `expect.min_citations`, `no_citations`, `no_retrieval`, `intent`,
  `require_topic`, `max_latency_ms`, `require_event`, `no_negative_cache`.
- `expect.builder`: the deterministic builder that must have produced the
  answer (`admission`, `fee`, `notice`, `programme`, `programme_list`,
  `browse`). A critical-fact case fails if a silent fallback to the model
  produced the text instead.
- `ci`: whether the case runs in the offline CI fixture. CI cases must not
  call the model or the network.

## Run

```bash
# CI gate: deterministic, fixture graph, no network, no model
.venv/bin/python -m pytest tests/test_acceptance.py -q

# Tuned baseline on a consistent snapshot of the live graph (default)
.venv/bin/python scripts/run_acceptance.py --ci-only
.venv/bin/python scripts/run_acceptance.py --llm live --no-offline --runs 3 \
  --report /tmp/acceptance.json

# Release report: unlocks the held-out set
.venv/bin/python scripts/run_acceptance.py --llm live --no-offline --runs 3 \
  --release --include-heldout --report /tmp/acceptance-release.json
```

The runner snapshots the graph with SQLite's backup API and clears the answer
cache by default. `--no-snapshot` and `--keep-cache` are for an operator who
explicitly wants to exercise the live cache.

## Baseline

Recorded 2026-10-31 on the live graph snapshot, three runs at temperature 0:

| Set | Cases | Turns | Result |
|---|---|---|---|
| Tuned (`cases.jsonl`) | 30 | 105 | **105/105 passed (100%)** |
| Held-out (`heldout.jsonl`) | 20 | — | Not run; release only |
| Adversarial (`adversarial.jsonl`) | 10 | 10 | **10/10 passed** (1 run) |

The report now records `data_state`: graph hash, chunk count, chunk index
version and a case-file digest, so a pass rate can be tied to the data it ran
against. The baseline found and closed seven general gaps: over-broad programme-claim
extraction in the verifier, the PG boundary swallowing named PG programmes,
the live PG boundary running before the agentic path, hand-off citations not
seeding conversation topics, missing admissions-portal evidence, LLM-dependent
fee/notice answers, and nonsense turns triggering deep search.
