"""
Dashboard Leads page — manage leads with filters, forms, and actions.
"""

from __future__ import annotations

import streamlit as st

from app.dashboard.pages import api_call


def render():
    st.title("👥 Lead Management")
    st.markdown("---")

    # ── Filters ──────────────────────────────────────────────────
    col_f1, col_f2, col_f3 = st.columns([2, 2, 1])

    with col_f1:
        status_filter = st.selectbox(
            "Filter by Status",
            ["All", "pending", "in_progress", "completed", "failed", "unreachable"],
        )
    with col_f2:
        search_phone = st.text_input("Search by Phone", placeholder="+1234567890")
    with col_f3:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🔄 Refresh", use_container_width=True):
            st.rerun()

    # ── Add lead form ────────────────────────────────────────────
    with st.expander("➕ Add New Lead", expanded=False):
        col_a, col_b = st.columns(2)
        with col_a:
            new_phone = st.text_input("Phone Number *", placeholder="+1234567890")
            new_name = st.text_input("Name", placeholder="John Doe")
            new_email = st.text_input("Email", placeholder="john@example.com")
        with col_b:
            new_program = st.text_input("Program Interest", placeholder="Computer Science")
            new_source = st.selectbox("Source", ["manual", "whatsapp", "streamlit", "inbound_call"])
            new_notes = st.text_area("Notes")

        if st.button("Add Lead", type="primary"):
            if new_phone:
                result = api_call(
                    "POST",
                    "/api/leads",
                    {
                        "phone_number": new_phone,
                        "name": new_name,
                        "email": new_email,
                        "program_interest": new_program,
                        "source": new_source,
                        "notes": new_notes,
                    },
                )
                if result and "error" not in result:
                    st.success(f"Lead added: {result.get('id', 'N/A')}")
                    st.rerun()
                else:
                    st.error(result.get("error", "Failed to add lead") if result else "Failed to add lead")
            else:
                st.warning("Phone number is required")

    st.markdown("---")

    # ── Leads table ──────────────────────────────────────────────
    status_param = status_filter if status_filter != "All" else None
    path = f"/api/leads?limit=100"
    if status_param:
        path += f"&status={status_param}"

    leads = api_call("GET", path) or []

    if not leads:
        st.info("No leads found matching the current filters.")
        return

    st.caption(f"Showing {len(leads)} lead(s)")

    for lead in leads:
        lead_id = lead.get("id", "")
        status = lead.get("status", "pending")
        name = lead.get("name") or "Unknown"
        phone = lead.get("phone_number", "N/A")

        status_emoji = {
            "pending": "🟡",
            "in_progress": "🔵",
            "completed": "🟢",
            "failed": "🔴",
            "unreachable": "⚫",
        }.get(status, "⚪")

        with st.expander(f"{status_emoji} {name} — {phone} ({status})"):
            col1, col2 = st.columns(2)

            with col1:
                st.write(f"**ID:** {lead_id}")
                st.write(f"**Email:** {lead.get('email', 'N/A')}")
                st.write(f"**Program:** {lead.get('program_interest', 'N/A')}")
                st.write(f"**Source:** {lead.get('source', 'N/A')}")
                st.write(f"**Call Attempts:** {lead.get('call_attempts', 0)}")

            with col2:
                st.write(f"**Created:** {lead.get('created_at', 'N/A')[:19] if lead.get('created_at') else 'N/A'}")
                st.write(f"**Last Called:** {lead.get('last_called_at', 'Never')[:19] if lead.get('last_called_at') else 'Never'}")
                st.write(f"**Next Follow-up:** {lead.get('next_follow_up', 'None')[:19] if lead.get('next_follow_up') else 'None'}")
                if lead.get("notes"):
                    st.write(f"**Notes:** {lead['notes']}")

            # Action buttons
            col_a, col_b, col_c, col_d = st.columns(4)

            with col_a:
                if st.button("📞 Call Now", key=f"call_{lead_id}"):
                    result = api_call("POST", f"/api/leads/{lead_id}/call")
                    if result and "error" not in result:
                        st.success("Call queued!")
                    else:
                        st.error(result.get("error", "Failed") if result else "Failed")

            with col_b:
                new_status = st.selectbox(
                    "Status",
                    ["pending", "in_progress", "completed", "failed", "unreachable"],
                    index=["pending", "in_progress", "completed", "failed", "unreachable"].index(status) if status in ["pending", "in_progress", "completed", "failed", "unreachable"] else 0,
                    key=f"status_{lead_id}",
                )
                if new_status != status:
                    if st.button("Update", key=f"update_{lead_id}"):
                        api_call("PUT", f"/api/leads/{lead_id}", {"status": new_status})
                        st.rerun()

            with col_c:
                if st.button("📋 History", key=f"hist_{lead_id}"):
                    st.session_state["view_lead_id"] = lead_id
                    st.session_state["nav_to"] = "conversations"
                    st.rerun()

            with col_d:
                if st.button("📅 Schedule", key=f"sched_{lead_id}"):
                    st.session_state["schedule_lead_id"] = lead_id
                    st.session_state["nav_to"] = "scheduler"
                    st.rerun()
