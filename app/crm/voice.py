"""
Linking a live phone call to the CRM as soon as the caller's name is known.

A call has three things the other channels do not: the caller's number arrives
before anyone speaks (via a `<Parameter>` on the TwiML `<Stream>`), the name
arrives *during* the call, and the whole thing is on a latency budget. So the
link cannot be a single call at a fixed moment the way it is for WhatsApp.

Instead each call owns a :class:`CallLinker`, and the turn loop offers it the
transcript after every exchange. It declines until a name appears, gives up
after a few tries, and stops for good once it has a userId.

Why the cap matters: answering "is there a name yet?" means running the same
LLM extraction the post-call path uses, and a long call would otherwise pay for
it on every turn. Three attempts covers the realistic case — the voice agent
asks for a name early, so the first or second attempt lands — and the post-call
handler still runs its own extraction, so nothing is lost if all three miss.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("crm.voice")

# Attempts per call. Each one is an LLM extraction; the caller's name normally
# arrives in the first exchange or two.
MAX_ATTEMPTS = 3


class CallLinker:
    """
    Tracks one call's progress towards a CRM link.

    Deliberately holds no state about the transcript — the caller passes the
    text in, because the WebSocket handler already accumulates it and a second
    copy here would be one more thing to keep in step.
    """

    def __init__(
        self,
        *,
        channel: str,
        conversation_id: str,
        phone_number: str,
        session_key: str,
        lead_id: str = "",
    ) -> None:
        self.channel = channel
        self.conversation_id = conversation_id
        self.phone_number = phone_number
        self.session_key = session_key
        self.lead_id = lead_id
        self.crm_user_id = ""
        self.attempts = 0

    @property
    def settled(self) -> bool:
        """Linked, or out of tries — no further work is worth scheduling."""
        return bool(self.crm_user_id) or self.attempts >= MAX_ATTEMPTS

    @property
    def can_try(self) -> bool:
        """
        Worth an attempt now?

        A call with no caller number cannot be linked at all — there would be
        nothing to match on but a name the CRM cannot search by. Saying so once
        is useful; saying it every turn is noise.
        """
        return not self.settled and bool(self.phone_number)

    async def attempt(self, transcript: str) -> str:
        """
        Try once to link this call. Returns the userId, or "" if not linked yet.

        Never raises: it runs in the background of a live call, where the only
        acceptable failure mode is a log line.
        """
        if not self.can_try:
            return self.crm_user_id

        text = (transcript or "").strip()
        if not text:
            return ""

        self.attempts += 1
        try:
            from app.database import extract_lead_from_transcript

            extracted = await extract_lead_from_transcript(text) or {}
        except Exception:
            logger.exception(f"crm.voice: lead extraction failed on attempt {self.attempts}")
            return ""

        name = str(extracted.get("name") or "").strip()
        if not name:
            # Normal early on: the caller has not given a name yet. The next
            # turn tries again.
            logger.debug(
                f"crm.voice: no name yet on attempt {self.attempts} — will retry"
            )
            return ""

        try:
            from app.crm.sync import link_conversation

            user_id = await link_conversation(
                channel=self.channel,
                conversation_id=self.conversation_id,
                phone_number=self.phone_number,
                email=extracted.get("email") or "",
                name=name,
                course=extracted.get("program") or "",
                session_key=self.session_key,
                # No lead_id: mid-call there is no lead row yet. The link is
                # parked on the live session, and the disconnect handler reads it
                # back from there and persists it against the lead.
            )
        except Exception:
            logger.exception("crm.voice: CRM link failed (non-fatal)")
            return ""

        if user_id:
            self.crm_user_id = user_id
            logger.info(
                f"crm.voice: call {self.session_key} linked to {user_id} "
                f"on attempt {self.attempts}"
            )
            return user_id

        logger.info(
            f"crm.voice: call {self.session_key} not linked on attempt "
            f"{self.attempts} (attempt {self.attempts}/{MAX_ATTEMPTS})"
        )
        return ""
