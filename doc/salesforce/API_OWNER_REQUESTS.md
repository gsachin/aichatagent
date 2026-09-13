# Requests for the admission API — from the universityDemo integration

For the owner of [`salesforce-admission-api`](https://github.com/LeahXing/salesforce-admission-api)
(verified against commit `272434d` and the running dev instance on 2026-09-12).

## How to read this

These are **not blockers**. The integration works today against the API as it is — status sync,
offer documents and channel linking are all live and gate-verified. Everything below is either a
defect that costs us a workaround, or a gap in the documentation that cost us a day of reverse
engineering. They are ordered by how much they would improve things.

Each item is written as a user story with the evidence I have, so they can be triaged directly.

---

## 1. A missing record answers 500, not 404

**As an integrator, I need a missing record to be distinguishable from a Salesforce outage, so that
I can stop retrying a write that can never succeed.**

*Evidence.* `PATCH /admissions/ZZ-NOT-A-RECORD` with a valid body returns:

```
500 {"detail": "Resource Customer Not Found. Response content:
              [{'errorCode': 'NOT_FOUND', 'message': 'Provided external ID field does not exist...'}]"}
```

Every admission route maps `except Exception → 500` (`app/routes/admission_routes.py:98-102` and the
identical blocks at 36-40, 52-56, 72-76, 118-122), so *not found*, *picklist violation*, and
*Salesforce is down* are indistinguishable by status code. Clients are forced to parse
`errorCode` out of a `detail` string, which will break the moment the message is reworded.

*Cost today.* We added `NOT_FOUND` to our permanent-error markers and match on the string. Before
that, a write to a deleted record retried four times, queued, and would have failed on every
replay forever.

*Ask.* Map `SalesforceResourceNotFound` → 404 on the admission routes (the user routes already do
this for `ValueError` at `user_routes.py:61`).

---

## 2. A record created without an email can never be read back (R18)

**As an integrator, I need to read back a record whose email is empty, so that a student who gives
only a phone number is not lost.**

*Evidence.* `UserResponse` declares `email: str` as **required** while `course`, `sentiment` and
`offerLetterAccepted` are explicitly nullable (read from the running service's `/openapi.json`).
Salesforce stores an empty string as null, so `format_user_response` passes `None` into a
non-optional field and response serialisation fails **after** the handler has returned — the row is
created, but every subsequent read of it answers 500. Reproduced on 2026-09-11 and re-confirmed
against the live schema.

*Cost today.* The mirror-image workaround: when a lookup fails for a phone-only person, we search
the **admissions** listing instead (its model has `Email__c: Optional[str] = None`, so the same row
reads fine there). It works — verified live — but it is an O(all rows) scan per phone-only link.

*Ask.* `email: Optional[str] = None` on `UserResponse` (and `phoneNumber`/`conversationId` for the
same reason). One line, and it removes a whole class of unreachable records.

---

## 3. `PATCH /admissions/{id}` is undocumented, and its null semantics are the opposite of the users route

**As an integrator, I need the profile-update route documented, including what an explicit `null`
does, so that I do not erase data by accident.**

*Evidence.* This is the **only** route that can update a person's `Course__c` or any offer field —
`PATCH /users/{id}/status` writes exactly three fields (`salesforce_user_repository.py:95-122`).
It is absent from the handbook, and its behaviour is subtle in two ways that a client must know:

| Request | Effect |
|---|---|
| `{"Course__c": "MBA"}` | changes `Course__c`, nothing else (`model_dump(exclude_unset=True)`) |
| `{"Course__c": null}` | **clears** `Course__c` — `exclude_unset` is not `exclude_none` |

The users route has the **opposite** convention: `null` there means "leave alone"
(`if sentiment is not None`). Two PATCH endpoints, opposite null semantics, neither documented.

We verified both live (`scripts/verify_gate7_status.py`) and now send only the keys we intend to
change, never nulls — but a new integrator round-tripping a record they just read would silently
wipe every empty field they sent back.

*Ask.* Document the route, state the null behaviour explicitly, and consider
`model_dump(exclude_unset=True, exclude_none=True)` so the two routes agree.

---

## 4. An uploaded document cannot be deleted

**As an integrator, I need to remove a document I uploaded, so that my test runs and my mistakes
are not permanent.**

*Evidence.* The routes are list / get / content / initialize / chunks / complete / verify — there is
no delete (`app/routes/document_routes.py`). Uploaded documents are `ContentVersion` records; the
only route that touches them creates them.

*Cost today.* Every gate run leaves a ContentVersion behind, and the harness has to print the
Salesforce REST call an operator must run by hand to clean up. Test data accumulates in the CRM.

*Ask.* `DELETE /admissions/documents/{document_id}`, or a documented test-data purge.

---

## 5. `/complete` is not idempotent, and its 500 is ambiguous

**As an integrator, I need a retry of `/complete` to be safe, so that an ambiguous failure does not
create a second document.**

*Evidence.* `complete_document_upload` creates the `ContentVersion`, reads it back, then removes
the session directory (`app/services/document_service.py:493-531`). A failure in the read-back — for
instance the response model rejecting a null `Document_Type__c` — leaves the document created and
the response a 500. A client that retries then creates a second copy; one that does not is left
unable to tell whether the document exists.

*Cost today.* We send `/complete` with `is_create=True` so the client refuses to retry it, and we
record an `unknown` outcome that a human has to check. That is the best a client can do.

*Ask.* Make `/complete` idempotent per `upload_id` (return the existing document), or return 409 on
a second call.

---

## 6. Abandoned upload sessions are never cleaned up

**As an operator, I need abandoned upload sessions to expire, so that the API host's disk does not
fill with partial uploads.**

*Evidence.* `initialize_document_upload` creates `temp_uploads/<upload_id>/`; only a successful
`complete` removes it. There is no TTL, no sweeper and no cleanup on abandonment
(`app/services/document_service.py:216-224`, `:522-524`).

*Cost today.* Every upload that fails at the chunk or complete stage — which for us means every
CRM outage mid-upload — leaks a directory. We log the `upload_id` when we abandon one so an operator
can sweep it, but nothing does so automatically.

*Ask.* A startup sweep (or a scheduled one) deleting sessions older than, say, 24 hours.

---

## 7. `GET /admissions` returns every row, unfiltered

**As an integrator, I need to look up a Customer by phone or email, so that I do not fetch the
whole table to find one person.**

*Evidence.* No query parameters, no `WHERE`, no `LIMIT`, no pagination — `query_all` over the whole
`Customer` object, ordered by `CreatedDate DESC` (`app/repositories/salesforce_admission_repository.py:8-67`).
The response is unbounded in both rows and memory.

*Cost today.* Our phone-only recovery (item 2) fetches the entire table and filters client-side.
Fine at 204 rows; not fine at 200,000.

*Ask.* `?phone=`, `?email=` or `?application_no=` filters — the SOQL already exists in
`find_user`.

---

## What we are *not* asking for

We are not asking for changes to `POST /users/lookup-or-create`'s find-or-create semantics. It is a
genuine constraint that a hit writes nothing back — no conversation-id update, no course update —
and we have worked around it by treating the admissions PATCH as the profile-update route. Changing
lookup-or-create would be a bigger design change than the problem justifies. Flagging it here only
so the behaviour is a decision rather than an oversight.

Two smaller notes, offered as observations rather than requests:

* **A fresh OAuth token is fetched per repository call**, and `get_user_by_id` swallows every
  exception — so a transient auth or network failure on the pre-check of
  `PATCH /users/{id}/status` is reported to the caller as **404 "User not found."**, which reads as
  permanent. We do not use that route for anything critical as a result.
* **No SOQL or DML call sets a timeout** — a hung Salesforce request hangs the worker thread
  indefinitely. Our client has its own timeouts, which is the only reason an outage does not take
  our side down with it.
