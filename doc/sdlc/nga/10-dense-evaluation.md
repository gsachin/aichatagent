# Dense chunk-retrieval evaluation — 2026-11-01

## Decision

**Do not enable dense embeddings.** Keep `EMBEDDING_PROVIDER=off` and the
production path on BM25 chunk retrieval. The evaluation missed the recall gate
and the latency is an order of magnitude outside the retrieval budget.

## Scope

| Item | Value |
|---|---|
| Snapshot | `/tmp/nga_dense_eval.sqlite3` (disposable copy of serving) |
| Chunks | 21,224 (667 page + 20,557 document) |
| Embedding model | `text-embedding-3-small`, 1536 dimensions |
| Vectors built | 21,224 / 21,224 |
| Labeled queries | 17 (`eval/retrieval/chunk_queries.jsonl`) |
| Query mix | 12 lexical anchors, 5 paraphrases |
| Evaluation script | `scripts/nga_embedding_eval.py` |

Relevance is the presence of a hand-labeled expected source URL in the
retrieved chunk's node URL. The set is a release gate, not a training set.

## Retrieval results

| Mode | hit@5 | hit@10 | MRR | p50 latency | p95 latency |
|---|---:|---:|---:|---:|---:|
| Lexical BM25 | 12/17 | 13/17 (76.5%) | 0.7124 | 103 ms | 223 ms |
| Dense only | 14/17 | 14/17 (82.4%) | 0.6999 | 3,007 ms | 3,293 ms |
| Hybrid (BM25 + dense RRF) | 14/17 | 14/17 (82.4%) | 0.7176 | 3,322 ms | 3,396 ms |

Query embedding: **976.5 ms for 17 queries** (about 57 ms per query).

- Gains: **R02** (“financial help for undergraduate students”) and **R17**
  (“examination fee submission remote sensing GIS semester 1”).
- Regression: **R13** (“UG PG end semester examination schedule 2022”) was
  found at lexical rank 9 and lost by hybrid.
- Still missed by every mode: **R14** (“end term exam dates”) and **R16**
  (“B.A. History fourth provisional admission list”).

The HLD gate is **recall@10 ≥ 90%**; hybrid reached **82.4%**.

## Answer-level check

Dense enabled on the same snapshot, one acceptance run:

- **30/30 cases, 35/35 turns** — all existing assertions still pass.
- **p95 9,186 ms** versus 5,324 ms on the lexical 3-run report.
- 0 false refusals, 0 fallbacks, 15 deterministic-builder turns.
- Citation sets changed in **A13, A14, A15, A18 and A19**. A19’s answer was
  still the verified portal answer, but its references gained unrelated
  admission selection-list PDFs. This is citation-quality drift even where the
  required fact passes.

## Why it is blocked

1. **Recall**: hybrid misses the 90% gate and loses a lexical hit.
2. **Latency**: `GraphStore.search_chunk_embeddings` loads every vector and
   computes cosine similarity in Python for each query. At 21k chunks the dense
   scan alone is ~3 s p95; voice retrieval/cache is budgeted at <1.5 s and chat
   p95 is already above 3 s before generation.
3. **Citation quality**: extra/irrelevant document citations appear in five
   acceptance cases, even though assertions pass.

## Remediation before re-evaluation

1. Replace the full-table Python cosine scan with an ANN/vector index
   (`sqlite-vec`, FAISS, or a cached NumPy matrix with candidate limits).
   Target dense scan p95 < 200 ms.
2. Guarantee lexical hits are preserved in fusion and tune the RRF/rerank
   stage so R13-class regressions cannot happen.
3. Improve recall for date- and list-shaped queries (R14/R16), e.g. query
   expansion, date-aware ranking, and OCR quality on scanned notices.
4. Re-run `scripts/nga_embedding_eval.py` and the acceptance set with dense
   enabled on a snapshot; add a citation-precision check before any canary.

## Reproduction

```bash
sqlite3 content/dspmu/nga_graph.sqlite3 ".backup '/tmp/nga_dense_eval.sqlite3'"
EMBEDDING_PROVIDER=openai \
  .venv/bin/python scripts/nga_chunks.py \
  --db /tmp/nga_dense_eval.sqlite3 --backfill --embed --json
EMBEDDING_PROVIDER=openai \
  .venv/bin/python scripts/nga_embedding_eval.py \
  --db /tmp/nga_dense_eval.sqlite3 \
  --queries eval/retrieval/chunk_queries.jsonl \
  --report /tmp/dense_eval.json
```

The live `.env` remains `EMBEDDING_PROVIDER=off`; the override applies only to
the disposable evaluation process. Rollback for lexical chunk retrieval itself
remains `NGA_CHUNK_RETRIEVAL_ENABLED=false`.
