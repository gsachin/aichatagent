# Offer Letter — Gap Analysis: Implemented vs Expected

> **Date**: 2026-08-09 | **Branch**: `offerlaterupdate`

## Summary

| Channel | Upload Works? | Offer Triggers? | PDF Link Shown? | Uploader UX |
|---|---|---|---|---|
| Streamlit Chat (in-chat) | ✅ Yes | ✅ Yes | ✅ Yes (after fix) | ⚠️ See Gap 1, 2 |
| Streamlit Chat (sidebar) | ✅ Yes | ✅ Yes | ✅ Yes (after fix) | ⚠️ See Gap 3 |
| WhatsApp | ✅ Yes | ✅ Yes | ✅ (PDF sent as attachment) | ✅ Native |
| Dashboard (staff) | ✅ Yes | ✅ Yes | ✅ (View PDF button) | ✅ Good |

---

## Gap 1: Sidebar upload doesn't clear `show_apply_prompt` state

**File**: `app.py` line 108
**Severity**: Medium

After sidebar upload succeeds, line 108 only sets `offer_generated = True`. It does NOT set:
- `show_apply_prompt = False`
- `awaiting_field = None`

**Impact**: The in-chat uploader keeps appearing below the chat even after a successful upload via sidebar. User sees the upload widget again and may try to upload twice.

**Fix**: Add `st.session_state["show_apply_prompt"] = False` and `st.session_state["awaiting_field"] = None` after line 108.

---

## Gap 2: Sidebar upload missing balloons and celebration

**File**: `app.py` line 106-108
**Severity**: Low (UX)

The sidebar upload shows `st.success()` and PDF link but doesn't show `st.balloons()` like the in-chat uploader does (line 717).

**Fix**: Add `st.balloons()` after line 107.

---

## Gap 3: "yes" handler ambiguity

**File**: `app.py` lines 517-536
**Severity**: Low

When `awaiting == "qualification"` and user types "yes":
- Line 517 `elif awaiting == "qualification"` matches first ✅
- Sets `awaiting_docs` and shows document request

But if `awaiting` is NOT "qualification" and user types "yes":
- Line 536 `elif msg_lower in ("yes", ...)` matches
- Shows generic "confirmed" message
- Does NOT ask about admission

The flow works correctly because qualification check comes first. But if user has `lead_program` set and types "yes" without being in qualification state, they get a generic message instead of being routed to admission.

**Fix**: After line 536, check if `lead_program` is set and guide user toward admission intent.

---

## Gap 4: WhatsApp — admission intent detection text is generic

**File**: `app/main.py` line ~1120-1150 (webhook)
**Severity**: Medium

When WhatsApp user says "I want to take admission", the bot asks for documents IMMEDIATELY — it skips the qualification check that Streamlit has. WhatsApp flow:
1. User: "I want to take admission in MBA"
2. Bot: "Upload documents" ← no qualification check

Streamlit flow:
1. User: "I want to take admission in MBA"  
2. Bot: Shows requirements → "Do you meet these?"
3. User: "yes" → "Upload documents"

These are inconsistent. WhatsApp should also confirm qualifications.

**Fix**: Add qualification confirmation step to `_detect_admission_intent_whatsapp()` flow in main.py.

---

## Gap 5: No "program interest" auto-detection in WhatsApp state machine

**File**: `app/main.py` line ~1076-1130
**Severity**: Medium

When WhatsApp user says "I want to take admission in Computer Science":
- The state machine treats it as a regular message
- Falls through to RAG
- RAG answers with program details from knowledge base

The `_detect_admission_intent_whatsapp()` function IS called, but it only returns boolean. It doesn't extract the program from the message.

**Fix**: Extract program name from admission intent messages using regex/keyword matching (like `app.py`'s `extract_program()` helper).

---

## Gap 6: Session state persistence across Streamlit reruns

**File**: `app.py` 
**Severity**: Low

Streamlit session state variables (`lead_id`, `lead_program`, `show_apply_prompt`) persist across browser refreshes but NOT across browser restarts or new tabs. If user closes the browser and comes back:
- `lead_id` is lost
- Upload will try to create a new lead with fallback
- Previous conversations are lost

**Fix**: Could store `lead_id` in URL params or browser localStorage via Streamlit components.

---

## Gap 7: In-chat uploader `st.rerun()` after success

**File**: `app.py` line 721
**Severity**: Medium

After in-chat upload succeeds and offer is generated, line 721 calls `st.rerun()`. This is at the TOP LEVEL (not inside chat_input), so it's safe. However:
- It causes the chat message about the upload to disappear immediately
- The success toast and balloons flash briefly before page re-renders
- User may not see the PDF download link in time

**Fix**: Remove `st.rerun()` and let the page stay with the success message + PDF link visible. The uploader is already hidden because `show_apply_prompt` was set to False.

---

## Gap 8: No "program_interest" update when user uploads without program set

**File**: `app/main.py` POST /api/leads/{lead_id}/documents
**Severity**: Low

The auto-trigger in the backend checks `lead.program_interest` before generating offer. If program_interest is empty, no offer is generated. The upload succeeds but silently returns without an offer. The user sees "Document uploaded but no offer generated" with no guidance on what to do next.

**Fix**: When `program_interest` is empty, return a clear message: "Document saved. Please set your program interest to trigger an offer letter."

---

## Gap 9: Email never works (SMTP not configured)

**File**: `app/emailer.py`
**Severity**: Low (demo)

`SMTP_USER` and `SMTP_PASS` are empty. Email sending is silently skipped. This is expected for demo but should be documented.

---

## Working Features (Verified)

| Feature | Status |
|---|---|
| Backend API: 10 endpoints | ✅ All working |
| Database: 3 new tables | ✅ Created on startup |
| PDF generation (fpdf2) | ✅ Branded A4, 2 pages |
| Course matching (ILIKE) | ✅ Auto-matches |
| 24h idempotency guard | ✅ Prevents duplicates |
| Accept/Decline status update | ✅ Via REST API |
| WhatsApp document handling | ✅ Media type detection |
| WhatsApp ACCEPT/DECLINE | ✅ Reply handling |
| Dashboard lead detail UI | ✅ Upload + offer history |
| Dashboard courses page | ✅ CRUD + payment_link |
| Payment link in PDF/WhatsApp/Email | ✅ All channels |
| Twilio error formatting | ✅ Friendly messages |
| LLM admission intent (keyword + Ollama) | ✅ Semantic matching |
