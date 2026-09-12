# Status & Manual Validation — Salesforce User Sync

**Branch:** `salesforce-user-api-integration` · **Updated:** 2026-09-11
**Plan:** [`PLAN_SALESFORCE_USER_INTEGRATION.md`](PLAN_SALESFORCE_USER_INTEGRATION.md)
**Intent (plain English):** [`PO_INTENT_SALESFORCE_USER_SYNC.md`](PO_INTENT_SALESFORCE_USER_SYNC.md)

---

## 1. Where the work stands

| Phase | State | Gate |
|---|---|---|
| **0** — Verify & stage the upstream API | ✅ **Done** | **Gate 0 passed** — 18 probes, 0 FAIL |
| **1** — Conversation identity | ✅ **Done** | **Gate 1 passed** — 101 tests, zero behaviour change |
| **2** — CRM client | ✅ **Done** | **Gate 2 passed** — nothing leaves the process when disabled |
| **3** — WhatsApp channel | 🟡 **Code complete; Gate 3 run live and FAILED on R18** | See §1a |
| **4** — Outbound voice | ⬜ Not started | |
| **5** — Inbound voice | ⬜ Not started | ⚠️ Gate 5 is mis-worded — see §4 |
| **6** — Web chat | ⬜ Not started | |
| **7** — Status push | ⬜ Not started | |
| **8** — Hardening & rollout | ⬜ Not started | |

**Commits** (5 of mine, on top of the ERC work):

```
4f8c347  fix(crm): WhatsApp session lookup key never matched
c0e6373  feat(crm): Phase 3 — WhatsApp is the first channel wired to the CRM
0510f83  feat(crm): Phase 2 — client, identity mapping, outbox, sync entry points
f750fc2  fix: outbound calls were logged with channel="inbound_call"
5dee0b5  feat(crm): Phase 1 — stable conversation identity from session start
e3bb18d  feat: Salesforce user-API integration plan + Phase 0 verification harness
```

**Scale:** 22 files, ~6,450 lines added. 101 new tests. Full suite 310 passed / 0 failed.

**Crucially: `CRM_ENABLED` is `false` by default.** Nothing in production behaves
differently today. Every phase so far is inert until someone flips that switch.

---

## 1a. Gate 3 — run live 2026-09-11, and it found a blocker

`scripts/verify_gate3_whatsapp.py` drives the real `/twilio/whatsapp` route against a real
uvicorn server and reads the result back through `GET /admissions`. Last run: 9 clauses,
**3 FAIL**, 1 WARN, cleanup verified (CRM left at its 202-row baseline).

| Clause | Result |
|---|---|
| **C0 — the link actually returned a userId** | ❌ **FAIL** — this is the one that matters |
| C1 — exactly one row created | ✅ PASS (the row exists *anyway* — that is the trap) |
| C2 — phone normalised, no `whatsapp:` prefix | ✅ PASS |
| C3 — `Conversation_ID__c` linked to the session | ✅ PASS |
| C4 — `Course__c` captured | ❌ FAIL — create-time only, see §4 |
| C5 — second thread does not duplicate | ✅ PASS |
| C6 — conversation updated on the hit path | ⚠️ WARN — R6, expected |
| C7 — course works when known at create | ❌ FAIL — same root cause as C0 |
| cleanup | ✅ PASS — 1/1 deleted |

**C1 passes while C0 fails.** The Salesforce record is created correctly — right phone,
right conversation — but the API cannot serialise its own response and returns a bare 500,
so no `userId` ever reaches us. A row count alone would have declared this a success.

### R18 — a record without an email is unreachable forever

`UserResponse` declares `email: str` as **required**, but Salesforce stores an empty string
as null, so `Email__c` reads back as `None`. The route's `try/except` cannot catch it —
response serialisation happens *after* the handler returns — so it surfaces as a plain
`500 Internal Server Error`. Reproduced directly:

| Call on a record created with `email: ""` | Result |
|---|---|
| `POST /users/lookup-or-create` (the create) | 500 — **and the row is created** |
| `POST /users/lookup-or-create` (subsequent) | 500 |
| `PATCH /users/{id}/status` | 500 |
| `DELETE /users/{id}` | 200 — no response model, so it works |

**This fires on the normal WhatsApp path**: the first linkable turn has a name and a phone
but no email yet, so we send `email: ""`. Consequences: `crm_user_id` is never persisted,
nothing caches, every turn re-attempts, and Phase 7's status pushes would have no user to
address.

**Decision D9 in the plan** lays out the four options. Recommendation: ask the API owner to
make `UserResponse.email` optional (one line, fixes R4 and R18 at the root), and in the
interim require an email before linking — which costs voice leads who never give one, but
is recoverable, unlike fabricating addresses into a CRM humans read.

---

## 2. Subtasks and what was actually done

### Phase 0 — Verify & stage the upstream API ✅

| # | Subtask | How it was done |
|---|---|---|
| 0.1 | Write the verification harness | `scripts/verify_salesforce_api.py` — read-only by default, `--write` for create/hit/status probes, `--json`, `--fail-on-defect` |
| 0.2 | Locate the API | Found running at **`127.0.0.1:8098`**, source at `D:\project\salesforce\salesforce-admission-api` @ `272434d` — the same commit the plan was written against. No contract drift across all 14 paths. |
| 0.3 | Run it | 18 probes, 0 FAIL, test records created and cleaned up |
| 0.4 | Fold findings back into the plan | 5 upstream behaviours reproduced; **R16 discovered** (below); D1/D2/D3 answered |

**The finding that mattered:** `Sentiment__c` is a **restricted picklist**. The obvious
field to send — `primary_emotion` — shares *zero* values with it and fails as a **500**,
which looks transient. The correct source is the categorizer output, upper-cased.

### Phase 1 — Conversation identity ✅

| # | Subtask | How it was done |
|---|---|---|
| 1.1 | Session registry | `app/crm/session.py` — mints a `conversation_id` at session start on all four channels |
| 1.2 | Voice lifecycle | Keyed by `stream_sid`, explicit start/end. `start()` is idempotent, so a redelivered Twilio `start` event can't fork one call into two conversations |
| 1.3 | WhatsApp lifecycle | No session object and no end event, so "one conversation" = messages within an idle window. Falls back to the most recent `conversations` row, so a restart mid-conversation doesn't split it |
| 1.4 | Migration | `app/crm/schema.py` — additive, idempotent. `conversation_id` and `crm_user_id` on `conversations`, `crm_user_id`/`crm_synced_at` on `leads`, plus `crm_sync_outbox` |
| 1.5 | Thread the id | `create_conversation` → `log_interaction` → `handle_post_interaction` → `_handle_disconnect` all take an optional `conversation_id` |
| 1.6 | Tests | 29 tests: stability, lifecycle, expiry, WhatsApp resume/degrade, SQL plumbing |

**Design note:** `conversations.conversation_id` is **separate** from the row primary key.
A voice call is one row so they coincide, but WhatsApp writes a row per message while a
session spans many — and must present *one* id to the CRM.

### Phase 2 — CRM client ✅

| # | Subtask | How it was done |
|---|---|---|
| 2.1 | HTTP client | `app/crm/client.py` — httpx, timeouts (2s connect / 5s read), bounded retry, jittered backoff, circuit breaker, single-flight |
| 2.2 | 500 classification | The core of the phase — see §3 |
| 2.3 | Identity + mappings | `app/crm/identity.py` — normalisation, SOQL sanitisation, picklist mappings |
| 2.4 | Outbox | `app/crm/outbox.py` — durable replay with 30s→2h backoff; drops permanent failures |
| 2.5 | Entry points | `app/crm/sync.py` — `lookup_or_create` and `push_status`, both never raise, both no-op when disabled |
| 2.6 | Tests | 54 tests against `httpx.MockTransport`, almost all about failure behaviour |

### Phase 3 — WhatsApp ✅ code / 🟡 gate

| # | Subtask | How it was done |
|---|---|---|
| 3.1 | Link entry point | `sync.link_conversation()` — the shared call every channel makes once per turn |
| 3.2 | Wire the webhook | `app/main.py` `/twilio/whatsapp` calls it after the identity state machine runs |
| 3.3 | Cache the link | `get_lead_crm_user_id` / `set_lead_crm_user_id`, so a chatty thread doesn't re-ask the API per message |
| 3.4 | Persist on the log row | `create_conversation`/`log_interaction` carry `crm_user_id` |
| 3.5 | Tests | 12 tests including a simulated multi-message thread |

---

## 3. The three upstream behaviours the code defends against

All reproduced live, all permanent — the contract is frozen, so these are handled
client-side and stay handled.

| ID | Behaviour | Defence |
|---|---|---|
| **R2** | `find_user` matches `Email__c = X OR Phone__c = Y LIMIT 1` with no ordering. An empty value becomes `Email__c = ''` and can match a stranger. | Never send a blank identifier. A phone we can't vouch for becomes `""`, which makes the identity unsendable rather than dangerous. |
| **R16** | `Sentiment__c` is a restricted picklist; a bad value arrives as **500**, indistinguishable by status code from a network blip. | `map_sentiment()` returns `None` for anything outside the vocabulary, and `push_status` refuses to send. Permanent errors are never queued for retry. |
| **R17** | `split_name("")` raises IndexError → 500. It runs *before* `create_user`, so nothing is created. | `is_sendable` requires a real name. A placeholder is not an option: the status PATCH writes three unrelated fields, so a fabricated name could never be corrected. |

Also handled: **R3** (SOQL metacharacters stripped), **R4** (a 500 on a POST may mean the
row exists — re-look-up rather than retry), **R5** (single-flight, since find-then-create
has no lock), **R6** (`Conversation_ID__c` is never updated on the hit path — accepted
limitation).

---

## 4. Known gaps and open decisions

| # | Item | Impact |
|---|---|---|
| **D4** | WhatsApp idle window — sits at a 6-hour default in `CRM_IDLE_WINDOW_HOURS` | Decides what "one conversation" means. Config-only change. |
| **D8** | The destructive R2/R4 probe was never run | R2 and R4 are source-read, not reproduced. Needs a sandbox org — if R4 fires, the orphan can't be deleted (no `GET /users/{id}`). |
| — | Migration never run against a live Postgres | Docker daemon was down. All 9 statements validated against the real Postgres grammar via `pglast`, but that's syntax, not semantics. |
| — | Gate 3 not verified live | See §5. |
| — | **Gate 5 is mis-worded** | Voice callers give their name only during the call, so linking happens at disconnect, not "before the first AI turn completes". The conversation id is still minted at start, so the linkage is fine. Revise the gate before Phase 5. |

---

## 5. Manual validation

### 5.0 Prerequisites

Three things must be running. Check what's already up:

```bash
curl -s --max-time 4 http://127.0.0.1:8098/health   # CRM API
curl -s --max-time 4 http://127.0.0.1:8000/health   # the app
```

**1. Postgres** (required — without it leads aren't stored, and the link can't be cached
between messages):

```bash
docker start elearning-postgres
# or, first time:
docker compose up -d postgres
docker exec elearning-postgres pg_isready -U elearning -d admissions
```

**2. The Salesforce API** — already up on `:8098`. If not:

```bash
cd /d/project/salesforce/salesforce-admission-api
venv/Scripts/python.exe -m uvicorn main:app --host 127.0.0.1 --port 8098
```

**3. The app:**

```bash
cd /d/project/universityDemo
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**4. Turn the CRM on** — it is **off by default**, so nothing below happens until you do.
Add to `.env` and restart the app:

```
CRM_ENABLED=true
CRM_BASE_URL=http://127.0.0.1:8098
```

> The first app start after this runs the migration. Watch the log for
> `Database initialized: all tables ready`.

---

### 5.1 Validate the CRM API itself (no app needed, writes nothing)

```bash
python scripts/verify_salesforce_api.py --base-url http://127.0.0.1:8098
```

**Expect:** `health`, `openapi/contract` and `status/404` PASS; warnings for `R1`, `R13`
and the R16 vocabulary mismatch. Exit code 0.

Add `--write` to exercise create/hit/status — **this creates test records**, marked
`ZZ-VERIFY-<runid>` and deleted again at the end:

```bash
python scripts/verify_salesforce_api.py --base-url http://127.0.0.1:8098 --write
```

**Expect:** `lookup/create`, `lookup/create/course`, `lookup/hit/dedupe`, `status/patch`
and `status/persisted` all PASS, `lookup/hit/conversation` WARN (**R6** — the new
conversation isn't linked), `cleanup` PASS. **18 probes, 0 FAIL.**

---

### 5.2 Validate the WhatsApp intent end-to-end

This is the one that proves the product intent. Substitute your own test number.

**Step 1 — a student's first message (their name).**

```bash
curl -s -X POST http://127.0.0.1:8000/twilio/whatsapp \
  --data-urlencode "From=whatsapp:+14155550199" \
  --data-urlencode "Body=Ana" \
  --data-urlencode "WaId=14155550199" \
  --data-urlencode "NumMedia=0"
```

**Expect in the app log:**

```
crm.session: started whatsapp conversation <uuid> (key=whatsapp:+14155550199, ...)
crm.sync: whatsapp+phone (conv=<uuid>) -> userId 0o6T...
```

That second line is the whole feature: the student now exists in the CRM.

**Step 2 — confirm the record in Salesforce.**

```bash
curl -s http://127.0.0.1:8098/admissions | python -c "
import json,sys
rows = json.load(sys.stdin)
mine = [r for r in rows if r.get('Phone__c') == '+14155550199']
for r in mine:
    print('userId        ', r['Id'])
    print('Phone__c      ', r.get('Phone__c'))
    print('Course__c     ', r.get('Course__c'))
    print('Conversation  ', r.get('Conversation_ID__c'))
print('rows for this number:', len(mine))
"
```

**Expect:** exactly **one** row. `Phone__c` is `+14155550199` — **no `whatsapp:` prefix**
(that's the R2/R3 normalisation working). `Conversation_ID__c` equals the uuid from the
log.

**Step 3 — send more messages; it must NOT duplicate.**

```bash
for m in "I want to take admission" "MBA" "Hi"; do
  curl -s -o /dev/null -X POST http://127.0.0.1:8000/twilio/whatsapp \
    --data-urlencode "From=whatsapp:+14155550199" \
    --data-urlencode "Body=$m" --data-urlencode "WaId=14155550199" --data-urlencode "NumMedia=0"
  sleep 2
done
```

Re-run the Step 2 check. **Expect:** still **exactly one** row. The log should show only
the first turn producing a `-> userId` line.

> ⚠️ **Known limitation to expect here:** `Course__c` will be **empty** unless the program
> was known on the linking turn. The API only accepts `course` at *create* time, and the
> WhatsApp state machine asks for name → email → program, so the link happens before the
> program is known. There is no endpoint that can add it later. **If the course must be on
> the record, the create has to be deferred until the program is captured** — say the word
> and I'll make that change; it costs CRM timeliness for correctness.

**Step 4 — the student is now visible to a counsellor.** Open Salesforce and find the
`Customer` record for `+14155550199`. That is the point of the whole exercise.

---

### 5.3 Validate the safety promises

**A — Nothing happens when the CRM is off.**

Set `CRM_ENABLED=false` in `.env`, restart the app, repeat Step 1 with a *fresh* number.
**Expect:** the student still gets a normal WhatsApp reply, and **no** `crm.sync` lines in
the log and no new Salesforce row. This is the rollback switch.

**B — The student never notices a CRM outage.**

Keep `CRM_ENABLED=true`, send a message to confirm linking works, then **stop the
Salesforce API** (Ctrl-C). Send another message from the same number.

**Expect:** the WhatsApp reply still arrives normally. The log shows
`crm.sync: status update failed ... — queueing` or a circuit-breaker warning, **not** an
error to the student. Restart the API and the queued writes replay.

**C — Undefined behaviour is refused, not sent.**

```bash
python - <<'PY'
from app.crm import identity
print("At-Risk ->", identity.map_sentiment("At-Risk"))   # AT-RISK
print("excited ->", identity.map_sentiment("excited"))   # None  <- refused
print("rejected ->", identity.map_offer_accepted("rejected"))  # NOT_ACCEPTED
print("phone   ->", identity.normalize_phone("whatsapp:+1 415 555 0199"))
PY
```

**Expect:** `AT-RISK`, `None`, `NOT_ACCEPTED`, `+14155550199`. The `None` is the R16
defence — that value would 500 if sent, and would then be retried forever by a naive
outbox.

---

### 5.4 Validate the automated suite

```bash
python -m pytest tests/test_crm_session.py tests/test_crm_client.py \
                 tests/test_crm_whatsapp.py tests/test_call_channel.py -q
```

**Expect:** `101 passed`.

```bash
python -m pytest tests/ -q \
  --ignore=tests/test_phase2_audio.py --ignore=tests/test_phase3_rag_llm.py \
  --ignore=tests/test_offer_readiness.py --ignore=tests/test_task9_requirements.py
```

**Expect:** `310 passed, 2 skipped`.

> The two ignored files fail for reasons predating this work and unrelated to it —
> `test_offer_readiness` needs `pytest-asyncio` (not installed), and
> `test_task9_requirements` asserts no spaces in a requirements line, tripping on a valid
> PEP 508 marker in `onnxruntime-gpu`.

---

### 5.5 What a clean run proves

| Promise | Verified by |
|---|---|
| A student who messages appears in Salesforce | §5.2 step 2 |
| Messaging repeatedly does not create duplicates | §5.2 step 3 |
| The phone is stored cleanly, without the `whatsapp:` prefix | §5.2 step 2 |
| The conversation is linked to the record | §5.2 step 2 (`Conversation_ID__c`) |
| Nothing happens while the switch is off | §5.3 A |
| A CRM outage does not affect the student | §5.3 B |
| Values the CRM can't accept are refused, not sent | §5.3 C |
| The whole thing is revertible instantly | `CRM_ENABLED=false` |

**Not yet proven:** that `Course__c` carries the program (§5.2, known limitation), and
Gate 3 as literally worded — it requires the course on the record, which the
first-contact link can't deliver.
