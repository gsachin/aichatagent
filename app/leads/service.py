"""
Business-logic layer for lead management.

Wraps the raw CRUD in app.leads.models with higher-level operations:
- Auto-creating / updating leads when a new interaction arrives
- Logging conversations from every channel
- Extracting lead data via LLM and merging it into the lead record
- Detecting follow-up intent in transcripts
- Unified post-call / post-message handler
"""

from __future__ import annotations

import logging

logger = logging.getLogger("leads.service")


# ── Channel-aware lead upsert ────────────────────────────────────────


async def ensure_lead_exists(
    phone_number: str,
    source: str = "whatsapp",
) -> dict | None:
    """
    Called on every incoming interaction to make sure a lead record exists.

    Returns the lead dict (existing or newly created).
    """
    from app.leads.models import upsert_lead_by_phone

    return await upsert_lead_by_phone(
        phone_number=phone_number,
        source=source,
    )


# ── Conversation logging helper ──────────────────────────────────────


async def log_interaction(
    phone_number: str,
    channel: str,
    transcript: str,
    extracted_lead: dict | None = None,
    follow_up_needed: bool = False,
    follow_up_reason: str = "",
    outcome: str = "",
    call_duration_seconds: int = 0,
) -> dict | None:
    """
    High-level helper:
    1. Ensure a lead record exists (upsert by phone)
    2. Log the conversation under that lead
    3. If extracted_lead has data, fill in blank lead fields
    4. If follow-up is needed, create a follow_up entry

    Returns the conversation dict.
    """
    from app.leads.models import (
        create_conversation,
        update_lead,
        upsert_lead_by_phone,
    )

    # 1. Upsert lead
    lead = await upsert_lead_by_phone(
        phone_number=phone_number,
        name=(extracted_lead or {}).get("name", ""),
        email=(extracted_lead or {}).get("email", ""),
        program_interest=(extracted_lead or {}).get("program", ""),
        source=channel,
    )
    if not lead:
        logger.warning(f"Could not upsert lead for {phone_number} — conversation not logged")
        return None

    lead_id = lead["id"]

    # 2. Save conversation
    conv = await create_conversation(
        lead_id=lead_id,
        phone_number=phone_number,
        channel=channel,
        transcript=transcript,
        outcome=outcome,
        call_duration_seconds=call_duration_seconds,
        follow_up_needed=follow_up_needed,
        follow_up_reason=follow_up_reason,
        extracted_lead=extracted_lead,
    )

    # 3. If we have extracted data and the lead is missing info, patch it
    if extracted_lead:
        patch = {}
        if not lead.get("name") and extracted_lead.get("name"):
            patch["name"] = extracted_lead["name"]
        if not lead.get("email") and extracted_lead.get("email"):
            patch["email"] = extracted_lead["email"]
        if not lead.get("program_interest") and extracted_lead.get("program"):
            patch["program_interest"] = extracted_lead["program"]
        if patch:
            await update_lead(lead_id, **patch)

    # 4. If follow-up is needed, schedule it
    if follow_up_needed and follow_up_reason:
        await _auto_schedule_follow_up(lead_id, follow_up_reason)

    return conv


async def _auto_schedule_follow_up(lead_id: str, reason: str):
    """
    Analyse the follow-up reason with the LLM and schedule a follow-up.

    This is intentionally conservative — if the LLM can't parse a date
    we default to 24 hours from now so nothing falls through the cracks.
    """
    import json as _json
    import re
    from datetime import datetime, timedelta, timezone

    from app.leads.models import schedule_follow_up

    # Try to parse a date from the reason using the LLM
    try:
        import ollama
        import urllib.request

        # Find available model
        try:
            req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = _json.loads(resp.read())
                models = [m.get("name", "") for m in data.get("models", [])]
            qwen_models = [m for m in models if "qwen" in m.lower()]
            model = qwen_models[0] if qwen_models else "qwen2.5:7b"
        except Exception:
            model = "qwen2.5:7b"

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        response = ollama.chat(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Today is {now}. "
                        f"A student said: \"{reason}\". "
                        f"Extract the follow-up date and time as an ISO 8601 string. "
                        f"If the time is vague (e.g. 'next week', 'tomorrow morning'), "
                        f"pick a reasonable time. If you cannot determine a time at all, "
                        f"return the ISO time exactly 24 hours from now. "
                        f"Return ONLY the ISO 8601 string, nothing else."
                    ),
                }
            ],
            options={"num_ctx": 512},
        )
        raw = response["message"]["content"].strip()
        # Extract ISO-ish string
        match = re.search(
            r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?", raw
        )
        if match:
            scheduled_at = match.group(0).replace(" ", "T")
        else:
            raise ValueError("No date found in LLM response")
    except Exception:
        logger.exception("Could not parse follow-up date from LLM — defaulting to +24h")
        scheduled_at = (
            datetime.now(timezone.utc) + timedelta(hours=24)
        ).isoformat()

    await schedule_follow_up(
        lead_id=lead_id,
        scheduled_at=scheduled_at,
        type="call",
        notes=reason,
    )
    logger.info(f"Auto-scheduled follow-up for lead {lead_id} at {scheduled_at}")


# ── Post-call / post-message handler ─────────────────────────────────


async def handle_post_interaction(
    phone_number: str,
    transcript: str,
    channel: str = "whatsapp",
    call_duration_seconds: int = 0,
) -> bool:
    """
    Unified handler called after ANY interaction completes.

    1. Extract lead data from transcript via LLM
    2. Log the conversation
    3. Update the lead record

    Replace calls to app.database.handle_post_call() with this for
    any channel where you want the new tables populated.

    Returns True if the conversation was saved successfully.
    """
    if not transcript or not transcript.strip():
        logger.info("Post-interaction: empty transcript — nothing to save")
        return False

    logger.info(
        f"Post-interaction: channel={channel}, phone={phone_number}, "
        f"transcript_len={len(transcript)}"
    )

    # 1. Extract lead data via LLM (reuse existing function)
    extracted = None
    try:
        from app.database import extract_lead_from_transcript

        extracted = await extract_lead_from_transcript(transcript)
        if extracted:
            logger.info(f"Post-interaction: extracted lead — {extracted}")
    except Exception:
        logger.exception("Lead extraction failed — proceeding without extracted data")

    # 2. Detect follow-up intent via simple keyword + LLM check
    follow_up_needed, follow_up_reason = await _detect_follow_up_intent(transcript)

    # 3. Log to new tables
    conv = await log_interaction(
        phone_number=phone_number,
        channel=channel,
        transcript=transcript,
        extracted_lead=extracted,
        follow_up_needed=follow_up_needed,
        follow_up_reason=follow_up_reason,
        call_duration_seconds=call_duration_seconds,
    )

    return conv is not None


async def _detect_follow_up_intent(transcript: str) -> tuple[bool, str]:
    """
    Quick check: does the transcript contain follow-up intent?

    Uses keyword matching first (fast path), then falls back to LLM
    for ambiguous cases.

    Returns (follow_up_needed: bool, reason: str).
    """
    lower = transcript.lower()

    # Fast path: strong keywords
    strong_keywords = [
        "call me back",
        "call back",
        "call again",
        "talk to you later",
        "speak later",
        "schedule a call",
        "set up a call",
        "follow up",
        "follow-up",
        "catch up later",
        "another time",
    ]

    for kw in strong_keywords:
        if kw in lower:
            return True, kw

    # Medium path: weaker keywords — confirm with LLM
    weak_keywords = ["next week", "tomorrow", "later", "another day", "not now"]
    for kw in weak_keywords:
        if kw in lower:
            # Use LLM to confirm
            try:
                import json as _json
                import urllib.request

                try:
                    req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        data = _json.loads(resp.read())
                        models = [m.get("name", "") for m in data.get("models", [])]
                    qwen_models = [m for m in models if "qwen" in m.lower()]
                    model = qwen_models[0] if qwen_models else "qwen2.5:7b"
                except Exception:
                    model = "qwen2.5:7b"

                import ollama

                response = ollama.chat(
                    model=model,
                    messages=[
                        {
                            "role": "user",
                            "content": (
                                "Does the following conversation transcript contain "
                                "a request or intent to follow up later, schedule "
                                "another call, or talk again? "
                                "Answer ONLY 'yes' or 'no'.\n\n"
                                f"Transcript:\n{transcript[-1000:]}"
                            ),
                        }
                    ],
                    options={"num_ctx": 2048},
                )
                raw = response["message"]["content"].strip().lower()
                if raw.startswith("yes"):
                    return True, kw
            except Exception:
                pass
            break

    return False, ""
