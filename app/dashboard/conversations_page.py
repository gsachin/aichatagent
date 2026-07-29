"""
Dashboard Conversations page — transcript viewer with search and export.
"""

from __future__ import annotations

import streamlit as st

from app.dashboard.pages import api_call


def render():
    st.title("💬 Conversations")
    st.markdown("---")

    # ── Filters ──────────────────────────────────────────────────
    col_f1, col_f2 = st.columns([2, 1])

    with col_f1:
        search_text = st.text_input(
            "Search transcripts", placeholder="Type to search conversation content..."
        )
    with col_f2:
        channel_filter = st.selectbox(
            "Channel", ["All", "whatsapp", "streamlit", "inbound_call", "outbound_call"]
        )

    st.markdown("---")

    # Build API params
    path = "/api/conversations?limit=50"
    if channel_filter != "All":
        path += f"&channel={channel_filter}"

    conversations = api_call("GET", path) or []

    # Client-side search
    if search_text:
        conversations = [
            c
            for c in conversations
            if search_text.lower() in (c.get("transcript", "") or "").lower()
            or search_text.lower() in (c.get("phone_number", "") or "").lower()
        ]

    if not conversations:
        st.info("No conversations found.")
        return

    st.caption(f"Showing {len(conversations)} conversation(s)")

    for conv in conversations:
        conv_id = conv.get("id", "")[:8]
        channel = conv.get("channel", "unknown")
        phone = conv.get("phone_number", "N/A")
        created = (conv.get("created_at", "") or "")[:19]
        transcript = conv.get("transcript", "") or ""
        outcome = conv.get("outcome", "")
        duration = conv.get("call_duration_seconds", 0)

        channel_emoji = {
            "whatsapp": "💬",
            "streamlit": "🌐",
            "inbound_call": "📞",
            "outbound_call": "📤",
        }.get(channel, "📝")

        title = f"{channel_emoji} [{channel}] {phone} — {created} ({len(transcript)} chars)"

        with st.expander(title):
            col1, col2 = st.columns([2, 1])

            with col1:
                st.text_area(
                    "Transcript",
                    transcript,
                    height=200,
                    key=f"transcript_{conv_id}",
                    disabled=True,
                )
                if st.button("📋 Copy Transcript", key=f"copy_{conv_id}"):
                    st.code(transcript, language=None)

            with col2:
                st.write(f"**Channel:** {channel}")
                st.write(f"**Duration:** {duration}s")
                st.write(f"**Outcome:** {outcome or 'N/A'}")
                st.write(f"**Follow-up needed:** {conv.get('follow_up_needed', False)}")
                if conv.get("follow_up_reason"):
                    st.write(f"**Reason:** {conv['follow_up_reason']}")

                # Extracted lead data
                extracted = conv.get("extracted_lead", {})
                if extracted and isinstance(extracted, dict) and any(extracted.values()):
                    st.write("**Extracted Lead Data:**")
                    st.json(extracted)
