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

## 1b. Gate 4 — offer-letter document upload, run live 2026-09-12

`scripts/verify_offer_document_upload.py` drives the real chunked-upload chain through
`app.crm.documents` against `:8098`. Last run: **13 PASS, 0 FAIL** (with `--include-schedule`),
2 test users deleted. It is deliberately not part of `verify_salesforce_api.py`, whose contract
is "read-only by default, deletes everything it creates" — this one must write, and what it
leaves behind **cannot be deleted through this API**.

`generate_and_send_offer()` now schedules the upload after the offer is sent, off the request
path (the WhatsApp webhook awaits offer generation inline, so three HTTP calls there would sit
in Twilio's window). It is best-effort: the offer is delivered whether or not the CRM takes the
document.

### The path segment is not the userId

The upload routes resolve `{application_no}` with
`SELECT Id, Application_No__c FROM Customer WHERE Application_No__c = X LIMIT 1` — they are
**not** record-Id keyed, and the value we store is the record Id (`format_user_response` emits
`userId: user["Id"]`). Reproduced on every gate run:

| Call | Result |
|---|---|
| `GET /admissions/{userId}` | 200 — returns the record, including `Application_No__c` |
| `GET /admissions/{userId}/documents` | **404** `Application <id> was not found` |
| `GET /admissions/{application_no}/documents` | 200 |

`lookup-or-create` mints the application number separately (`APP-<10 hex>`) and **never returns
it**, so it is resolved once via the Id-keyed `GET /admissions/{userId}` and cached on the lead
(`leads.crm_application_no`, cleared whenever the CRM link changes). Live data: 202 rows, 200
carrying `TESTAPP2027xxx` and 2 carrying `APP-*`; none carries the record Id.

### R19 — `Document_Type__c` is a restricted picklist with no offer-letter value

Read from Salesforce field metadata (`--describe-only`), not inferred: `Document_Type__c` and
`Source__c` are **both restricted picklists**. Their vocabulary is
`{Academic Transcript, Resume, Financial Document, ID Document, Admission Call Recording,
Chatbot Conversation Record, Test Score, Recommendation Letter, Other, 10th Marksheet,
12th Marksheet, Diploma Marksheet, Bachelor's Last-Semester Result, IELTS Score,
Diploma Last-Semester Result}` — there is **no offer-letter value**, and `Source__c` allows only
`Internal Upload`, which is what we send.

An out-of-vocabulary value fails as a 500 (`INVALID_OR_NULL_FOR_RESTRICTED_PICKLIST`), i.e. an
R16-class permanent failure that no retry can fix. `CRM_OFFER_DOCUMENT_TYPE` therefore ships as
`Other` — the only honest fit today — and should be changed to `Offer Letter` once an admin adds
it to the picklist (the org already has dedicated values for system artifacts, e.g.
`Admission Call Recording`, so this fits the existing pattern).

**Consequence for the duplicate check:** because the value we must use is a catch-all, matching
existing documents on `document_type` would suppress the upload for any student who happens to
have an unrelated `Other` document. The rule keys on our own file-name prefix
(`Offer_Letter_<offer-id>.pdf`) instead — precise, and independent of a picklist we do not own.

### Other live findings

* **`/complete` is not idempotent.** It creates the `ContentVersion`, reads it back, then
  deletes the session directory; a 500 after the create would duplicate the document on retry.
  It is the one call sent with `is_create=True`, so the client refuses to retry it, and an
  ambiguous outcome is recorded as `crm_upload_status='unknown'` — which tells an operator to go
  and look, where `'failed'` would have told them not to bother.
* **A fields-only init encodes as `application/x-www-form-urlencoded`,** not multipart — httpx
  treats an empty `files` as absent (`httpx/_content.py:211` at the pinned 0.28.1). Verified
  against the route, which parses both and answers 400 "Application ... was not found" for
  either, i.e. all five `Form(...)` fields arrive. The chunk call is genuinely multipart.
* **`GET /admissions/{id}` answers a bad id with 500, not 404,** so the resolver runs with
  `retries=0`: at the default three retries one stale id would record four failures against a
  breaker whose threshold is five, pausing every other CRM call in the app.
* **Residue, stated rather than hidden:** each successful upload leaves one `ContentVersion`.
  There is no delete route, and deleting the Customer may orphan it. The gate prints every id it
  created and the exact REST call to remove it by hand.

### Not yet done

* `push_status` remains unwired — neither `offerLetterReleased` nor `offerLetterAccepted` is
  pushed when an offer is sent or accepted.
* The offer idempotency guard is still a no-op: `service.py:150` passes
  `within_hours=minutes / 60.0` and `offers/models.py:599` interpolates `int(within_hours)`, so
  `OFFER_GUARD_MINUTES=1` becomes `INTERVAL '0 hours'` and the guard never fires. A student who
  types "done" twice gets two offers. Flagged, deliberately not changed (it alters sending
  behaviour); the prefix rule above stops the *second* offer becoming a second CRM document.
* Gate 4 proves the module and the real API; a full end-to-end run through the running app
  (Postgres + `CRM_ENABLED=true` + restart) is still a manual step.

---

## 1c. Gates 4, 5 & 6 — every conversation channel, run live 2026-09-12

Only WhatsApp (Phase 3) had ever been wired, so voice and web chat left no trace in Salesforce.
That was observed, not theorised: a web-chat conversation (Pradeep, `pradeepdubey@test.com`, MBA)
produced and delivered an offer letter while `leads.crm_user_id` stayed `NULL` and the CRM held no
row for `+917757057985`.

All three remaining channels are now wired through the same `link_conversation` entry point.

| Gate | Harness | Result |
|---|---|---|
| 6 — web chat | `scripts/verify_gate6_webchat.py` | **6 PASS, 0 FAIL** |
| 4 & 5 — voice | `scripts/verify_gate45_voice.py` | **5 PASS, 0 FAIL**, 2 clauses explicitly deferred |

### The web chat now reaches the CRM

The Streamlit chat mints a conversation id in the browser tab and never sent it anywhere; the lead
endpoints were CRM-blind. The id now travels with the lead write, and the FastAPI handler links the
person in the background — backgrounded because Streamlit's HTTP timeout is shorter than a cold CRM
call with retries, and all the chat needs back is the lead id.

Proof, per run: the person appears in Salesforce with `Conversation_ID__c` equal to the chat's id;
a second write does not duplicate them; a lead with **no** conversation id creates nothing (the
dashboard's add-lead form posts to the same endpoint, and inventing an id would be worse than
declining); and — the seam that matters — `leads.crm_user_id` is populated, which is what makes
`app/crm/documents.py` stop skipping the offer PDF.

### Voice: the caller's number now crosses the boundary

Twilio Media Streams does not put the caller's number in the `start` event, the webhooks discarded
`From`, and the post-call extractor never even asks for a phone (`app/database.py:175-180`) — so
`resolved_phone` was always `""` and every inbound call created a lead keyed on the empty string.
That also meant inbound sentiment could not be keyed to a lead (blocker B6).

The chain is now: webhook `?From=` → `<Parameter name="phone">` on `<Stream>` → the `start` event →
the session registry → the CRM. Both directions, verified clause by clause. Outbound additionally
carries the lead id it dialled, so it links at call start with a full identity from the lead row.

**Not proven by the gate, and stated as such in its output:** the mid-call CRM link itself, which
needs real audio through STT. It is covered by `tests/test_crm_voice.py` (15 tests) and needs one
live call to confirm for real.

### The R18 workaround: phone-only people can now be linked

The API cannot return a record whose email is empty (`UserResponse.email` is required; the
admissions model has it optional). A phone-only person therefore answered 500 on every read — the
row is created, but unreachable.

`sync.lookup_or_create` now falls back, **only after the normal lookup has failed and only when
there is a phone**, to finding the person in `GET /admissions` by normalised number. Verified live:
it returned `0o6T100000000UfIAI` for a phone-only identity, whose row showed `Email__c: None`,
`Phone__c: +14155550188`, `Course__c: MBA`. No fabricated data; the real fix is still one line
upstream (make `UserResponse.email` optional), after which this can be deleted.

Cost: `GET /admissions` has no server-side filter, so a phone-only link is an O(all rows) fetch —
204 today. Worth watching as the org grows.

### Also fixed on the way

* `_handle_disconnect` now prefers the carrier number over the extraction, so calls stop creating
  leads with an empty phone, and the CRM id reaches the conversation row.
* The offer path recorded every conversation as `channel="whatsapp"` — including offers generated
  from the web chat. It now records the channel the request came from.
* The web chat's interaction log dropped its `conversation_id`, so every exchange became its own
  conversation row with a fresh uuid. It is carried through now.
* Background CRM work goes through one helper (`app/crm/tasks.py::spawn`) instead of three copies of
  the same "keep a strong reference so the task is not garbage-collected" idiom.

---

## 1d. Gate 7 — status sync, run live 2026-09-13

The CRM used to contradict itself: it held a released offer letter while reporting
`Offer_Letter_Released__c = False`, and a student who switched to MBA stayed on "Computer Science"
— because program interest only ever reached Salesforce inside `lookup-or-create`, at create time
and never again. Nothing pushed sentiment, and `flush_outbox` had a docstring claiming a scheduler
called it when no scheduler had ever been written.

`scripts/verify_gate7_status.py` drives `app/crm/status.py` against the live API and **re-reads the
record** for every clause. Last run: **15 PASS, 0 FAIL**.

| Clause | Result |
|---|---|
| C1 a changed program reaches `Course__c` | ✅ |
| C2 an offer release moves `Offer_Status__c` / `Offer_Sent_At__c` / `Offer_Letter_Released__c` / `Admission_Status__c` together | ✅ |
| C3 accepting → `ACCEPTED` / `Accepted` / `Approved` | ✅ |
| C4 declining → `NOT_ACCEPTED` / `Declined` / `Rejected` | ✅ |
| C5 a lapsed offer → `Expired` | ✅ |
| C6 sentiment lands on both category fields, each with its own spelling | ✅ `AT-RISK` and `AT RISK` |
| C7 **a CRM outage loses nothing** — queued, then replayed | ✅ 3 queued, 3 succeeded |
| C8 a deleted record is dropped, not retried forever | ✅ |
| intact — identity fields after seven status writes | ✅ untouched |

**One write route, and one trap.** `PATCH /admissions/{id}` is the only route that can carry
`Course__c` and the offer fields (`PATCH /users/{id}/status` writes three fields and no more). It
is a true partial update — but `exclude_unset` is not `exclude_none`, so **an explicit `null`
clears the field**, while on the *users* route a null means "leave alone". Two PATCH endpoints with
opposite null semantics; the dry run reproduced both. The client therefore sends only the keys it
means to change, enforced by an allowlist, and never a null.

**End to end, through the running app** (the exact requests the web chat makes): a first save with
"Computer Science" → `Course__c='Computer Science'`; the student switching to MBA →
**`Course__c='MBA'`**. The offer chain is confirmed by the `:8098` watcher rather than by our own
logs: `Offer_Letter_Released__c: False -> True` with the PDF attached.

**Two bugs the live runs caught that unit tests did not:**

1. `link_conversation` returns early for an already-linked lead — and that early return is the only
   path a *returning* student takes, so a course sync placed after the lookup would never run for
   the one case it exists to fix. Fixed, with a regression test.
2. The field diff in the dry run initially flagged Salesforce's own audit fields
   (`LastModifiedDate`, `SystemModstamp`, `LastViewedDate`) as collateral damage. They are not: only
   the business fields moved.

**The first thing the new worker ever did in production** was deliver a write that had been sitting
in the queue — a `Course__c` update for a real record, queued by a code path that had given up on
it, replayed on startup: `1 sent, 0 retrying, 0 dropped`.

**Out of scope, unchanged:** the offer idempotency guard no-op, and any change to the API repo —
the seven requests for its owner are in `doc/salesforce/API_OWNER_REQUESTS.md`.

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
