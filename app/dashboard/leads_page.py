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

            # ── Documents & Offer Letters ──────────────────────────────
            st.divider()
            st.subheader("📄 Documents & Offer Letter")

            # Document upload
            uploaded_file = st.file_uploader(
                "Upload Document",
                type=["pdf", "png", "jpg", "jpeg", "docx", "xlsx", "txt"],
                key=f"doc_upload_{lead_id}",
            )
            doc_type = st.selectbox(
                "Document Type",
                ["transcript", "id_proof", "marksheet", "recommendation", "other"],
                key=f"doc_type_{lead_id}",
            )
            if uploaded_file and st.button("📤 Upload", key=f"upload_btn_{lead_id}"):
                from app.dashboard.pages import api_upload
                result = api_upload(
                    f"/api/leads/{lead_id}/documents",
                    "file",
                    uploaded_file.name,
                    uploaded_file.getvalue(),
                    {"doc_type": doc_type},
                )
                if result and "error" not in result:
                    offer_info = result.get("offer_letter")
                    if offer_info:
                        st.success(f"✅ Document uploaded and offer letter sent via {offer_info.get('sent_via', 'N/A')}!")
                    else:
                        st.success("Document uploaded!")
                    st.rerun()
                else:
                    st.error(result.get("error", "Upload failed") if result else "Upload failed")

            # Existing documents
            docs = api_call("GET", f"/api/leads/{lead_id}/documents") or []
            if docs:
                st.caption(f"{len(docs)} document(s) uploaded:")
                for d in docs:
                    c1, c2 = st.columns([4, 1])
                    with c1:
                        st.write(f"📎 {d.get('doc_type', 'file')} — {d.get('filename', '')} ({d.get('uploaded_at', '')[:10] if d.get('uploaded_at') else ''})")
                    with c2:
                        from app.dashboard.pages import api_call_bytes, BACKEND_URL
                        file_bytes = api_call_bytes(f"/api/documents/{d['id']}/file")
                        if file_bytes:
                            st.download_button(
                                "⬇️", file_bytes, file_name=d.get("filename", "download"),
                                key=f"dl_{d['id']}",
                            )
            else:
                st.caption("No documents uploaded yet.")

            # Offer letters
            st.markdown("---")
            offers = api_call("GET", f"/api/leads/{lead_id}/offer-letters") or []
            if offers:
                for o in offers:
                    status_emoji = {"sent": "🔵", "accepted": "🟢", "rejected": "🔴"}.get(o.get("status", ""), "⚪")
                    st.write(
                        f"{status_emoji} **{o.get('program', 'N/A')}** — "
                        f"Sent: {o.get('sent_at', '')[:10] if o.get('sent_at') else 'N/A'} | "
                        f"Via: {o.get('sent_via', 'N/A')} | "
                        f"Valid until: {o.get('valid_until', 'N/A')}"
                    )
                    co1, co2, co3 = st.columns(3)
                    with co1:
                        from app.dashboard.pages import BACKEND_URL
                        st.link_button("📄 View PDF", f"{BACKEND_URL}/api/offers/{o['id']}/pdf")
                    with co2:
                        if o.get("status") in ("sent",):
                            if st.button("✅ Accept", key=f"accept_{o['id']}"):
                                api_call("PUT", f"/api/offers/{o['id']}/status", {"status": "accepted"})
                                st.rerun()
                    with co3:
                        if o.get("status") in ("sent",):
                            if st.button("❌ Decline", key=f"decline_{o['id']}"):
                                api_call("PUT", f"/api/offers/{o['id']}/status", {"status": "rejected"})
                                st.rerun()
            else:
                lead_name = lead.get("name", "")
                lead_prog = lead.get("program_interest", "")
                if not lead_name or not lead_prog:
                    st.info("Add a name and program interest before generating an offer letter.")
                elif not docs:
                    st.info("Upload a document — the offer letter will be generated and sent automatically.")
