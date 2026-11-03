# Plan — Integrate the updated Salesforce admission API with the universityDemo offer pipeline

**Date:** 2026-11-04
**Branch:** `salesforce-offer-update` (universityDemo)
**Status:** Draft for review — no code changed yet
**Scope:** Replace the live `salesforce-admission-api` at `:8098` with the updated
`salesforce-admission-api-main_updated_offer_letter` build **and change the offer rule from
"any document uploaded → offer" to "document validated → marks meet the program requirement →
offer"**. The validation/eligibility result is the gate; the offer pipeline is the consumer.

**Requirement (confirmed 2026-11-04):** when a student uploads a document, the system validates it
and extracts the marks; the offer letter is generated only when those marks satisfy the program's
requirement. Today the offer is generated as soon as *any* document row exists
(`UD/app/offers/service.py:93-95`), with no marks check.

**At a glance**

| | Today | Target |
|---|---|---|
| Offer trigger | Any row in `lead_documents` | The program's gate document is validated and its marks meet the requirement |
| Marks check | None | API extracts the mark; UD compares it with `minimum_required_*` |
| Decision | Implicit (“document exists”) | Explicit `issue` / `block_not_eligible` / `block_wrong_document` / `block_review` / `block_missing` / `block_unknown` |
| Student sees | An offer | Either an offer with “Basis of Offer”, or the mark-vs-requirement explanation |
| Salesforce | Offer fields pushed | `Course_Eligibility__c` + offer fields, consistent with the decision |
| Validation service | Not used | `POST /documents/upload-and-verify` + `requirement_status` |

**Source folders**

| Alias | Folder |
|---|---|
| `UD` | `D:\project\universityDemo` |
| `OLD` | `D:\project\salesforce\salesforce-admission-api` (live, branch `user-lookup-endpoint-v1`) |
| `NEW` | `D:\project\salesforce\salesforce-admission-api-main_updated_offer_letter\salesforce-admission-api-main` (the update) |

**Evidence basis.** Every claim below was read from the three trees on disk. The `NEW` extractors
and validator were also **run over all 19 supplied sample PDFs** in an isolated temporary venv
(§3.12), and the live org's fields/picklists were read-only described (§6). **Salesforce writes
were not executed.** Tesseract 5.4 was installed during the verification, so scanned-document OCR
was exercised. The `Salesforce Credential .docx` was deliberately **not** read; the screenshot shows
`SALESFORCE_DOMAIN` / `SALESFORCE_CLIENT_ID` / `SALESFORCE_CLIENT_SECRET` values that must be
copied into `NEW/.env` and never committed or pasted into a document.

---

## 0. Executive summary

1. **`NEW` is not "`OLD` plus validation".** It is a fork of `origin/main` (which is 4 commits
   ahead of, and does not contain, the local `fixed issue` commit `f327a95`) plus a large
   document-validation feature. Deploying it as-is **regresses** the user-API fixes that the live
   integration depends on: optional email on `UserResponse` (R18 phone-only recovery), email-first
   lookup, `format_soql` binding (SOQL injection), and precise not-found handling.
2. **The new build deletes the document upload contract the offer pipeline uses.**
   `POST /admissions/{application_no}/documents/uploads`, `/chunks`, and `/complete` are gone
   (`NEW/app/routes/document_routes.py`). Both the offer PDF and every student document are
   uploaded through those three endpoints (`UD/app/crm/documents.py:335-450`). **Offer uploads and
   student-document uploads stop working on cutover.**
3. **The replacement endpoint cannot carry an offer letter.** `upload-and-verify` accepts only
   PDFs, only the eight canonical document types, runs OCR/validation, and writes eligibility
   history to Salesforce (`NEW/app/core/admission_document_config.py:10-13`,
   `NEW/app/services/document_service.py:859-950`). An offer letter is not evidence and has no
   canonical type, so it must not go through that route.
4. **The new validation code has real prerequisites and real defects:** missing `PyMuPDF` /
   `OpenCV` / `pytesseract` / `Pillow` / `PyJWT` dependencies; `Program__c` does not exist in the
   org; `Verification_Status__c` is a restricted picklist with **no `Pending`**; all relevant
   picklists are restricted; the OCR binary was missing and was installed during verification (one
   supplied sample is scanned); and two code paths can never succeed (`evaluate-and-save`). The
   org **does** already have
   `Document_Validation_Results__c`, `Course_Eligibility__c` and `Failed_Reason__c`.
5. **The confirmed business rule changes the pipeline.** Today `evaluate_offer_readiness` only
   checks that *at least one document row exists* (`UD/app/offers/service.py:93-95`) — the marks
   are never read. The target is: upload → API extracts and validates the marks → the program's
   academic requirement is compared → **offer only when the requirement is satisfied**; below
   threshold, review-required, missing, or wrong-type documents produce a student-facing
   explanation instead of an offer. §3 defines the exact decision matrix.
6. **Recommended path: stage, don't swap.** Bring `NEW` up on `:8099` with the local user-API fixes
   merged and a documented non-validating document endpoint, prove every call the app makes
   against it, run the new marks decision in **shadow mode** (computed and logged, not enforced),
   then enforce it behind `OFFER_ELIGIBILITY_MODE`. Cut `:8098` over only after the existing gates
   pass against the shadow.

---

## 1. What exists today

### 1.1 System map

```
WhatsApp / Web chat / Voice
        │
        ▼
universityDemo (FastAPI :8000)
  ├─ app/crm/sync.py        identity → POST /users/lookup-or-create
  ├─ app/crm/status.py      profile  → PATCH /admissions/{userId}
  ├─ app/offers/service.py  PDF      → WhatsApp + email
  └─ app/crm/documents.py   offer PDF + student docs
        │                     chunked upload protocol
        ▼
salesforce-admission-api (:8098)  ← separate repo, own venv, own .env
        │
        ▼
Salesforce org (Customer / ContentVersion / ContentDocument)
```

`start_services.ps1:46-52` pins `$CrmApiRoot = D:\project\salesforce\salesforce-admission-api`
and `$CrmApiPort = 8098`; Step 5b (`:643-711`) starts it from its own working directory because
`NEW/app/core/config.py:4` calls `load_dotenv()` with no path.

### 1.2 The API contract used today

| Call | Where in UD | Used for |
|---|---|---|
| `POST /users/lookup-or-create` | `app/crm/sync.py:101-186` | Link a conversation to a CRM person; the **only** route that returns a `userId` |
| `PATCH /users/{id}/status` | `app/crm/sync.py:465-497` | Legacy status fields (`sentiment`, offer flags) |
| `PATCH /admissions/{id}` | `app/crm/status.py:86-138` | The profile writer: `Course__c`, `Conversation_ID__c`, `Sentiment__c`, `Lead_Category__c`, offer lifecycle, admission status |
| `GET /admissions/{id}` | `app/crm/documents.py:131-175` | Resolve `Application_No__c` from the stored `userId` |
| `GET /admissions/{app}/documents` | `app/crm/documents.py:180-255` | Duplicate defence before uploading |
| `POST /admissions/{app}/documents/uploads` | `app/crm/documents.py:369-382` | Begin upload session |
| `POST .../uploads/{upload_id}/chunks` | `app/crm/documents.py:399-414` | Send bytes |
| `POST .../uploads/{upload_id}/complete` | `app/crm/documents.py:419-436` | Create the `ContentVersion` |
| `GET /admissions/{app}/documents`, `GET /documents/{id}/content` | not used by UD today | — |

The integration is gated by `CRM_ENABLED` (`UD/app/crm/sync.py:50-52`) and the two upload switches
(`UD/app/crm/documents.py:76-89`). This machine's `.env` has `CRM_ENABLED=true`,
`CRM_OFFER_UPLOAD_ENABLED=true`, `CRM_DOCUMENT_UPLOAD_ENABLED=true`,
`CRM_BASE_URL=http://127.0.0.1:8098` (`UD/.env:443-461`).

### 1.3 The existing offer-generation pipeline (as built)

1. **Student supplies prerequisites.** Name, email, phone, program and at least one document are
   required — `evaluate_offer_readiness` (`UD/app/offers/service.py:66-102`); one document of any
   kind is enough (`:93-95`). **The document's contents and marks are never inspected.** This is
   the rule the update changes.
2. **Document arrives.**
   - WhatsApp media → saved under `data/documents/<lead_id>/`, row in `lead_documents`, CRM upload
     scheduled (`UD/app/main.py:2660-2704`).
   - Web upload → same, and **the offer is auto-triggered as soon as readiness is true**
     (`UD/app/main.py:5110-5163`).
   - WhatsApp offers are only triggered when the student types "done" (`UD/app/main.py:2706-2711`).
3. **Offer generated.** `generate_and_send_offer` (`UD/app/offers/service.py:108-346`):
   idempotency guard → course match → `offer_letters` row → PDF via fpdf2
   (`UD/app/offers/pdf.py:45-207`) → WhatsApp media + email → conversation log.
4. **Offer filed in Salesforce.** `schedule_offer_upload` (`UD/app/crm/documents.py:718-741`) runs
   `upload_offer_document` in the background, which resolves the application number, checks for an
   existing `Offer_Letter_*` file, and performs init → chunks → complete
   (`UD/app/crm/documents.py:260-315`, `:335-450`). Outcome stored on
   `offer_letters.crm_upload_status` (`uploaded` / `failed` / `unknown`).
5. **Offer released published.** `push_offer_released` writes `Offer_Status__c=Offered`,
   `Offer_Sent_At__c`, `Offer_Letter_Released__c=True`, `Admission_Status__c` through
   `PATCH /admissions/{id}` (`UD/app/crm/status.py:173-194`, called at
   `UD/app/offers/service.py:334-344`).
6. **Student answers.** WhatsApp reply or dashboard action updates the offer row and calls
   `push_offer_response` (`UD/app/main.py:3152-3183`, `:5267-5273`) → `Offer_Letter_Accepted__c`,
   `Offer_Status__c`, `Admission_Status__c` (`UD/app/crm/status.py:197-222`).
7. **Expiry sweep** pushes `Offer_Status__c=Expired` (`UD/app/offers/models.py:670-728`).

Current baseline: **73 CRM/offer tests pass** (`test_crm_documents`, `test_crm_student_documents`,
`test_offer_crm_hook`, `test_offer_readiness`, `test_crm_recovery`) on this machine.

---

## 2. What is in the new build

### 2.1 Provenance — this matters more than any individual diff

| Ref | Commit | What it is |
|---|---|---|
| `OLD` HEAD | `2f452dc` (`user-lookup-endpoint-v1`) | Live local build: local `main` (`f327a95 "fixed issue"`) + `GET /users/lookup` + tooling |
| `OLD` local `main` | `f327a95` | Ahead 1 / behind 4 vs `origin/main`; carries the user-API fixes |
| `OLD` `origin/main` | `22dc995` | Upstream: `272434d` + security commits; **does not have `f327a95`** |
| `NEW` | no `.git` | Content matches `origin/main` + the document-validation feature, except that the router-level JWT dependency that `origin/main` enables is commented out |

`git diff --stat origin/main..HEAD` in `OLD` shows the live branch differs from upstream in exactly
the files that make the integration safe:

- `app/schemas/user_schema.py` — optional `email`/`phoneNumber`/`conversationId`, blank-name guard
- `app/repositories/salesforce_user_repository.py` — `format_soql` binding, email-first lookup,
  `SalesforceResourceNotFound` only
- `app/services/user_service.py` — `split_name` blank guard, `bool()` on the checkbox
- `app/routes/user_routes.py` — `GET /users/lookup`

`NEW` has the upstream versions of all of these. **The update must be merged onto the fixes, not
deployed over them.**

### 2.2 File-level delta

`NEW` vs `OLD` live branch: 40 files, +4,523 / −1,117 (including `__pycache__` noise). `NEW` vs
`origin/main`: 16 files, +4,393 / −942. The meaningful additions:

| File | Purpose |
|---|---|
| `app/core/admission_document_config.py` | Canonical types, categories, program rules, thresholds |
| `app/core/security.py` | JWT decode scaffolding (routes still commented out) |
| `app/services/academic_score_extractor.py` | Transcript / Class-12 extraction (PyMuPDF + OCR) |
| `app/services/exam_score_extractor.py` | SAT / GRE / GMAT / TOEFL extraction |
| `app/services/identity_document_extractor.py` | Passport MRZ extraction (OpenCV + Tesseract) |
| `app/services/document_validation_service.py` | Per-document PASSED / REVIEW_REQUIRED / FAILED |
| `app/services/eligibility_service.py` | Four-category aggregation → Eligible / Not Eligible / Not Evaluated |
| `app/services/document_service.py` (rewritten) | `upload_and_verify_document`, history, recalculation |
| `app/repositories/salesforce_admission_repository.py` (rewritten) | `Document_Validation_Results__c` history + `Course_Eligibility__c` |
| `app/repositories/salesforce_document_repository.py` (rewritten) | Picklist mappings, verification written at create time |
| `test_demo_rules.py` | Threshold assertions (currently inconsistent — see §2.5) |
| `requirements.txt` | Adds only `pypdf` (incomplete — see §2.4) |

### 2.3 New capabilities

- **Document types:** `ID_PROOF`, `CLASS_12_MARKSHEET`, `UNDERGRADUATE_TRANSCRIPT`, `SAT`, `GRE`,
  `GMAT`, `IELTS`, `TOEFL` (`NEW/app/core/admission_document_config.py:10-13`).
- **Program rules:** Bachelor (12th ≥ 70%), Master (UG GPA ≥ 3.2/4.0), MBA (GMAT ≥ 600 etc.);
  IELTS 6.5 / TOEFL 72 (`:28-51`).
- **Validation output:** `verification_status` ∈ PASSED / REVIEW_REQUIRED / FAILED and
  `requirement_status` ∈ SATISFIED / PROVISIONAL / BELOW_THRESHOLD / REVIEW_REQUIRED
  (`NEW/app/services/document_validation_service.py:277-292`).
- **Eligibility output:** four categories (ID proof, academic record, entrance exam, English);
  IELTS/TOEFL are alternatives; overall PASSED / FAILED / REVIEW_REQUIRED / MISSING
  (`NEW/app/services/eligibility_service.py:118-367`). Maps to Salesforce values `Eligible`,
  `Not Eligible`, `Not Evaluated` (`:321-330`). **`offer_letter_authorized` is hard-coded
  `False` (`:358`)** — the API does not issue or authorize offers.
- **Two new write targets on Customer:** `Document_Validation_Results__c` (JSON history, max
  32,768 chars) and `Course_Eligibility__c`
  (`NEW/app/repositories/salesforce_admission_repository.py:42-80`).
- **New endpoints:**
  - `POST /admissions/{application_no}/documents/upload-and-verify` — one PDF ≤ 10 MB
    (`NEW/app/routes/document_routes.py:173-206`)
  - `POST /admissions/{application_no}/documents/evaluate-eligibility`
  - `POST /admissions/{application_no}/documents/evaluate-and-save` (can never succeed — §2.5)

### 2.4 Contract-breaking changes

| Change | `OLD` | `NEW` | Impact on UD |
|---|---|---|---|
| Chunked upload routes | init / chunks / complete | **removed** | **Breaks offer + student document uploads** |
| Single upload route | — | `upload-and-verify` | Cannot accept an offer letter; only 8 canonical types |
| `Document_Type__c` writing | raw value (e.g. `Other`) | mapped to `ID Document`, `12th Marksheet`, `Academic Transcript`, `Test Score`, `IELTS`; unknown **raises** (`NEW/app/repositories/salesforce_document_repository.py:18-55, 187-191`) | UD's `Other`, `Resume`, `Financial Document`, `Recommendation Letter`, `10th Marksheet` are rejected |
| Verification status on create | `"Pending"` | `ELIGIBLE` / `REVIEW_REQUIRED` / `FAILED` (`NEW/...:39-45, 189-221`) | Org's restricted picklist has **no `Pending`**; the old hard-coded `"Pending"` is invalid. Validated uploads use valid values; offer-letter/raw paths must omit the field |
| Verification field | `Rejection_Reason__c` | `Failed_Reason__c` (`NEW/...:104, 207`) | Org field must exist; UD does not call verify today |
| Document response | `rejection_reason` | `failed_reason` (`NEW/app/schemas/document_schema.py:24-27`) | UD only reads `file_name`/`document_id` — safe |
| `GET /users/lookup` | present on live branch | **removed** (`NEW/app/routes/user_routes.py`) | UD does not use it — safe to lose, but document it |
| `UserResponse.email` | optional on live branch | **required** (upstream) | **Re-breaks R18** — phone-only records answer 500 on read |
| `find_user` | `format_soql`, email then phone | f-string `WHERE Email__c = '{email}' OR Phone__c = '{phone}' LIMIT 1` | SOQL injection returns; lookup semantics change |
| `get_user_by_id` | catches `SalesforceResourceNotFound` only | catches `Exception` | Auth/network failures become 404 “User not found.” |
| `UserLookupCreateRequest` | whitespace strip + `min_length=1` | plain `str` (`NEW/app/schemas/user_schema.py:9-15`) | Blank name → `IndexError` → 500 (R17) returns |
| Auth | none | JWT scaffolding present, **router dependency commented out** (`NEW/app/routes/user_routes.py:2,20`; `NEW/app/routes/admission_routes.py:6,25`); `origin/main` has it enabled | No 401s today, but `security.py` imports `jwt`, which is not in `requirements.txt` — a security regression relative to upstream if this build leaves localhost |
| JWT config | — | `security.py` needs `JWT_PUBLIC_KEY`; `.env.example` offers `JWT_JWKS_URL` and no public key | Enabling auth today fails on a missing key |
| Dependencies | no `pypdf` | only adds `pypdf` | `fitz`, `cv2`, `numpy`, `pytesseract`, `PIL`, `jwt` are imported by `NEW` but **absent from `requirements.txt`** |

**Dependency check (static, verified):** `NEW` imports `fitz`, `cv2`, `numpy`, `pytesseract`,
`PIL`, `jwt`. `requirements.txt` pins only FastAPI/Pydantic/dotenv/requests/simple-salesforce/
uvicorn/python-multipart/pypdf. The `OLD` venv has `jwt` but **not** `pypdf`, `fitz`, `cv2`,
`numpy`, `pytesseract`, or `PIL`. The `UD` venv has `numpy`, `PIL`, `pypdf`, `jwt` but **not**
`fitz`, `cv2`, or `pytesseract`. A fresh `NEW` venv built from its `requirements.txt` will fail at
the first document extraction.

### 2.5 Defects found in `NEW` (fix before relying on them)

1. **`evaluate-and-save` can never succeed.** The route calls
   `evaluate_and_save_application_documents` (`NEW/app/services/document_service.py:976-1055`),
   which calls `update_customer_document_validation(...)` without `state=`. The repository rejects
   a missing state with `"Direct evaluate-and-save is disabled to protect document history; use
   upload-and-verify"` (`NEW/app/repositories/salesforce_admission_repository.py:63-64`). Every
   call is a 400. Either remove the endpoint or implement it as a history append.
2. **`admission_service.save_admission_document_evaluation` is dead and broken.** It calls the same
   repository without `state` (`NEW/app/services/admission_service.py:211-216`) and reads
   `evaluation.get("validation_status")` from `admission_document_config.evaluate_application`,
   which returns `eligibility_status`, not `validation_status` (`:177-203` vs
   `NEW/app/core/admission_document_config.py:124`).
3. **`test_demo_rules.py` asserts keys that do not exist.** It asserts
   `result['validation_status']` on `validate_document` (returns `verification_status`) and
   `evaluate_application(...)['validation_status']` (returns `eligibility_status`) —
   `NEW/test_demo_rules.py:29-32`. The suite will `KeyError`, not fail an assertion.
4. **Duplicate source copies:** `academic_score_extractor.py.bak` and
   `academic_score_extractor_backup.py` are byte/largely-duplicate 491/448-line files inside
   `app/services/`. Exclude them from deployment and delete them.
5. **JWT configuration is inconsistent, and the update disables auth that upstream enables.**
   `origin/main` protects both routers with `Depends(get_current_user)`; `NEW` comments those
   dependencies out (`NEW/app/routes/user_routes.py:2,20`;
   `NEW/app/routes/admission_routes.py:6,25`). `security.py` itself decodes with `JWT_PUBLIC_KEY`
   (`NEW/app/core/security.py:27-31`), but `.env.example` offers `JWT_JWKS_URL` and no
   `JWT_PUBLIC_KEY`; `PyJWT` is not pinned. Enabling the dependencies today would raise
   `ModuleNotFoundError` or `TypeError` on the missing key. For this local integration the disabled
   state is convenient, but it is a deliberate security downgrade and must not travel unchanged to
   a shared host.
6. **Program detection rejects most Meridian programs.** `_customer_program`
   (`NEW/app/services/document_service.py:693-712`) accepts `MBA`, `B.TECH…`, `B.SC…`, `BACHELOR…`,
   `M.TECH…`, `M.SC…`, `MASTER…`, or an explicit `Program__c`/`Education_Level__c`. By inspection,
   `BBA`, `BCA`, `B.Com`, `BA`, `MCA`, `M.Com`, and `MA` all raise
   `"Unable to identify Bachelor, Master, or MBA program"`. These are real Meridian programs
   (`UD/app/programs.py:32-79`). The upload route answers 400 for them.
7. **History growth has a hard ceiling.** Every upload appends to
   `Document_Validation_Results__c`; the repository rejects an encoding over 32,768 chars
   (`NEW/...:6, 73-74`). With repeated re-uploads this is reachable; the stated remediation is
   "move history to a child object".
8. **`upload-and-verify` blocks the API's event loop.** The route is declared `async def`
   (`NEW/app/routes/document_routes.py:173-206`) and then calls the synchronous
   `upload_and_verify_document(...)` inline. FastAPI runs `async def` handlers on the event loop,
   so PDF extraction/OCR blocks every other request — including status pushes for live calls —
   for the duration. Fix by declaring the route `def` (FastAPI then uses its threadpool) or by
   wrapping the call in `run_in_threadpool` / `asyncio.to_thread`.

---

## 3. Target flow — validate the document, then decide the offer from the marks

This is the confirmed requirement: **an uploaded document no longer triggers an offer by itself.
The API extracts the marks, compares them with the program's requirement, and only a satisfied
requirement produces an offer.**

### 3.1 End-to-end sequence

```
Student uploads a document (WhatsApp media / web upload)
        │
        ▼
UD saves it locally (lead_documents) and selects the API document type
        │
        ▼
POST /admissions/{application_no}/documents/upload-and-verify
        │  document_type = CLASS_12_MARKSHEET | UNDERGRADUATE_TRANSCRIPT | ...
        │  file          = PDF (images converted first)
        ▼
NEW extracts text/OCR → validate_document → create ContentVersion
        │  → appends Document_Validation_Results__c history
        │  → recalculates Course_Eligibility__c
        ▼
Response: validation_status, extracted mark, minimum required,
          reason_codes, message, action_required,
          course_eligibility, missing_documents
        │
        ▼
UD decides (app/offers/eligibility.py — new)
        │
        ├─ PASSED / requirement satisfied ──► generate_and_send_offer(...)
        │                                        PDF now contains "Basis of offer"
        │                                        → WhatsApp + email
        │                                        → offer PDF uploaded to CRM
        │                                        → Offer_Status__c=Offered, Released=True
        │
        ├─ BELOW_THRESHOLD ─────────────────► no offer; explain the mark vs requirement
        │                                        CRM: Course_Eligibility__c=Not Eligible (API)
        │
        ├─ REVIEW_REQUIRED ─────────────────► no auto-offer; manual-review message
        │                                        CRM: Not Evaluated / Under Review
        │
        ├─ WRONG DOCUMENT TYPE ─────────────► no offer; ask for the correct document
        │
        └─ GATE DOCUMENT MISSING ───────────► no offer; ask for the required document
```

### 3.2 What the API actually returns for one upload

`upload-and-verify` returns the compact shape built at
`NEW/app/routes/document_routes.py:87-120`:

| Field | Meaning | Used by the offer decision |
|---|---|---|
| `validation_status` | PASSED / FAILED / REVIEW_REQUIRED for this document | ✅ primary |
| `extracted_percentage` | Class-12 percentage (when type is `CLASS_12_MARKSHEET`) | ✅ display + audit |
| `minimum_required_percentage` | 70.0 for Class 12 | ✅ display + audit |
| `extracted_gpa`, `minimum_required_gpa` | UG GPA and 3.2 threshold (transcript) | ✅ display + audit |
| `extracted_score`, `minimum_required_score` | IELTS/SAT/GRE/GMAT/TOEFL score and threshold | ✅ display + audit |
| `reason_codes` | e.g. `BELOW_THRESHOLD`-driving codes, `DOCUMENT_TYPE_MISMATCH`, `ACADEMIC_LEVEL_MISMATCH`, `DOCUMENT_NOT_ACCEPTED_FOR_PROGRAM` | ✅ choose the message / block kind |
| `message`, `action_required` | API-authored student-facing text | ✅ reuse or map |
| `course_eligibility` | `Eligible` / `Not Eligible` / `Not Evaluated` across **active history** | ⚠️ display / strict mode only — not the academic gate (see §3.4) |
| `missing_documents` | Categories still missing, e.g. `ID_PROOF`, `SAT`, `IELTS_OR_TOEFL` | ✅ messaging, not the academic gate |
| `document_id`, `is_active` | ContentVersion id and whether it became the active evidence for its type | ✅ store |

Required status: the service itself computes `PASSED` only when validation passed **and** the
requirement is not below threshold (`NEW/app/services/document_service.py:940-950`). That makes
`validation_status` the authoritative per-document gate.

**One field is missing from the compact response.** `requirement_status`
(`SATISFIED` / `BELOW_THRESHOLD` / `REVIEW_REQUIRED` / `NOT_EVALUATED`) is computed but dropped by
`_compact_upload_response` (it is not in the dict at `NEW/app/routes/document_routes.py:87-120`).
Without it, a `FAILED` result cannot be distinguished as “marks below threshold” vs “wrong
document” without parsing `reason_codes`. Add `requirement_status` to the compact response in
Phase 3; §3.3 depends on it.

### 3.3 Marks → decision matrix

Program thresholds come from `NEW/app/core/admission_document_config.py:48-51` and are echoed in
the response's `minimum_required_*` fields.

| Program (API `_customer_program`) | Gate document | Requirement | Below → |
|---|---|---|---|
| `BACHELOR` | `CLASS_12_MARKSHEET` | percentage ≥ 70.0 | no offer — below threshold |
| `MASTER` | `UNDERGRADUATE_TRANSCRIPT` | GPA ≥ 3.2 / 4.0 | no offer — below threshold |
| `MBA` | `UNDERGRADUATE_TRANSCRIPT` | GPA ≥ 3.2 / 4.0 | no offer — below threshold |
| Any (optional, strict) | `SAT` / `GRE` / `GMAT` / `TOEFL` | 1100 / 290 / 600 / 72 | strict mode: no offer |
| Any (optional, strict) | `IELTS` | 6.5 | strict mode: no offer |
| Any | `ID_PROOF` | type match only | never blocks an offer |

Decision for the **uploaded gate document**:

| `validation_status` / signal | Decision | Student message |
|---|---|---|
| `PASSED` | **issue offer** | “Your 12th marksheet shows 78%, which meets the 70% requirement. Generating your offer now.” |
| `FAILED` + `requirement_status = BELOW_THRESHOLD` | **block_not_eligible** | “Your 12th marksheet shows 62%, below the 70% requirement for this program.” |
| `FAILED` + `DOCUMENT_TYPE_MISMATCH` / `ACADEMIC_LEVEL_MISMATCH` / `DOCUMENT_NOT_ACCEPTED_FOR_PROGRAM` | **block_wrong_document** | “This looks like a transcript; this program needs a 12th marksheet. Please upload the correct document.” |
| `REVIEW_REQUIRED` (unreadable, low OCR, uncertain GPA scale, missing score) | **block_review** | “We could not reliably read the marks; our admissions team will review it.” |
| Gate document never uploaded | **block_missing** | “To check your eligibility, please upload your 12th marksheet.” |
| Document saved but API call failed / timed out | **block_unknown** | “We received your document but could not verify it right now. Our team will review it.” |

**Important:** the offer decision is made from the *gate document's* `validation_status` and marks,
not from `course_eligibility` alone. `course_eligibility` is an aggregate over the four categories
and is `Not Evaluated` until every category has a document (§3.4).

### 3.4 Which requirements gate an offer — the one product decision that matters

The API's `evaluate_eligibility` requires **four categories**: `ID_PROOF`, `ACADEMIC_RECORD`,
`ENTRANCE_EXAM`, `ENGLISH_PROFICIENCY` (`NEW/app/services/eligibility_service.py:38-43`). A student
who uploads only a Class-12 marksheet therefore gets `course_eligibility=Not Evaluated`, even
though the marksheet itself says 78% ≥ 70%.

Two modes, selected by `OFFER_ELIGIBILITY_MODE`:

| Mode | Gate | Effect |
|---|---|---|
| `academic` (**recommended default**) | The program's academic gate document only (Class 12 / UG transcript) | “Based on the marks” — offers issue from the academic document; ID/exam/English remain advisory unless separately required |
| `strict` | All four categories must be `PASSED` (`course_eligibility=Eligible`) | Strongest gate; with the current demo document flow, offers will be rare until students supply ID + exam + English too |

`academic` matches the stated requirement and the current demo behaviour; `strict` should be
available but not the default. Do not silently mix the two: the same uploaded document can be
`validation_status=PASSED` and `course_eligibility=Not Evaluated` at the same time.

#### Keeping Salesforce consistent in `academic` mode

The API derives `Course_Eligibility__c` from all four categories, so in `academic` mode the CRM
can contradict the offer:

| Situation | API aggregate | UD decision | CRM would show |
|---|---|---|---|
| Only the gate document uploaded and passed | `Not Evaluated` (three categories missing) | issue | `Course_Eligibility__c=Not Evaluated` with an offer attached |
| Gate passed, an optional IELTS failed | `Not Eligible` | issue | `Not Eligible` with an offer attached |

That is unacceptable for a CRM humans read. Pick one:

1. **Recommended — make the API's required set configurable.** Add
   `required_categories: list[str] = all four` to `evaluate_eligibility` (and pass it through
   `update_customer_document_validation`), then set `["ACADEMIC_RECORD"]` for `academic` mode.
   `Course_Eligibility__c` then means the same thing the offer decision means. This is a small,
   contained API change and the cleanest long-term answer.
2. **Keep `strict` mode only.** One source of truth, but offers require ID + academic + exam +
   English; the demo flow must supply all four.
3. **Do not write `Course_Eligibility__c` in academic mode** and use a separate field for the
   academic gate. Less clean; the CRM loses the aggregate.

Do not ship option “issue on academic while the CRM says Not Eligible” without an explicit
sign-off — it recreates the exact class of contradiction the status-sync work was built to remove
(`UD/doc/salesforce/STATUS_2026-09-13.md:86-90`).

### 3.5 Where the decision lives in UD

Add one pure module, `app/offers/eligibility.py`:

```python
@dataclass(frozen=True)
class OfferDecision:
    action: str            # "issue" | "block_not_eligible" | "block_wrong_document"
                           # | "block_review" | "block_missing" | "block_unknown"
    reason: str            # short operator-facing reason
    message: str           # student-facing text
    evidence: dict         # document type, score, threshold, validation_status, document_id

def assess_offer_eligibility(lead, program, validation_results) -> OfferDecision:
    ...
```

Call sites:

- `evaluate_offer_readiness` (`app/offers/service.py:66-102`) — extend the return with
  `decision`. Keep the existing field checks; replace `if not docs: missing.append("document")`
  with `if not gate_document: missing.append("gate_document")`.
- `generate_and_send_offer` (`app/offers/service.py:108-346`) — after the readiness check, refuse
  unless `decision.action == "issue"`; return the decision so callers can message the student.
  `force=True` bypasses the idempotency guard only, **not** the eligibility gate; add a separate
  staff-only `override_eligibility` flag with an audit log if operations needs it.
- Web upload (`app/main.py:5152-5163`) — currently auto-calls the offer when `ready`; change to
  call the offer only when `decision.action == "issue"`, otherwise return the decision so the UI
  can show the message.
- WhatsApp "done" (`app/main.py:2706-2711` and the offer trigger) — same gate; the reply text comes
  from `decision.message`.
- Dashboard/manual generation — show the decision before the button; keep staff override explicit.

### 3.6 The offer letter itself must show the basis of the offer

“Generating the offer letter based on the marks” should be visible in the PDF, not only in the
decision. Extend `app/offers/pdf.py:45-207` and the `offer_letters` row:

- New “Basis of Offer” block: document type, extracted mark, program requirement, validation date,
  and `validation_status` (e.g. “Class 12 percentage: 78% — requirement: 70% — Verified 2026-11-04”).
- Store the evidence snapshot with the offer (see §5, Phase 4) so a regenerated PDF or an audit
  shows exactly which marks justified it.
- Never print passport/ID numbers, MRZ data, or full document numbers in the offer PDF.
- If a conditional offer is ever added for `PROVISIONAL` records, the conditions must be printed.
  The current API maps `PROVISIONAL` to `REVIEW_REQUIRED`, so there is no auto-conditional offer
  today.

### 3.7 Student messaging

Reuse the API's `message` where it is accurate, but map the decision to the app's tone and
channel. The message must always state **the extracted mark and the requirement**, not just
“eligible/not eligible”:

| Decision | WhatsApp / web text (template) |
|---|---|
| `issue` | “Your {document} shows {mark} (requirement {threshold}). You meet the requirement — I'm generating your offer letter now.” |
| `block_not_eligible` | “Your {document} shows {mark}, below the {threshold} required for {program}. I can't issue an offer for this program.” |
| `block_wrong_document` | “That document doesn't match what {program} needs. Please upload {gate_document}.” |
| `block_review` | “I couldn't read the marks reliably. Our admissions team will review your document.” |
| `block_missing` | “To check your eligibility, please upload {gate_document}.” |
| `block_unknown` | “I received your document but couldn't verify it right now. Our team will review it.” |

### 3.8 CRM state after the decision

| Decision | `Course_Eligibility__c` (API writes) | `Admission_Status__c` (UD may push) | `Offer_Status__c` |
|---|---|---|---|
| `issue` | unchanged by UD; API's value reflects active history | `Under Review` (existing `push_offer_released` default) | `Offered` |
| `block_not_eligible` | `Not Eligible` (if the API's aggregate concludes it) | `Documents Pending` or `Under Review` — **not** `Rejected` without sign-off | untouched |
| `block_review` | `Not Evaluated` | `Under Review` | untouched |
| `block_missing` | `Not Evaluated` | `Documents Pending` | untouched |
| `block_unknown` | unknown; do not overwrite | `Under Review` with a note | untouched |

UD must **not** write `Course_Eligibility__c` through `push_profile` — it is API-owned. The
`WRITABLE_FIELDS` allowlist (`app/crm/status.py:56-67`) already excludes it; keep it that way.

### 3.9 Re-uploads, multiple attempts, and idempotency

- **The API keeps history.** `_record_upload_and_recalculate` appends every attempt and marks only
  the latest passing document active for its type; a later *failing* attempt does not deactivate an
  earlier pass (`NEW/app/services/document_service.py:792-838`). A student who passes, then
  uploads a worse scan, keeps the pass.
- **A better re-upload can promote a new pass.** Uploading 75% after a 62% attempt makes the 75%
  document active and recalculates eligibility. The next offer trigger can then issue.
- **UD should dedupe bytes.** The API creates a ContentVersion for every call and does not dedupe
  content. Add `sha256` to `lead_documents`, skip an identical re-upload, and keep the existing
  offer-document duplicate defence (`app/crm/documents.py:180-255`).
- **Fix the offer idempotency guard first.** `get_recent_offer_for_lead(within_hours=...)`
  truncates fractional hours with `int()` (`app/offers/models.py:800-835`), so the default guard
  is effectively zero. Enabling a stricter offer gate makes this more visible: a student typing
  “done” twice should not get two offers.

### 3.10 Latency and failure behaviour

- `upload-and-verify` is **synchronous inside an `async def` route**: PDF text extraction and OCR
  run on the API's event loop (see §2.5 item 8), so a slow scan blocks every other request to the
  API while it runs. Text PDFs are fast; scans can take seconds. Fixing the route to run in a
  threadpool is a Phase 3 prerequisite, not an optimisation — otherwise one student's scan can
  stall a live voice call's status push. The HTTP caller still waits for the result either way.
- Raise `CRM_UPLOAD_TIMEOUT_S` from 30 s to **90–120 s** for validation uploads. Keep the
  chunked-upload timeout separate for the offer PDF (which is small and fast).
- **Do not offer on an ambiguous response.** If `upload-and-verify` times out or returns 5xx after
  the ContentVersion was created, record `crm_upload_status=unknown`, compute
  `block_unknown`, and let a human check — the API has no document delete route.
- For the web upload path, either (a) await validation and show a “checking your document” state,
  or (b) return immediately and deliver the offer/decision asynchronously. Phase 3 uses (a) for
  simplicity; Phase 4 can move to (b) if OCR latency is unacceptable. WhatsApp already has a
  natural asynchronous window: the document arrives, then the student types “done”.

### 3.11 Impact on the existing pipeline

| Pipeline stage | Survives cutover? | Why |
|---|---|---|
| Identity link (`lookup-or-create`) | ⚠️ works, but regressions | R18/R17/SOQL fixes missing; phone-only students become unreachable |
| Course / conversation / sentiment pushes | ✅ | Routes and field semantics unchanged |
| Application-number resolution | ✅ | `GET /admissions/{id}` unchanged |
| Duplicate offer check | ✅ | `GET /admissions/{app}/documents` shape unchanged for the fields UD reads |
| **Offer PDF upload** | ❌ | `uploads` / `chunks` / `complete` removed; `upload-and-verify` rejects a non-canonical “offer” document |
| **Student document upload** | ❌ | Same removed protocol; plus canonical type + PDF-only + program-detection constraints |
| Offer release status push | ✅ | `PATCH /admissions/{id}` unchanged |
| Accept / decline push | ✅ | unchanged |
| Expiry push | ✅ | unchanged |
| **Offer readiness** | ❌ **changes by design** | Replaced by the marks decision in §3.3; “one document” is no longer sufficient |
| Student-facing upload feedback | ❌ **changes by design** | Must show extracted mark vs requirement, and the block reason |

**Bottom line:** the pipeline's *delivery* half (PDF → WhatsApp/email → status pushes) is safe. The
*persistence* half (documents into Salesforce) is entirely dependent on the removed protocol, and
the *decision* half changes from “any document” to “marks meet the requirement”.

### 3.12 Sample verification — results from the supplied PDFs (2026-11-04)

Ran the `NEW` extractors and `validate_document` over all 19 files in
`Document samples-20261103T194256Z-1-001\Document samples` using an isolated venv
(PyMuPDF 2.1 / numpy / OpenCV / pytesseract / Pillow / pypdf). **Read-only:** no Salesforce calls
and no files modified. Tesseract is **not installed** on this machine.

| Sample | Extracted | Requirement | Would be |
|---|---|---|---|
| `12th_marksheet_sample_1` | 70.83% (Class 12) | SATISFIED | **pass** (70.83 ≥ 70) |
| `12th_marksheet_sample_2` | 71.6% (Class 12, **OCR**) | SATISFIED | **pass** (71.6 ≥ 70) |
| `12th_marksheet_sample_3` | detected **Class 10**, no percentage | mismatch | **wrong document** |
| `12th_marksheet_sample_4` | 85.33% (Class 12) | SATISFIED | **pass** |
| `GMAT_sample_1` | 330 | BELOW_THRESHOLD | **block** (600 required) |
| `GMAT_sample_2` | 760 | SATISFIED | pass (strict mode) |
| `GRE_sample_1` | 313 | SATISFIED | pass (strict mode) |
| `GRE_sample_2` | 272 | BELOW_THRESHOLD | block (290 required) |
| `SAT_sample_1` | 1540 | SATISFIED | pass (strict mode) |
| `SAT_sample_2` | 1024, internally inconsistent | REVIEW_REQUIRED | manual review |
| `TOEFL_sample_1` | 65 | BELOW_THRESHOLD | block (72 required) |
| `TOEFL_sample_2` | 104 | SATISFIED | pass (strict mode) |
| `passport_sample_1` | MRZ valid, **expired** | extraction REVIEW_REQUIRED | manual review (service downgrade) |
| `passport_sample_2` | MRZ valid, in date | SATISFIED | pass (ID never gates) |
| `transcript_sample_1_graduate` | GPA 3.73, detected **GRADUATE** level | level mismatch | **wrong document** for a Master applicant |
| `transcript_sample_4` (×3 duplicates) | GPA 3.906 / 4.0 | SATISFIED | pass |
| `transcript_sample_6` | GPA 3.03 / 4.0 | BELOW_THRESHOLD | **block** (3.2 required) |

What this changes in the plan:

1. **Tesseract is mandatory for scans.** `12th_marksheet_sample_2` is scanned. Without the binary,
   the extractor raises `RuntimeError: Scanned PDF requires ... Tesseract`;
   `upload_and_verify_document` catches extractor exceptions and converts them to
   `REVIEW_REQUIRED` (`NEW/app/services/document_service.py:888-913`), so the API returns manual
   review rather than 500. **Tesseract 5.4.0 was installed on 2026-11-04** at
   `C:\Program Files\Tesseract-OCR`; the re-run extracted **71.6%** and passed (71.6 ≥ 70). Any
   machine that handles scans must have the binary and its `eng.traineddata`.
2. **The wrong-document path is real.** `12th_marksheet_sample_3` is a Class 10 marksheet and
   `transcript_sample_1_graduate` is a graduate transcript. Both must produce
   `block_wrong_document`, not “below threshold”.
3. **Use the service's `validation_status`, not `validate_document`'s `verification_status`.**
   `transcript_sample_6` has `verification_status=PASSED` but `requirement_status=BELOW_THRESHOLD`;
   the service derives `validation_status=FAILED`. A decision built on the raw validator would
   wrongly issue an offer.
4. **Expired passports are downgraded by the service.** `validate_document` alone returns PASSED
   for `passport_sample_1`; `upload_and_verify_document` sees `extraction_status=REVIEW_REQUIRED`
   and downgrades it. The decision must read the API response, not a local re-validation.
5. **Duplicates are byte-identical** (`transcript_sample_4(1)/(2)`). The `sha256` dedupe in Phase 3
   would prevent three identical ContentVersions.
6. **Threshold boundary is exercised**: 70.83 passes, 65 and 3.03 fail. Keep these samples as the
   regression set for Gate 3/Gate 4.

---

## 4. Integration design — decisions to make

> **Contract note.** The existing integration treats the admission API as a **frozen external
> contract** (plan decision D1; `doc/salesforce/STATUS_2026-09-13.md`). D2/D3 below deliberately
> propose changes **on the API side**, because the new build removes a capability the offer
> pipeline needs and regresses fixes the live branch carries. If the API must remain frozen, the
> only alternatives are to keep the old chunked endpoints running side-by-side or to stop uploading
> offer/student documents — neither meets the requirement. Get explicit sign-off before editing
> `NEW`.

| ID | Decision | Recommendation |
|---|---|---|
| **D1** | Replace in place vs stage | **Stage.** Run `NEW` on `:8099` with its own venv and `.env`; keep `:8098` live until gates pass. `CRM_BASE_URL` selects the target. |
| **D2** | How offer PDFs reach Salesforce | **Add a non-validating offer-letter endpoint** to `NEW` (`POST /admissions/{application_no}/documents/offer-letter`), backed by a storage function that **omits `Verification_Status__c`** (the org's restricted picklist has no `Pending`; the field is nillable) and performs no OCR/history write. File it as `Document_Type__c="Other"` (the org has no `Offer Letter` value). Add a **legacy chunked shim** for one release so UD needs no change in Phase 1. Never route an offer letter through `upload-and-verify`. |
| **D3** | Upstream user-API regressions | **Port `f327a95` + `0fc5328` into `NEW` before cutover.** Treat the upstream user API as unsafe for this integration. Add regression tests for R17/R18/SOQL. |
| **D4** | Student document strategy | **Two lanes.** Canonical, evidence-bearing documents → `upload-and-verify`; unsupported types (`Resume`, `Financial Document`, `Recommendation Letter`, generic `Other`) → a raw-upload endpoint (or don't upload). Convert images to PDF before upload (the API rejects non-PDF). |
| **D5** | Offer gating | **Confirmed: validate first, offer only on satisfied marks.** Implement `OFFER_ELIGIBILITY_MODE=academic` (default) or `strict` (§3.4). Rollout order: compute in shadow mode → enforce. `Not Eligible` / review / missing / wrong type → no offer + explanation. |
| **D6** | Auth | Keep JWT **off** for cutover (as shipped). Fix the config/dependency mismatch, and expose the existing `CRM_API_KEY` header path before any non-localhost deployment. |

### 4.1 Target flow (after Phase 4)

```
Student document
   │
   ├─ canonical type & PDF ──► POST /admissions/{app}/documents/upload-and-verify
   │                              └─ returns validation_status, extracted mark,
   │                                 minimum threshold, reason_codes, message,
   │                                 action_required, missing_documents,
   │                                 course_eligibility
   ├─ unsupported type ──────► POST /admissions/{app}/documents/raw   (document_type="Other")
   │
   ▼
app/offers/eligibility.py  ──►  issue | block_not_eligible | block_wrong_document |
                                  block_review | block_missing | block_unknown
   │
   ├─ issue ──► generate_and_send_offer → offer PDF (with Basis of Offer)
   │                                      → POST /admissions/{app}/documents/offer-letter
   │                                      → Offer_Status__c=Offered / Released=True
   └─ block ──► student message + CRM admission status; no offer row, no PDF
```

`Course_Eligibility__c` is written by the API on validated uploads (aggregate over active
history). UD reads it for display/strict mode, but the **academic gate** uses the uploaded
document's `validation_status` and extracted mark (see §3.4).

---

## 5. Phased implementation plan

### Phase 0 — Decisions and prerequisites (no code)

- [ ] Confirm D5 (offer gating) with the admissions/product owner.
- [ ] Confirm the ownership model: may we modify `NEW`, or must UD adapt to it as-is? D2 and D3
      assume we may modify it.
- [ ] Obtain org confirmation for the fields/picklists in §6.
- [ ] Decide the fate of documents already in Salesforce: no history rows exist for them, so the
      new eligibility recalculation will ignore them until they are re-uploaded or backfilled.
- [ ] Copy `SALESFORCE_*` credentials from the supplied folder into `NEW/.env`; confirm `.env` is
      gitignored; rotate if the folder has been shared.
- [ ] Provision Tesseract if scanned PDFs must be supported; otherwise accept
      `REVIEW_REQUIRED` for scans.

**Gate 0:** signed-off decisions; field/picklist checklist complete.

### Phase 1 — Bring up `NEW` in shadow, preserving the live contract

`NEW`:
- [ ] Initialize git in `NEW` (or merge into a branch of `OLD`) with `origin/main` as the base and
      re-apply `f327a95` + `0fc5328`. Do not hand-edit the files without history.
- [ ] Add `PyMuPDF`, `opencv-python-headless`, `numpy`, `pytesseract`, `Pillow` to
      `requirements.txt`; add `PyJWT` only if auth is enabled.
- [ ] Create `venv`, `.env`; smoke-test `import main` and `/health` on `:8099`.
- [ ] Implement `store_document_without_validation()` (no OCR, no history, **omit
      `Verification_Status__c`** — the org's restricted picklist has no `Pending` and the field is
      nillable).
- [ ] Fix the `upload-and-verify` event-loop block (§2.5 item 8): make the route `def` or run the
      service in `run_in_threadpool`.
- [ ] Make the offer-letter `Document_Type__c` configurable and default it to `Other` (the org has
      no `Offer Letter` value). Do **not** add a `PENDING` mapping; use “omit”.
- [ ] Expose it as:
      - `POST /admissions/{application_no}/documents/offer-letter` (new contract), and
      - the legacy `uploads` / `chunks` / `complete` routes as a **deprecated shim** over the same
        storage function (so UD is untouched in Phase 1).
- [ ] Delete `.bak` / `_backup.py` duplicates; fix or remove the broken `evaluate-and-save` and
      `save_admission_document_evaluation`; fix `test_demo_rules.py`.
- [ ] Regenerate `openapi.json` and store it for contract review.

`UD`:
- [ ] Add `CRM_UPLOAD_MODE=legacy|updated` (default `legacy`); point `CRM_BASE_URL` at `:8099`
      for the shadow run only.
- [ ] No behavior change yet.

**Setup already completed on this machine (2026-11-04):**
- [x] `NEW/.env` created from the live API's credentials (same org, `power-agility-491`);
      `.env` is covered by `.gitignore`. No values printed or committed.
- [x] `NEW/venv` created (Python 3.11); PyMuPDF, OpenCV, numpy, pytesseract, Pillow and pypdf
      installed; `import main` succeeds.
- [x] `uvicorn main:app` served `GET /health` → `{"status":"ok"}` on `127.0.0.1:8099`; the test
      process was stopped.
- [x] **Tesseract 5.4.0 installed** at `C:\Program Files\Tesseract-OCR`; the scanned sample now
      extracts 71.6% and passes (§3.12). The installer is per-machine; any other host needs it too.
- [ ] `requirements.txt` is **not yet updated**; the venv has the packages, the file does not.
- [ ] No code changes have been made.

**Gate 1:** the four existing live harnesses pass against `:8099` with `CRM_UPLOAD_MODE=legacy`:
`verify_offer_document_upload.py`, `verify_gate45_voice.py`, `verify_gate6_webchat.py`,
`verify_gate7_status.py`. Point them with `--base-url` / `--crm-url` where supported, otherwise
export `CRM_BASE_URL=http://127.0.0.1:8099` before the run (`verify_gate45_voice.py` has no CRM URL
argument and reads the app's settings). Unit baseline stays green (73 CRM/offer tests).

### Phase 2 — Port the fixes and switch the client to the new upload contract

`NEW`:
- [ ] Confirm the merged user API matches the live fixes: optional `UserResponse` identifiers,
      blank-name guard, `format_soql`, email-then-phone, `SalesforceResourceNotFound` only,
      `GET /users/lookup`.
- [ ] Add tests pinning those behaviours (they are the regression fence).
- [ ] Add `POST /admissions/{application_no}/documents/raw`: multipart PDF for documents that are
      not evidence (`Other`, `Resume`, `Financial Document`, `Recommendation Letter`). Stores a
      ContentVersion **without `Verification_Status__c`** (no `Pending` in the restricted picklist);
      **no OCR, no history, no eligibility write**. This is the Phase 2 lane for student documents
      that cannot be validated.
- [ ] Decide whether the legacy chunked shim stays for a second release or is removed after
      Gate 2; either way it must keep the same duplicate-defence semantics.

`UD`:
- [ ] Rewrite `_transfer` (`app/crm/documents.py:335-450`) into:
      - `_upload_offer_letter` → new offer-letter endpoint (single multipart, no chunk loop);
      - `_upload_student_document_raw` → new raw endpoint (Phase 2 behavior);
      - `_upload_and_verify` → the validation endpoint, added now but wired in Phase 3;
      - keep `_find_existing_offer_document`, `is_create` semantics, and the
        `uploaded` / `failed` / `unknown` outcome model.
- [ ] Preserve the “never retry a create” rule for the new create-shaped call.
- [ ] Update the mocked paths in `tests/test_crm_documents.py`,
      `tests/test_crm_student_documents.py`.

**Gate 2:** offer and student-document uploads pass against `:8099` using `CRM_UPLOAD_MODE=updated`;
student documents use the raw lane and **no offer behaviour changes**; the old gate harnesses pass
unchanged (same `CRM_BASE_URL` override); duplicate defence still suppresses a second offer upload.

### Phase 3 — Capture validation evidence and compute the decision in shadow mode

Goal: prove the marks pipeline on real documents **without changing whether an offer is sent**.
Every upload is validated, the decision is computed and logged, and the current “any document →
offer” behaviour is compared against it. This is the phase that catches OCR mistakes, wrong type
mappings, and program-detection failures before a student is denied an offer.

`UD`:
- [ ] Add `api_document_type()` to `app/crm/identity.py`: `transcript` →
      `UNDERGRADUATE_TRANSCRIPT`, `marksheet` → `CLASS_12_MARKSHEET` (confirm: UD currently files
      this as `10th Marksheet`, which the new API does not model), `id_proof`/`passport` →
      `ID_PROOF`, `ielts` → `IELTS`; `test_score` needs a disambiguation question (SAT/GRE/GMAT/
      TOEFL cannot be inferred from “test_score”); `resume`, `financial`, `recommendation`,
      `other` → raw lane.
- [ ] Add `program_level(program)` returning `Bachelor's` or `Master's`, and push it to
      `Education_Level__c` — **`Program__c` does not exist in this org**. `_customer_program`
      checks `Course__c == "MBA"` first, so ensure `Course__c` is exactly `"MBA"` for MBA
      students; otherwise a `Master's` education level classifies them as `MASTER`, not `MBA`.
      `Education_Level__c` is a restricted picklist (`Diploma`, `Bachelor's`, `Master's`).
      Add it to `WRITABLE_FIELDS` (`app/crm/status.py:56-67`) and call it from
      `link_conversation`/`sync_course` (`app/crm/sync.py:243-247, 311-348`). Without this,
      `_customer_program` rejects BBA/BCA/B.Com/BA/MCA/M.Com/MA uploads.
- [ ] Convert image documents (WhatsApp JPEGs) to PDF before upload, or keep them out of the
      validation lane.
- [ ] Wire `_upload_and_verify` for canonical types; keep `_upload_student_document_raw` for
      unsupported types. Raise the validation call's timeout to 90–120 s (the offer-letter and raw
      calls keep the short timeout).
- [ ] Persist validation evidence. Add to `lead_documents`: `api_document_type`,
      `validation_status`, `requirement_status`, `extracted_score`, `score_threshold`,
      `reason_codes` (JSON/text), `validation_message`, `action_required`, `is_active`, `sha256`.
      Add to `leads`: `crm_course_eligibility`. Add to `offer_letters`:
      `eligibility_action`, `eligibility_evidence` (JSON), `gate_document_id`, `gate_score`,
      `gate_threshold`. Use `ADD COLUMN IF NOT EXISTS` migrations (`app/offers/schema.py`,
      `app/leads/schema.py`).
- [ ] Implement `app/offers/eligibility.py` with `assess_offer_eligibility(...)` returning
      `OfferDecision` (§3.5). It must be pure: no DB, no network — it receives the lead, program,
      and stored validation rows.
- [ ] In shadow mode (`OFFER_ELIGIBILITY_SHADOW=true`), compute the decision after every upload,
      store it, and log `would_issue` / `would_block:<action>` alongside the current outcome.
      Do **not** change `generate_and_send_offer` yet.
- [ ] Add an operator view in the dashboard: document → extracted mark → threshold →
      `validation_status` → decision. This is how the team audits OCR accuracy before enforcement.

`NEW`:
- [ ] Ensure the compact response carries everything the decision needs. It already carries
      `validation_status`, the extracted/minimum score fields, `reason_codes`, `message`,
      `action_required`, `missing_documents`, and `course_eligibility`; **do not drop
      `requirement_status`** unless UD stops needing it (it is currently omitted from the compact
      response — add it if the decision needs to distinguish PROVISIONAL).
- [ ] Implement the `required_categories` parameter on `evaluate_eligibility` and
      `update_customer_document_validation` (see §3.4) if `academic` mode is chosen, so
      `Course_Eligibility__c` matches the offer decision.
- [ ] Extend `_customer_program` to cover the Meridian program names (BBA/BCA/B.Com/BA/MCA/
      M.Com/MA) **or** return a 400 that names the missing program classification
      (`Education_Level__c` / `Course__c`) — never a generic “Unable to identify…” after the
      document has already been uploaded.
- [ ] Add a route/contract test that pins these fields so a future change cannot silently remove
      the decision inputs.

**Gate 3 (shadow):**
- Upload the sample set (`Document samples-20261103T194256Z-1-001.zip`) and a set of live demo
  documents; for each, the stored evidence matches the PDF's actual mark.
- Shadow decisions agree with a human reading of the document on the threshold cases
  (70.0 vs 69.9; 3.2 vs 2.33; 1100 vs 1099; 6.5 vs 6.0; 72 vs 71).
- Wrong-type uploads are classified `block_wrong_document`, not `block_not_eligible`.
- Scanned/unreadable PDFs are `REVIEW_REQUIRED`, never a silent pass.
- No change in offers sent during the shadow run; every divergence is logged and explained.
- Upload success rate and p95 latency are recorded (OCR should stay under the 90–120 s timeout).

### Phase 4 — Enforce the marks gate, and put the evidence in the offer

`UD`:
- [ ] Add `OFFER_ELIGIBILITY_MODE=academic` (default) / `strict`; add
      `OFFER_ELIGIBILITY_SHADOW=false` at enforcement.
- [ ] `evaluate_offer_readiness` returns `decision`; the gate document is derived from the lead's
      program (Class 12 for Bachelor; UG transcript for Master/MBA). Keep name/email/phone/
      program checks; replace “at least one document” with “gate document present and decided”.
- [ ] `generate_and_send_offer` refuses unless `decision.action == "issue"` and returns the
      decision to every caller. `force=True` bypasses only the idempotency guard; add a separate,
      logged `override_eligibility` for staff.
- [ ] Web upload (`app/main.py:5152-5163`): issue only on `issue`; otherwise return
      `{decision, message}` and show it in the chat.
- [ ] WhatsApp “done” and document replies: issue only on `issue`; send `decision.message`
      otherwise. Do not create an `offer_letters` row for a blocked decision.
- [ ] Fix the idempotency guard (`app/offers/models.py:800-835`) before enforcing, so a double
      “done” cannot create two offers under the new gate.
- [ ] Store the decision on the offer row and render “Basis of Offer” in `app/offers/pdf.py`
      (§3.6). The PDF must show the extracted mark, the program requirement, and the validation
      date — never ID/passport data.
- [ ] Map the decision to `Admission_Status__c` per §3.8; never send `Offer_Status__c=Offered` for
      a blocked decision. Do not auto-send `Rejected` for below threshold without sign-off.
- [ ] Deduplicate identical re-uploads by `sha256`; keep the existing offer-document duplicate
      defence unchanged.

**Gate 4 (enforced):**
- `PASSED` gate document → exactly one offer row, one PDF, one CRM upload, `Offer_Status__c=Offered`,
  `Offer_Letter_Released__c=True`, and the PDF shows the mark/threshold.
- `BELOW_THRESHOLD` → zero offer rows, zero PDFs, `Offer_Status__c` untouched, student receives the
  mark-vs-requirement message.
- `REVIEW_REQUIRED`, `WRONG_DOCUMENT`, `MISSING`, `UNKNOWN` → zero offers and the matching message.
- Re-upload a better score after a below-threshold attempt → the next trigger issues an offer.
- Re-upload a worse scan after a pass → the previous pass stays active; no regression.
- Double “done” / duplicate webhook → one offer (idempotency).
- Full regression suite green; the existing four live gates still pass.

### Phase 5 — Cutover and hardening

- [ ] Stop the `:8098` process; point `start_services.ps1` `$CrmApiRoot` at the merged `NEW` repo
      (or set `SALESFORCE_API_ROOT`); keep the same port and health check.
- [ ] Keep the old repo and `.env` untouched for rollback.
- [ ] Set `OFFER_ELIGIBILITY_SHADOW=false` and `OFFER_ELIGIBILITY_MODE=academic` (or `strict` if
      the admissions team has signed off) in the live `.env`.
- [ ] Run a full demo scenario end to end: pass mark → offer with evidence; below-threshold mark →
      no offer; unreadable scan → review message; re-upload better mark → offer.
- [ ] Update `doc/salesforce/STATUS_AND_VALIDATION.md` and `STATUS_2026-09-13.md` with the new
      contract and the new offer rule; note `GET /users/lookup` removal and the new upload
      endpoints.
- [ ] Re-run all gates against `:8098`; monitor `offer_letters.crm_upload_status`, the outbox, and
      the shadow/enforced decision log for divergences.

**Gate 5:** all gates pass on `:8098`; no `failed`/`unknown` offer uploads for a full demo run; the
decision log shows no unexplained “issue” on a below-threshold mark or vice versa.

---

## 6. Salesforce org prerequisites — verified live 2026-11-04

Read-only `describe()` against the live org (same domain as the supplied credentials). **No records
were created or changed.**

| Object | Field | Actual state |
|---|---|---|
| Customer | `Document_Validation_Results__c` | ✅ exists — textarea, length 32,768, nillable |
| Customer | `Course_Eligibility__c` | ✅ exists — **restricted** picklist: `Not Evaluated`, `Eligible`, `Not Eligible` |
| Customer | `Program__c` | ❌ **MISSING** |
| Customer | `Education_Level__c` | ✅ exists — **restricted** picklist: `Diploma`, `Bachelor's`, `Master's` (no `MBA`) |
| Customer | `Course__c` | ✅ free text, 255 |
| Customer | `Admission_Status__c` | ✅ restricted: `Approved`, `Rejected`, `Cancelled`, `Documents Pending`, `Under Review` |
| Customer | `Offer_Status__c` | ✅ restricted: `Not Sent`, `Offered`, `Accepted`, `Declined`, `Expired` |
| Customer | `Offer_Letter_Released__c` | ✅ boolean, required |
| Customer | `Offer_Letter_Accepted__c` | ✅ restricted: `UNKNOWN`, `ACCEPTED`, `NOT_ACCEPTED` |
| ContentVersion | `Failed_Reason__c` | ✅ exists — textarea, length 1,000 |
| ContentVersion | `Rejection_Reason__c` | ✅ exists (legacy field) |
| ContentVersion | `Document_Type__c` | ✅ restricted: `Academic Transcript`, `Resume`, `Financial Document`, `ID Document`, `Admission Call Recording`, `Chatbot Conversation Record`, `Test Score`, `Recommendation Letter`, `Other`, `10th Marksheet`, `12th Marksheet`, `Diploma Marksheet`, `Bachelor's Last-Semester Result`, `IELTS`, `Diploma Last-Semester Result` — **no `Offer Letter`** |
| ContentVersion | `Source__c` | ✅ restricted: `Web`, `WhatsApp`, `Chatbot`, `Email`, `Internal Upload`, `Other` |
| ContentVersion | `Verification_Status__c` | ✅ restricted: `ELIGIBLE`, `NOT_ELIGIBLE`, `FAILED`, `REVIEW_REQUIRED` — **no `Pending`** |

**What this changes**

1. **`Program__c` does not exist.** Push `Education_Level__c` instead: `Bachelor's` for
   B.Tech/BBA/BCA/B.Com/BA/B.Sc, `Master's` for MCA/M.Tech/M.Sc/MA/M.Com. For MBA, the first
   branch in `_customer_program` is `Course__c == "MBA"`, so ensure `Course__c` is exactly `"MBA"`
   — otherwise a `Master's` education level classifies the student as `MASTER`, not `MBA`.
2. **Offer PDFs must be filed as `Other`.** There is no `Offer Letter` value. D2 uses the
   configurable offer-letter type, set to `Other` for this org.
3. **Do not write `Pending` to `Verification_Status__c`.** The field is a restricted picklist and
   has no `Pending`; the field is nillable. The offer-letter/raw endpoints must **omit**
   `Verification_Status__c` (or the org owner must add `Pending`). The old API's hard-coded
   `"Pending"` (`OLD/app/repositories/salesforce_document_repository.py:210-212`) is invalid
   against this org — this is a latent defect in the old offer upload, not just in the new build.
4. **Every picklist here is restricted.** Values must match exactly. The new code's mappings
   (`NEW/app/repositories/salesforce_document_repository.py:18-45`) all exist in the org; the
   offer-letter path is the only gap.
5. **The new vocabulary is already live.** Recent ContentVersion rows (2026-10-30) carry
   `Verification_Status__c` values `ELIGIBLE`, `REVIEW_REQUIRED`, `FAILED` and
   `Document_Type__c="12th Marksheet"`, `Source__c="Internal Upload"` — the new API has been
   exercised against this org.
6. `Document_Validation_Results__c` is exactly 32,768 chars, the code's `MAX_FIELD_LENGTH`
   (`NEW/...:6`). JSON escaping can push a near-limit history over; leave headroom and plan the
   child-object move.

---

## 7. Configuration changes

`NEW/.env` (never commit; `.env` is gitignored):

```ini
SALESFORCE_CLIENT_ID="..."
SALESFORCE_CLIENT_SECRET="..."
SALESFORCE_DOMAIN="https://<org>.my.salesforce.com"
# Only if auth is enabled:
# JWT_ALGORITHM=RS256
# JWT_ISSUER=...
# JWT_AUDIENCE=...
# JWT_PUBLIC_KEY="-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----"
```

`UD/.env` additions:

```ini
# Phase 1 shadow
CRM_BASE_URL=http://127.0.0.1:8099
CRM_UPLOAD_MODE=legacy
# Phase 2+
# CRM_UPLOAD_MODE=updated
# Phase 3 — compute the marks decision, do not enforce it yet
OFFER_ELIGIBILITY_SHADOW=true
# Phase 4 — enforce
# OFFER_ELIGIBILITY_SHADOW=false
# academic = gate on the program's academic document (recommended)
# strict   = all four categories must pass
OFFER_ELIGIBILITY_MODE=academic
# OCR uploads can exceed the 30 s default
CRM_UPLOAD_TIMEOUT_S=120
```

`start_services.ps1`:
- `$CrmApiRoot` default → the merged `NEW` repo (or keep `OLD` and set `SALESFORCE_API_ROOT`).
- `$CrmPython` must point at the new venv (PyMuPDF/OCR installed).
- Health check remains `GET /health` on `:8098`.

---

## 8. Test plan

**Static / import**
- Fresh venv from `NEW/requirements.txt`; `python -c "import main"` and `uvicorn main:app` start.
- `pip check`; confirm `fitz`, `cv2`, `numpy`, `pytesseract`, `PIL` import.

**Unit (NEW)**
- Fix `test_demo_rules.py` to assert `verification_status` / `eligibility_status`, then run it.
- Route contract tests with `TestClient` + mocked Salesforce: upload-and-verify accepts only PDFs
  ≤ 10 MB; rejects `Offer Letter`/`Other`; maps picklist values; `evaluate-and-save` is either
  removed or appends history.
- User API regression tests for R17 (blank name), R18 (null email read), SOQL injection, and
  not-found handling.

**Unit (UD)**
- Current baseline: 73 CRM/offer tests. After Phase 2, all must still pass with the new mocked
  paths.
- New: offer-letter endpoint call shape; validation response stored; unsupported type goes to the
  raw lane; image→PDF conversion.
- New: `assess_offer_eligibility` is pure and table-driven. Test every row of §3.3 with a stored
  validation row — no network, no DB.
- New: `generate_and_send_offer` refuses every non-`issue` decision and creates no `offer_letters`
  row; `force=True` still refuses; staff `override_eligibility` issues and logs.
- New: the offer PDF contains the extracted mark and threshold, and contains no passport/ID
  number.
- New: a below-threshold upload followed by a passing upload issues exactly one offer; the
  reverse order does not revoke the pass.
- New: identical bytes re-uploaded (`sha256`) do not create a second ContentVersion request.

**Marks decision matrix (enforced gate)**

| Case | Document | Expected |
|---|---|---|
| Bachelor pass | Class 12, 70.0% | offer issued; evidence 70.0 / 70 |
| Bachelor fail | Class 12, 69.9% | no offer; below-threshold message |
| Master pass | UG transcript, GPA 3.2/4.0 | offer issued |
| Master fail | UG transcript, GPA 2.33 | no offer |
| GPA scale unknown | UG transcript, no scale | `REVIEW_REQUIRED`; no offer |
| SAT pass/fail | 1100 / 1099 | pass/fail (strict mode) |
| IELTS pass/fail | 6.5 / 6.0 | pass/fail |
| TOEFL pass/fail | 72 / 71 | pass/fail |
| Wrong level | Class 12 uploaded for Master | `block_wrong_document` |
| Wrong type | Transcript uploaded as Class 12 | `block_wrong_document` |
| Missing gate | ID proof only | `block_missing` |
| Unreadable scan | no extractable text | `block_review` |
| API timeout after create | `unknown` state | `block_unknown`; no offer |
| Re-upload better | 62% then 75% | offer on the next trigger |
| Re-upload worse | 75% then 62% | previous pass remains active; offer still allowed |
| Double “done” | two triggers | one offer row (idempotency) |

**Live gates (against `:8099`, then `:8098`)**
- `scripts/verify_offer_document_upload.py`
- `scripts/verify_gate45_voice.py`
- `scripts/verify_gate6_webchat.py`
- `scripts/verify_gate7_status.py`
- New: `verify_upload_and_verify.py` using the sample PDFs in
  `Document samples-20261103T194256Z-1-001.zip` (GRE, TOEFL, SAT, GMAT, transcripts, marksheets,
  passports). Confirm the extracted score matches the sample and the resulting
  `Course_Eligibility__c` is correct.
- New: idempotency/duplicate test — two uploads of the same offer produce one `ContentVersion`.

**Regression / performance**
- Phone-only student links and reads back (R18).
- A CRM outage mid-upload leaves `crm_upload_status=failed`, never a duplicate.
- OCR latency on the largest sample (1.28 MB marksheet, 1.5 MB SAT) under the configured timeout.
- 10 MB cap and non-PDF rejection return 400, not 500.

---

## 9. Open questions

1. **Academic or strict gate?** The requirement says “based on the marks”, which maps to
   `OFFER_ELIGIBILITY_MODE=academic` (academic document only). Confirm that ID proof, entrance
   exam and English proficiency are advisory for the demo, or choose `strict` (all four
   categories).
2. **Which document is the gate for each program?** Class 12 for Bachelor; UG transcript for
   Master/MBA. Confirm BBA/BCA/B.Com/BA → Bachelor and MCA/M.Com/MA → Master, and what “marksheet”
   means in the current UI (it is filed as `10th Marksheet`, which the new API does not model).
   Note `Program__c` does not exist in the org; the plan pushes `Education_Level__c` instead.
3. **Is `REVIEW_REQUIRED` allowed to receive a conditional offer?** Recommendation: no auto-offer;
   route to manual review. A conditional offer would need an API change because `PROVISIONAL` is
   mapped to `REVIEW_REQUIRED` today.
4. **Below-threshold CRM status.** Recommendation: `Documents Pending` or `Under Review`, not
   `Rejected`, unless admissions explicitly wants an automatic rejection.
5. **Who creates the org fields/picklists in §6?** Without them, the new build cannot write
   validation results at all.
6. **May we modify `NEW`?** D2/D3 assume yes. If not, UD must be adapted to a contract that has
   no offer-letter path — which is not possible without either a raw endpoint or dropping offer
   PDF uploads.
7. **What about documents already uploaded?** They have no history rows; eligibility will ignore
   them. Backfill or re-upload for existing demo records.
8. **Image documents:** convert to PDF (adds Pillow/pdf2image or img2pdf) or keep them out of the
   validation lane? The new API accepts PDF only.
9. **Auth now or later?** If the API leaves localhost, enabling JWT requires a `PyJWT` dependency,
   a real `JWT_PUBLIC_KEY`, and a token source in UD.

---

## 10. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Deploying `NEW` as-is loses phone-only students (R18) | High | High | D3: port `f327a95` before cutover; regression tests |
| Offer uploads silently fail on cutover | High | Medium (offer still delivered, CRM copy missing) | D2 endpoint + Phase 1 legacy shim; keep `crm_upload_status` alerting |
| Missing OCR dependencies | High | High (every upload 500) | Pin deps; fresh venv; import smoke test in Gate 1 |
| Missing org fields/picklists | Medium | High | Phase 0 checklist; test with one sample upload before Phase 2 |
| Program detection rejects BBA/BCA/etc. | High | Medium | Push `Education_Level__c` (Program__c missing); ensure `Course__c="MBA"`; extend `_customer_program`; 400 with a clear message |
| OCR misreads a mark and issues/blocks the wrong decision | Medium | High | Shadow mode + evidence display; `REVIEW_REQUIRED` on low confidence; manual override; never trust a bare score without the extracted-document evidence |
| Wrong document-type mapping blocks a valid student | High in Phase 3 | Medium | Ask for type where ambiguous (`test_score`, `marksheet`); test matrix; `block_wrong_document` asks for the correct document rather than rejecting |
| `strict` mode starves offers (missing ID/exam/English) | High | Medium | Default `academic`; enable `strict` only after the demo document set is agreed |
| Validation latency makes the upload feel hung | Medium | Medium | 90–120 s timeout; “checking your document” state; async offer delivery option |
| History JSON exceeds 32,768 chars | Low now, certain later | Medium | Cap/archive history; move to child object |
| SOQL injection from f-string `find_user` | Medium | High | D3 `format_soql` fix + test |
| Duplicate ContentVersion after a retry | Medium | High (cannot delete via API) | Keep `is_create`/no-retry on create-shaped calls; duplicate check by file name; sha256 dedupe |
| Offer gating changes student experience | **Certain** | High | Shadow mode, then enforce; clear mark-vs-threshold messaging; staff override |
| Auth half-enabled breaks every call | Low | High | Keep routes commented; fix config before enabling |

---

## 11. Rollback

1. Stop the new process; restart `OLD` from `D:\project\salesforce\salesforce-admission-api` with
   its own venv and `.env` (unchanged).
2. Point `UD` `CRM_BASE_URL` back to `:8098`; set `CRM_UPLOAD_MODE=legacy`.
3. UD database changes are additive (`ADD COLUMN IF NOT EXISTS`), so no downgrade is required.
4. The offer path is best-effort: offers already delivered are unaffected; only CRM copies and
   status pushes during the outage need replay/backfill (`crm_sync_outbox`, `crm_upload_status`).

---

## Appendix A — Endpoint inventory

| Method | Path | `OLD` | `NEW` |
|---|---|---|---|
| GET | `/health` | ✅ | ✅ |
| POST | `/users/lookup-or-create` | ✅ | ✅ |
| GET | `/users/lookup` | ✅ (local branch) | ❌ |
| PATCH | `/users/{id}/status` | ✅ | ✅ |
| DELETE | `/users/{id}` | ✅ | ✅ |
| GET | `/admissions` | ✅ | ✅ |
| GET | `/admissions/{id}` | ✅ | ✅ |
| POST | `/admissions` | ✅ | ✅ |
| PATCH | `/admissions/{id}` | ✅ | ✅ |
| DELETE | `/admissions/{id}` | ✅ | ✅ |
| GET | `/admissions/{app}/documents` | ✅ | ✅ |
| GET | `/admissions/documents/{id}` | ✅ | ✅ |
| GET | `/admissions/documents/{id}/content` | ✅ | ✅ |
| PATCH | `/admissions/documents/{id}/verification` | ✅ (`rejection_reason`) | ✅ (`failed_reason`) |
| POST | `/admissions/{app}/documents/uploads` | ✅ | ❌ |
| POST | `/admissions/{app}/documents/uploads/{id}/chunks` | ✅ | ❌ |
| POST | `/admissions/{app}/documents/uploads/{id}/complete` | ✅ | ❌ |
| POST | `/admissions/{app}/documents/upload-and-verify` | ❌ | ✅ |
| POST | `/admissions/{app}/documents/evaluate-eligibility` | ❌ | ✅ |
| POST | `/admissions/{app}/documents/evaluate-and-save` | ❌ | ✅ but always 400 |
| POST | `/admissions/{app}/documents/offer-letter` | ❌ | **to add (D2)** |
| POST | `/admissions/{app}/documents/raw` | ❌ | **to add (D4)** |

## Appendix B — Document type mapping

| UD `doc_type` | API canonical type | Salesforce `Document_Type__c` | Lane |
|---|---|---|---|
| `transcript` / `academic_transcript` | `UNDERGRADUATE_TRANSCRIPT` | `Academic Transcript` | validate |
| `marksheet` | `CLASS_12_MARKSHEET` (confirm — UD currently files this as `10th Marksheet`, and the new API models no Class-10 type) | `12th Marksheet` | validate after confirmation |
| `id_proof` / `id` / `passport` | `ID_PROOF` | `ID Document` | validate |
| `ielts` | `IELTS` | `IELTS` | validate |
| `test_score` | needs student confirmation (SAT/GRE/GMAT/TOEFL) | `Test Score` | validate after choice |
| `resume` / `cv` | — | `Other` (raw lane) | raw |
| `financial` | — | `Other` (raw lane) | raw |
| `recommendation` | — | `Other` (raw lane) | raw |
| `other` | — | `Other` (raw lane) | raw |
| offer letter (generated) | — | `Other` (the org's restricted `Document_Type__c` has no `Offer Letter`) | offer-letter endpoint |

## Appendix C — File change map

| File | Change | Phase |
|---|---|---|
| `NEW/requirements.txt` | Add `PyMuPDF`, `opencv-python-headless`, `numpy`, `pytesseract`, `Pillow`; `PyJWT` only if auth | 1 |
| `NEW/app/routes/document_routes.py` | Offer-letter + raw endpoints; make upload/OCR run off the event loop; add `requirement_status` to the compact response | 1–3 |
| `NEW/app/services/document_service.py` | `store_document_without_validation`; `requirement_status` in the compact response; optional `required_categories`; remove dead `evaluate-and-save` | 1–3 |
| `NEW/app/services/eligibility_service.py` | Optional `required_categories` so `Course_Eligibility__c` matches the offer policy | 3 |
| `NEW/app/repositories/salesforce_admission_repository.py` | Pass through `required_categories` | 3 |
| `NEW/app/repositories/salesforce_document_repository.py` | Omit `Verification_Status__c` on non-validated stores; configurable offer-letter type (`Other`) | 1 |
| `NEW/app/services/admission_service.py` | Remove/fix dead `save_admission_document_evaluation` | 1 |
| `NEW/test_demo_rules.py` | Fix the `validation_status` key assertions | 1 |
| `NEW/app/repositories/salesforce_user_repository.py`, `schemas/user_schema.py`, `services/user_service.py`, `routes/user_routes.py` | Re-apply `f327a95` + `0fc5328` (R17/R18/SOQL/lookup) | 2 |
| `UD/app/crm/documents.py` | `_upload_offer_letter`, `_upload_student_document_raw`, `_upload_and_verify`; keep outcome model | 2–3 |
| `UD/app/crm/identity.py` | `api_document_type()`, `program_level()` | 3 |
| `UD/app/crm/status.py` | Add `Education_Level__c` to `WRITABLE_FIELDS`; admission-status mapping | 3–4 |
| `UD/app/crm/sync.py` | Push the program while linking | 3 |
| `UD/app/offers/eligibility.py` | New pure decision module | 3 |
| `UD/app/offers/service.py` | Readiness returns `decision`; refusal in `generate_and_send_offer` | 4 |
| `UD/app/offers/pdf.py` | “Basis of Offer” block with mark/threshold | 4 |
| `UD/app/offers/models.py`, `app/offers/schema.py` | Evidence columns, `sha256`; fix idempotency guard | 3–4 |
| `UD/app/main.py` | Gate the web-upload and “done” offer triggers; show the decision message | 4 |
| `UD/app/config.py` | `CRM_UPLOAD_MODE`, `OFFER_ELIGIBILITY_MODE`, `OFFER_ELIGIBILITY_SHADOW`, timeout | 1–4 |
| `UD/start_services.ps1` | Point at the merged `NEW` repo/venv | 5 |
| `UD/doc/salesforce/*` | Update status/contract docs | 5 |

## Appendix D — Evidence index

| Claim | Evidence |
|---|---|
| Live branch has fixes absent from upstream/new | `OLD` `git diff --stat origin/main..HEAD`; files in §2.1 |
| Chunked routes exist in `OLD` | `OLD/app/routes/document_routes.py:176-341`; `OLD/app/services/document_service.py:180,266,366` |
| Chunked routes removed in `NEW` | `NEW/app/routes/document_routes.py` (no `/uploads` routes) |
| `upload-and-verify` contract | `NEW/app/routes/document_routes.py:173-206`; `NEW/app/services/document_service.py:859-950` |
| Canonical types / thresholds | `NEW/app/core/admission_document_config.py:10-51` |
| Eligibility aggregation | `NEW/app/services/eligibility_service.py:118-367` |
| `offer_letter_authorized` always false | `NEW/app/services/eligibility_service.py:358` |
| History + eligibility write | `NEW/app/repositories/salesforce_admission_repository.py:42-80` |
| Missing dependencies | `NEW/requirements.txt`; imports at the top of the extractor modules |
| `evaluate-and-save` can never succeed | `NEW/app/services/document_service.py:1039-1044` vs `NEW/app/repositories/salesforce_admission_repository.py:63-64` |
| `test_demo_rules.py` key mismatch | `NEW/test_demo_rules.py:29-32` vs `NEW/app/services/document_validation_service.py:277-292` |
| Compact response omits `requirement_status` | `NEW/app/routes/document_routes.py:87-120` vs `NEW/app/services/document_service.py:940-950` |
| `upload-and-verify` blocks the event loop | `NEW/app/routes/document_routes.py:173-206` — `async def` calls the sync service inline |
| Current offer rule is “any document” | `UD/app/offers/service.py:93-95` |
| Marks thresholds | `NEW/app/core/admission_document_config.py:48-51`; `NEW/app/services/document_validation_service.py:166-226` |
| UD offer pipeline | `UD/app/offers/service.py:66-346`; `UD/app/crm/documents.py:260-450` |
| UD readiness = one document | `UD/app/offers/service.py:93-95` |
| UD document types | `UD/app/crm/identity.py:68-82,379-399` |
| UD CRM config | `UD/app/config.py:777-869`; `UD/.env:443-461` |
| Startup wiring | `UD/start_services.ps1:46-52,643-711` |
