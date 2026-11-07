# Dense chunk-retrieval evaluation

This directory holds the hand-labeled query set used to decide whether dense
chunk embeddings may be enabled. It is **offline-only**: run it against a
disposable snapshot, never against the serving database.

## Why

The production retrieval path is BM25-only (`EMBEDDING_PROVIDER=off`). Dense
embeddings can improve paraphrase recall, but the current implementation
(`GraphStore.search_chunk_embeddings`) loads every vector and computes cosine
similarity in Python for each query. Before any rollout we must show:

- recall@10 on the labeled set is at least **90%**;
- hybrid retrieval does not lose lexical hits;
- p95 retrieval latency stays inside the chat/voice budgets (`<3 s` chat,
  `<1.5 s` voice retrieval/cache).

## Build the evaluation snapshot

```bash
SERVE=content/dspmu/nga_graph.sqlite3
EVAL=/tmp/nga_dense_eval.sqlite3

sqlite3 "$SERVE" ".backup '$EVAL'"
EMBEDDING_PROVIDER=openai \
  .venv/bin/python scripts/nga_chunks.py \
  --db "$EVAL" --backfill --embed --json
```

`--embed` uses the approved OpenAI boundary and batches of 64. The live
provider stays off; the environment override applies only to this process.

## Run

```bash
EMBEDDING_PROVIDER=openai \
  .venv/bin/python scripts/nga_embedding_eval.py \
  --db /tmp/nga_dense_eval.sqlite3 \
  --queries eval/retrieval/chunk_queries.jsonl \
  --report /tmp/dense_eval.json
```

The report contains per-query lexical/dense/hybrid ranks and top URLs, plus
hit@5, hit@10, MRR and p50/p95 latency for each mode.

## Reading the result

- `lexical`: production BM25 chunk retrieval.
- `dense_only`: cosine ranking over all embedded chunks.
- `hybrid`: BM25 + dense fused with RRF (the proposed rollout).
- A query counts as a hit when any `expected_urls` fragment appears in the
  retrieved chunk's source URL.
- `hybrid_regressions` are queries where lexical found the expected source in
  the top-k but hybrid did not. A rollout must not introduce these.

The query set is deliberately small and hand-labeled; it is a release gate,
not a training set. Held-out acceptance cases are not used here.

## Result — 2026-11-01

Hybrid did **not** clear the gates: hit@10 **14/17 (82.4%)** versus the 90%
target, dense scan p95 **3.3 s**, and dense-enabled acceptance p95 **9.2 s**
(1 run) versus 5.3 s lexical (3 runs), with citation drift in five cases. Dense
remains off. Full results and remediation:
`doc/sdlc/nga/10-dense-evaluation.md`.

## Decision rule

Keep `EMBEDDING_PROVIDER=off` and
`NGA_CHUNK_RETRIEVAL_ENABLED=true` (BM25) until the recall and latency gates
pass. The rollback for a bad lexical rollout remains
`NGA_CHUNK_RETRIEVAL_ENABLED=false`.
