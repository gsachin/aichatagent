# Conversation storage map and write-path performance

This is the authoritative map of where each conversation record lives, which
key joins the stores, and how much the NGA writes cost the chat path.

The design set is in `doc/sdlc/nga/`; operational procedures are in
`doc/ops/nga-runbook.md`.

---

## 1. Storage map

| Data | Store | Key | Written by | When |
|---|---|---|---|---|
| Full transcripts for Streamlit, WhatsApp, inbound/outbound calls | Postgres `conversations` | `conversation_id`, `lead_id`, `phone_number` | `app/leads/service.py:log_interaction` via `/api/interactions/log`, FastAPI `BackgroundTasks`, or the post-call handler | Async/off the answer path |
| Lead, language, CRM link | Postgres `leads`, CRM link tables | `lead_id`, phone | `upsert_lead_by_phone`, CRM sync | Request start/end, async |
| NGA structured turns, plan, tool calls, evidence IDs and citations | SQLite `nga_turn_event` in the graph DB | `conversation_id` + `turn_id` | `app/agent/text.py:build_text_answer` → `ConversationMemory.record` | Synchronous before the chat answer returns |
| Voice→WhatsApp delivery attempts | SQLite `nga_reference_delivery` in the graph DB | `(call_sid, node_id)`, indexed by phone hash | `app/references/delivery.py` worker thread | After the voice turn, off the call |
| Shadow observations | JSONL `logs/nga_shadow.jsonl` | append-only | shadow worker | Off the answer path |
| Refresh/alerts | JSONL `logs/nga_refresh.jsonl`, `logs/nga_alerts.jsonl` | append-only | refresh scheduler | Background |
| Perf traces | JSONL `logs/perf_turns.jsonl` | append-only | `app/perf_trace.py` | Background |
| CRM/WhatsApp session state | In-memory session registry | session key | `app/crm/session.py` | Not durable; Postgres is authoritative after a turn |

### Identity

The same `conversation_id` flows through both durable stores for text:

```
Streamlit: st.session_state.conversation_id
  -> nga_turn_event.conversation_id
  -> POST /api/interactions/log -> conversations.conversation_id

WhatsApp: crm_session.conversation_id (resolved from the inbound number)
  -> nga_turn_event.conversation_id
  -> BackgroundTask _link_crm_and_log -> conversations.conversation_id

Voice: crm_session.conversation_id
  -> conversations.conversation_id
  -> nga_reference_delivery.conversation_id (alongside call_sid)
```

There is **no foreign key** between the SQLite graph DB and Postgres. The
`conversation_id` is the join key; `call_sid` identifies a voice call in the
delivery log.

### What is not stored where

- `nga_turn_event` is the NGA event-sourced memory, **not** the legal
  transcript. The full text is in Postgres `conversations`.
- Voice turns do not currently write `nga_turn_event`. Their full transcript is
  in Postgres, and their reference deliveries are in
  `nga_reference_delivery` keyed by `call_sid`.
- `nga_reference_delivery` stores no message body and no full phone number:
  only a SHA-256 phone hash, the last four digits, the exact reference URL and
  the delivery status.
- The reference delivery queue and a `call_end` pending batch are in memory.
  A process restart loses queued work; completed attempts are already in the
  delivery log.
- CRM session registry state is in memory. Postgres rows are the durable
  record.

### Retention

| Store | Setting | Purge |
|---|---|---|
| `nga_turn_event` | `NGA_EVENT_RETENTION_DAYS` (90) | `GraphStore.purge_turn_events`; runbook R12 |
| `nga_reference_delivery` | `NGA_REFERENCE_LOG_RETENTION_DAYS` (30) | `scripts/nga_references.py --purge`; service purge on start |
| Postgres `conversations` | Owned by the CRM/leads retention policy | Outside this map; do not use NGA settings |

### Consistency checks

```sql
-- NGA events per conversation (SQLite)
SELECT conversation_id, COUNT(*), MIN(created_at), MAX(created_at)
FROM nga_turn_event GROUP BY conversation_id ORDER BY MAX(created_at) DESC;

-- Reference delivery outcomes (SQLite)
SELECT status, COUNT(*) FROM nga_reference_delivery GROUP BY status;

-- Full transcripts (Postgres)
SELECT id, channel, created_at FROM conversations
WHERE id = '<conversation_id>';
```

A text conversation should normally have one Postgres row for the thread and
one or more `nga_turn_event` rows with the same id. A missing NGA row means the
turn was not an NGA agentic turn; it does not mean the transcript is missing.

---

## 2. Write-path performance

### Methodology

`scripts/bench_nga_events.py` writes 200 real turns through
`ConversationMemory.record` against a fresh SQLite graph database. Each
measurement includes:

- loading the previous six events for the same conversation,
- `next_turn_id`,
- serializing plan/tool calls/citations/session state,
- the insert and commit.

The synthetic turn has the same `Plan` / `ExecutionResult` /
`EvidenceBundle` / `AnswerResult` shape as a real cited chat answer.

Reproduce:

```bash
.venv/bin/python scripts/bench_nga_events.py --iterations 200 --json
```

### Measured result — 2026-10-25

Development machine, local SQLite, 200 iterations:

| Metric | Result |
|---|---|
| p50 | **1.751 ms** |
| p95 | **3.567 ms** |
| p99 | **10.930 ms** |
| mean | **2.260 ms** |
| max | **34.573 ms** |
| throughput | **442.5 writes/s** |

### Interpretation against the chat budget

The text chat budget is `NGA_TEXT_DEADLINE_MS=8000` (8,000 ms). The turn-event
write is synchronous on the chat thread, but:

| Metric | Share of 8,000 ms budget |
|---|---|
| p50 1.751 ms | 0.022% |
| p95 3.567 ms | **0.045%** |
| p99 10.930 ms | **0.137%** |
| max 34.573 ms | 0.432% |

The write is inside the chat budget by more than three orders of magnitude at
p95. It is kept synchronous deliberately: the next turn's session memory must
see the previous turn immediately. Moving it to a background queue would save
single-digit milliseconds and introduce a follow-up context race.

This is a development-machine measurement, not a production SLA. Re-run the
benchmark on the reference hardware before a latency gate; store the new table
here with the date and machine.

### Why the call path is unaffected

| Work | Thread / timing |
|---|---|
| NGA turn-event write | Chat request thread, after generation, p95 ≈ 3.6 ms |
| Streamlit transcript log | Daemon thread in `app.py` (`_log_and_score_async`) |
| WhatsApp transcript + CRM log | FastAPI `BackgroundTasks` after TwiML |
| Voice transcript | Post-call teardown, after the call |
| Voice→WhatsApp registration, template decision, send, retry | Delivery worker thread; never awaited by the call |
| WhatsApp over-1,600-character reference follow-up | `crm.tasks.spawn`, after the webhook answer |
| Document/PDF extraction and OCR | Refresh worker only |
| Shadow observation | Bounded shadow worker; answer unchanged |

The voice path does not write `nga_turn_event`; its cost is limited to the
in-memory enqueue in `VoiceCallSession.queue_reference_delivery`, which is a
dictionary/queue operation with no network or DB call.

### Guardrail

`tests/test_nga_persistence.py` re-runs a smaller write loop and fails only on
a gross regression (p95 above 100 ms), not on normal machine variance. The
benchmark script remains the source for the reported figures.
