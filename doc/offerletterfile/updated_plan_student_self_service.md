# Offer Letter — Student Self-Service & Multi-Channel Upload Plan

> **Status**: Implementation Plan | **Date**: 2026-08-08 | **Branch**: `DashboardImplemetation`

## Context

Currently only staff can upload documents via the Streamlit dashboard. The client wants students to self-serve — uploading documents from WhatsApp, Streamlit chat, or being prompted after a voice call. When a student expresses interest in admission, the chatbot should ask for required documents, and upon receiving them, auto-generate the offer letter AND send a payment link to close the lead as fast as possible. Document verification is out of scope for now (demo mode — will add human/AI verification later).

## ⚡ Backward Compatibility — Guaranteed No Breakage

Every change below is **additive** — nothing is removed or restructured. Here's exactly how each existing flow stays untouched:

### Existing Flow → Protected By

| Existing Feature | Protection Strategy |
|---|---|
| **WhatsApp voice notes** (audio/ogg) | Media detection checks `MediaContentType0`. Audio stays on the EXISTING code path. Only `image/*` and `application/pdf` go to the new document handler. |
| **WhatsApp name/email state machine** | The admission intent check runs **after** the existing state machine (name → email → RAG). If the user is still providing name/email, that takes priority. Admission intent only triggers once profile is complete. |
| **WhatsApp RAG Q&A** | Admission keywords are checked **before** RAG. If the user asks "how do I apply?", it hits the admission path instead. But if they ask "what is the tuition fee?" (no admission keywords), it falls through to RAG exactly as before. |
| **Staff dashboard document upload** | `POST /api/leads/{lead_id}/documents` — unchanged. The auto-trigger logic already exists and works. No modifications needed. |
| **Dashboard leads page** | UI additions (document list, offer history) are **inside the expander after a divider** — existing Call/Status/History/Schedule buttons untouched. |
| **Voice call pipeline** | `_detect_admission_intent()` is a NEW function — it does not modify `_detect_follow_up_intent()`. Post-call WhatsApp message is an ADDITIONAL action after the existing conversation log + follow-up detection. If it fails, existing flow continues. |
| **Offer letter auto-trigger** | `generate_and_send_offer()` already exists. New channels just call the same function. 24h idempotency guard prevents duplicates across ALL channels. |
| **Streamlit chat (`app.py`)** | File uploader is added in the **sidebar** — the chat interface, RAG chain, TTS, and voice input are untouched. If `lead_id` is not in session state, the chat works exactly as before (just without lead tracking). |

### Code Change Pattern

Every modification follows this safe pattern:

```python
# ✅ SAFE: Additive — new code path, existing path unchanged
if new_condition:        # Only triggers for NEW behavior
    handle_new_way()     # New function
else:
    existing_code()      # Original code, verbatim

# ❌ NOT USED: Destructive — we never do this
# Rewriting existing functions or changing signatures
```

### WhatsApp Webhook — Critical Safety Point

The `twilio_whatsapp_webhook` function is the **most sensitive**. Here's the exact before/after:

**Before (current):**
```
Media arrives → check if "audio" → transcribe
             → else → "Send me a question..." (falls through)
```

**After (new):**
```
Media arrives → check content_type:
    audio/* → transcribe (SAME AS BEFORE ✅)
    image/* → save as document → maybe trigger offer (NEW PATH, no overlap)
    application/pdf → save as document → maybe trigger offer (NEW PATH, no overlap)
    other → friendly message (SAME AS BEFORE ✅)
```

No code path that previously worked can now fail — `audio/*` still routes to the exact same `_process_voice_note_async()` function.

### Post-Call — Safety Point

**Before:** `handle_post_interaction()` → extract lead → detect follow-up → log conversation

**After:** Same as above → **plus** detect admission intent → send WhatsApp doc request (fire-and-forget, failure is logged and ignored)

If the WhatsApp send fails, the conversation is still logged, the lead is still updated, and the follow-up detection still works. No cascading failures.

### Dashboard API — Safety Point

The 10 new endpoints are added **before** the MCP section. No existing endpoint routes are changed. All existing `POST/GET /api/leads`, `/api/quick-call`, `/api/stats` etc. remain at their exact same paths with the exact same behavior.

---

## Additional Gaps Found During Exploration

### Gap 1: WhatsApp ACCEPT/DECLINE is a dead end (BUG)
The offer letter message tells students to "reply ACCEPT or DECLINE" (`app/offers/service.py:140`), but the WhatsApp webhook has **zero handling** for these replies. The student types "ACCEPT" and it falls through to RAG as an unknown question. **Fix**: Add ACCEPT/DECLINE keyword detection in the webhook text handler, before the RAG fallthrough.

### Gap 2: Voice call disconnects don't capture caller's phone number
`handle_post_interaction()` is called with `phone_number=""` in 4 WebSocket disconnect sites (`app/main.py:273, 281, 482, 630`). To send a WhatsApp follow-up after a call, we must thread the caller's `From` number through the WebSocket session. This requires a small refactor: store `caller_number` on the WebSocket state object during the Twilio voice webhook handshake.

### Gap 3: Only first media attachment is received
Twilio sends `NumMedia` (count) + `MediaUrl0..N` / `MediaContentType0..N`. Currently only `MediaUrl0` is declared — if a student sends 3 documents at once, only the 1st is seen. **Fix**: Add `NumMedia` support, loop through all attachments.

### Gap 4: `app.py` already has a lead collection state machine
The Streamlit chat (`app.py:313-349`) already collects name → email → phone and POSTs to `/api/leads`. We can hook into this existing flow rather than building from scratch.

---

## Channels to Support

| Channel | Current State | Target State |
|---|---|---|
| **Dashboard (staff)** | Already working ✅ | No changes needed |
| **WhatsApp** | Only voice notes handled. Document uploads ignored. | Detect admission intent → ask for documents → receive documents → auto offer letter + payment link |
| **Streamlit chat (`app.py`)** | Text + voice only. No file upload. | Add file uploader in sidebar. Detect interest → prompt documents → auto offer letter |
| **Voice calls** | Post-call follow-up detection exists. | After call, if interest detected → send WhatsApp with document upload request |

---

## 1. Database Changes

### 1.1 Add `payment_link` to `courses` table

```sql
ALTER TABLE courses ADD COLUMN IF NOT EXISTS payment_link VARCHAR(512) DEFAULT '';
```

Update `app/offers/schema.py` — add to CREATE_COURSES_TABLE. Since we use `CREATE TABLE IF NOT EXISTS`, we need a manual migration OR update the existing table. **Approach**: add an ALTER TABLE migration block that runs in `init_db()`.

### 1.2 Add `NumMedia` handling (no DB change — WhatsApp webhook enhancement)

Twilio sends `NumMedia` (integer) with each incoming message. Currently the webhook only receives `MediaUrl0` and `MediaContentType0`. Need to add `NumMedia: str = Form(default="0")` to the webhook signature.

---

## 2. WhatsApp Webhook Changes (`app/main.py`)

### 2.1 Fix Media Detection (critical bug fix)

**Current code** (line 1038):
```python
if not Body.strip() and MediaUrl0.strip():
    # Treats ALL media as voice notes!
```

**New logic**:
```python
num_media = int(NumMedia or "0")
if num_media > 0 and MediaUrl0.strip():
    content_type = MediaContentType0.lower()
    if "audio" in content_type:
        # Voice note — existing flow (transcribe + RAG)
        ...
    elif "image" in content_type or "pdf" in content_type or "document" in content_type:
        # Document upload from student!
        await _handle_whatsapp_document(lead, MediaUrl0, content_type, From, To)
    else:
        # Unknown media — ask them to send a document or audio
        answer = "I received your file but I can only accept images (transcripts, IDs) and PDFs. Please try again."
```

### 2.2 New: `_handle_whatsapp_document()` function

```python
async def _handle_whatsapp_document(lead, media_url, content_type, from_number, to_number):
    """Download WhatsApp media, save as document, check for offer trigger."""
    # 1. Download the file from Twilio media URL (like voice note download, but save raw)
    # 2. Determine file extension from content_type
    # 3. Save to data/documents/<lead_id>/<uuid8>_<filename>
    # 4. Insert lead_documents row
    # 5. If lead has program_interest → trigger generate_and_send_offer()
    # 6. Reply to student: "Received your document! Your offer letter is on the way."
```

### 2.3 Intent Detection: LLM-Based Semantic Matching (NOT exact keywords)

The user should NOT need to say an exact phrase like "I want admission." The LLM catches ALL semantic variations. We follow the same hybrid pattern already used by `_detect_follow_up_intent()` in `app/leads/service.py:243-317`.

**Architecture: Keyword fast-path → LLM semantic check**

```python
# app/leads/service.py — NEW function (mirrors _detect_follow_up_intent)

async def _detect_admission_intent(transcript: str) -> tuple[bool, str]:
    """
    Detect if the user wants to proceed with admission.
    
    Uses keyword fast-path first (free), then LLM for semantic matching.
    Returns (is_interested: bool, evidence: str).
    """
    lower = transcript.lower()
    
    # ── Fast path: strong keywords (no LLM needed) ──────────────
    strong_keywords = [
        "i want to take admission",
        "i want admission",
        "take addmission",          # common typo
        "i want to enroll",
        "ready to enroll",
        "sign me up",
    ]
    for kw in strong_keywords:
        if kw in lower:
            return True, kw
    
    # ── LLM path: catch ALL semantic variations ─────────────────
    # Any phrase that MEANS the user wants to proceed — the LLM understands:
    # "let's go ahead", "I'm ready", "proceed with enrollment",
    # "yes I want to apply", "let's do it", "I'd like to join",
    # "go for it", "confirm my admission", "I'm interested", etc.
    
    try:
        import ollama
        from app.config import settings
        
        response = ollama.chat(
            model="qwen2.5:7b-instruct-q3_K_M",
            messages=[{
                "role": "user",
                "content": (
                    "You are an intent classifier for a university admissions chatbot.\n"
                    "Determine if the user's message expresses that they are READY to "
                    "proceed with admission, want to enroll, want to apply, or want to "
                    "take a program.\n\n"
                    "This includes phrases like: 'i am ready to take admission', "
                    "'let's go ahead', 'i want to join', 'sign me up', 'yes apply now', "
                    "'proceed with enrollment', 'i'd like to study here', 'confirm my seat', "
                    "'let's do it', 'i'm interested in joining', etc.\n\n"
                    "Answer ONLY 'yes' or 'no'.\n\n"
                    f"User message:\n{transcript[-800:]}"
                ),
            }],
            options={"num_ctx": 1024},
        )
        raw = response["message"]["content"].strip().lower()
        if raw.startswith("yes"):
            return True, "llm_detected"
    except Exception:
        pass  # LLM unavailable → rely on keyword fast-path result
    
    return False, ""
```

**LLM catches all these variations (and more):**

| User says | Keyword match? | LLM catch? |
|---|---|---|
| "I want admission" | ✅ Strong keyword | ✅ (skipped, fast path) |
| "i am ready to take admission" | ❌ No exact match | ✅ LLM understands |
| "let's go ahead with the enrollment" | ❌ | ✅ LLM understands |
| "yes I'd like to apply for MBA" | ❌ | ✅ LLM understands |
| "go for it, sign me up" | ✅ "sign me up" | ✅ (fast path) |
| "I'm interested in joining this program" | ❌ | ✅ LLM understands |
| "please proceed with my application" | ❌ | ✅ LLM understands |
| "confirm my admission please" | ❌ | ✅ LLM understands |
| "What is the tuition fee?" | ❌ | ✅ LLM correctly says NO |
| "Can you tell me about the campus?" | ❌ | ✅ LLM correctly says NO |

**Where it runs:**
- **WhatsApp** (`app/main.py`): After the name/email state machine, before RAG fallthrough
- **Voice calls** (`app/leads/service.py::handle_post_interaction`): After conversation is saved
- **Streamlit** (`app.py`): After each user message, if lead has program_interest

### 2.4 WhatsApp Document Collection State

Track which step the user is in via a simple flag. Since we don't have session state in WhatsApp, use the lead's `notes` field or a simple approach:

- After asking for documents, store `status = "awaiting_documents"` on the lead
- When a document arrives, acknowledge it and ask for more or confirm completion
- When the user says "done" or "that's all" → finalize

### 2.5 Payment Link in WhatsApp Reply

After offer letter is sent, append payment link:
```
"Your offer letter is attached! 📎
To confirm your seat, please complete the payment:
💳 Payment Link: https://pay.university.edu/course/xyz
Amount: $500 (seat reservation fee)"
```

---

## 3. Streamlit Chat (`app.py`) Changes

### 3.1 Add Lead Creation in Chat

When a user sends their first message, auto-create (or upsert) a lead record in the background. Use session state to track the lead_id:

```python
# In the chat message handler:
if "lead_id" not in st.session_state:
    phone = st.session_state.get("user_phone", "streamlit_user")
    lead = upsert_lead_by_phone(phone_number=phone, source="streamlit")
    st.session_state["lead_id"] = lead["id"]
```

### 3.2 Add File Uploader in Sidebar

```python
with st.sidebar:
    st.markdown("---")
    st.markdown("### 📄 Upload Documents")
    uploaded_file = st.file_uploader(
        "Transcript, ID Proof, or Marksheet",
        type=["pdf", "png", "jpg", "jpeg"],
        key="doc_uploader"
    )
    doc_type = st.selectbox("Document type", ["transcript", "id_proof", "marksheet", "other"])
    
    if uploaded_file and st.button("Upload Document"):
        result = api_upload_lead_document(
            st.session_state.get("lead_id"),
            uploaded_file, doc_type
        )
        if result:
            st.success("Document uploaded! Processing your admission...")
        else:
            st.error("Upload failed. Please try again.")
```

### 3.3 Quick Interest Button

Add a prominent button when the chatbot detects the user is interested:
```python
st.button("🎓 I want to take admission!", on_click=handle_admission_interest)
```

---

## 4. Voice Call Post-Interaction Changes (`app/leads/service.py`)

### 4.1 Add Admission Interest Detection

Uses the same `_detect_admission_intent()` LLM-based function described in §2.3 above. The voice transcript (potentially much longer than a WhatsApp message) is passed to the LLM with the same semantic intent prompt. For voice calls we send the last 1000 chars of the transcript to stay within context limits.

### 4.2 Thread Caller Phone Number Through WebSocket

### 4.2 In `handle_post_interaction()`:

After saving the conversation, if admission intent detected:
1. Update lead `status = "in_progress"`
2. Send a WhatsApp follow-up asking for documents (via `app/messaging.send_whatsapp_message`)
3. Log a follow-up with type `"message"`

```python
if await _detect_admission_intent(transcript):
    from app.messaging import send_whatsapp_message
    from app.config import settings
    
    lead = await get_lead_by_phone(phone_number)
    program = lead.get("program_interest", "the program") if lead else "the program"
    
    send_whatsapp_message(
        to_number=f"whatsapp:{phone_number}",
        from_number=f"whatsapp:{settings.TWILIO_PHONE_NUMBER}",
        body=f"Thanks for your interest in {program}! To proceed with admission, please upload:\n"
             f"📄 Your transcript/mark sheet\n🆔 ID proof\n\n"
             f"Just send photos or PDFs here, and I'll process your application right away!"
    )
```

---

## 5. Payment Link Integration

### 5.1 Add to Courses Table

Migration SQL (run in `init_db()`):
```sql
ALTER TABLE courses ADD COLUMN IF NOT EXISTS payment_link VARCHAR(512) DEFAULT '';
```

### 5.2 Configurable Payment Link

In `.env`: `DEFAULT_PAYMENT_LINK=https://pay.example.com/admissions`

In `app/config.py`: `DEFAULT_PAYMENT_LINK: str = field(...)`

### 5.3 Include in Offer Letter

In `app/offers/pdf.py` — add a "Payment" section with the link.

In WhatsApp message body — append payment link after the offer letter.

In email body — append payment link.

### 5.4 Update Models + API

- `update_course()` — allow `payment_link` field
- `GET/POST/PUT /api/courses` — include payment_link
- `POST /api/leads/{lead_id}/offer-letters/generate` — include payment_link in response

---

## 6. Files to Create/Modify

### New Files
| File | Purpose |
|---|---|
| None needed — all changes are modifications to existing files |

### Modified Files

| File | Change Summary |
|---|---|
| `app/main.py` | Fix WhatsApp media detection (voice vs document). Add `_handle_whatsapp_document()`. Add admission intent keywords. Add NumMedia param. |
| `app/offers/schema.py` | Add `payment_link` column to CREATE_COURSES_TABLE. Add migration ALTER TABLE block. |
| `app/offers/models.py` | Add `payment_link` to `update_course()`, `create_course()`, `get_course()`. |
| `app/offers/pdf.py` | Add payment link section to PDF template. |
| `app/offers/service.py` | Include payment link in WhatsApp body + email body. |
| `app/leads/service.py` | Add `_detect_admission_intent()`. Send doc-request WhatsApp after voice calls. |
| `app/config.py` | Add `DEFAULT_PAYMENT_LINK` setting. |
| `app.py` (Streamlit) | Add file uploader in sidebar. Add lead creation on first message. Add "I want to apply" button. |
| `app/dashboard/courses_page.py` | Add payment_link field to course form. |

---

## 7. Implementation Order

| Step | What | File(s) | Priority |
|---|---|---|---|
| 1 | Add `payment_link` column + migration | `app/offers/schema.py`, `app/database.py` | P1 |
| 2 | Add `DEFAULT_PAYMENT_LINK` config | `app/config.py` | P1 |
| 3 | Update models for payment_link | `app/offers/models.py` | P1 |
| 4 | Fix WhatsApp media detection: check `MediaContentType0`, distinguish audio vs doc/image | `app/main.py` (webhook) | **P0** — fixes existing bug |
| 5 | Add `NumMedia` + multi-attachment support | `app/main.py` (webhook) | P1 |
| 6 | Add `_handle_whatsapp_document()` download + save | `app/main.py` | P1 |
| 7 | Add admission intent detection (WhatsApp text) | `app/main.py` (webhook) | P1 |
| 8 | Add ACCEPT/DECLINE handling in WhatsApp | `app/main.py` (webhook) | P1 — fixes dead-end |
| 9 | Thread caller phone number through WebSocket handlers | `app/main.py` (4 disconnect sites) | P2 — enables voice follow-up |
| 10 | Add admission intent detection (voice calls) + WhatsApp doc request | `app/leads/service.py` | P2 |
| 11 | Update PDF template with payment link section | `app/offers/pdf.py` | P1 |
| 12 | Update service to include payment link in WhatsApp + email | `app/offers/service.py` | P1 |
| 13 | Add file uploader to Streamlit chat sidebar | `app.py` | P2 |
| 14 | Hook into existing lead collection in Streamlit chat | `app.py` | P2 |
| 15 | Update courses dashboard page with payment_link field | `app/dashboard/courses_page.py` | P2 |
| 16 | Test end-to-end across all 3 channels | Manual testing | P0 |

---

## 8. Verification

### WhatsApp Flow
1. Send "I want to take admission in Computer Science" → bot asks for documents
2. Send a PDF/image → bot acknowledges, saves, triggers offer letter
3. Receive offer letter PDF + payment link
4. Send second document → no duplicate offer (24h guard)

### Streamlit Chat Flow
1. Open `app.py`, type "I'm interested in MBA"
2. Upload document via sidebar → offer letter generated
3. Check dashboard → lead created, document stored, offer letter sent

### Voice Call Flow
1. Call the Twilio number, say "I want to enroll in Data Science"
2. Hang up → within 30s the scheduler should trigger
3. Receive WhatsApp message asking for documents
4. Reply with documents → offer letter + payment link

### Payment Link
1. Open generated PDF → payment link section visible
2. Check WhatsApp message → payment link included
3. Check email → payment link included

---

## 9. Future: Verification System (Not in Scope)

When ready to add document verification:
1. Add `verification_status` column to `lead_documents` (pending/verified/rejected)
2. Add `verified_by` column (human user ID or "ai_system")
3. Add verification dashboard page for staff to review documents
4. Offer letter auto-trigger changes from "on any upload" → "on all documents verified"
5. Could integrate with an AI document verification API (e.g., for transcript authenticity)
