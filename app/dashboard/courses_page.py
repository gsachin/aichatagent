"""
Dashboard Courses page — manage program catalog for offer letters.
"""

from __future__ import annotations

import streamlit as st

from app.dashboard.pages import api_call


def render():
    st.title("📚 Course Catalog")
    st.markdown("---")

    # ── Add course form ──────────────────────────────────────────────
    with st.expander("➕ Add New Course", expanded=False):
        col_a, col_b = st.columns(2)
        with col_a:
            new_name = st.text_input("Course Name *", placeholder="Computer Science")
            new_duration = st.text_input("Duration", placeholder="4 Years")
            new_fees = st.text_input("Fees", placeholder="$15,000/year")
        with col_b:
            new_intake = st.text_input("Intake", placeholder="Fall 2026, Spring 2027")
            new_description = st.text_area("Description")
            new_payment_link = st.text_input("Payment Link", placeholder="https://pay.university.edu/course")

        if st.button("Add Course", type="primary"):
            if new_name:
                result = api_call(
                    "POST",
                    "/api/courses",
                    {
                        "name": new_name,
                        "duration": new_duration,
                        "fees": new_fees,
                        "intake": new_intake,
                        "description": new_description,
                        "payment_link": new_payment_link,
                    },
                )
                if result and "error" not in result:
                    st.success(f"Course added: {result.get('name')}")
                    st.rerun()
                else:
                    st.error(result.get("error", "Failed") if result else "Failed")
            else:
                st.warning("Course name is required")

    st.markdown("---")

    # ── Courses table ─────────────────────────────────────────────────
    courses = api_call("GET", "/api/courses") or []

    if not courses:
        st.info("No courses defined yet. Add one above to get started.")
        return

    st.caption(f"{len(courses)} course(s)")

    for course in courses:
        course_id = course.get("id", "")
        name = course.get("name", "Unknown")
        active = course.get("is_active", True)
        status_badge = "🟢 Active" if active else "🔴 Inactive"

        with st.expander(f"{status_badge} {name} — {course.get('duration', 'N/A')}"):
            col1, col2 = st.columns(2)
            with col1:
                st.write(f"**Duration:** {course.get('duration', 'N/A')}")
                st.write(f"**Fees:** {course.get('fees', 'N/A')}")
                st.write(f"**Intake:** {course.get('intake', 'N/A')}")
                pay_link = course.get("payment_link", "")
                if pay_link:
                    st.write(f"**Payment Link:** {pay_link}")
            with col2:
                st.write(f"**Status:** {status_badge}")
                st.write(f"**Created:** {course.get('created_at', 'N/A')[:19] if course.get('created_at') else 'N/A'}")
                if course.get("description"):
                    st.write(f"**Description:** {course['description']}")

            col_a, col_b, col_c = st.columns(3)
            with col_a:
                new_status_label = "Deactivate" if active else "Reactivate"
                if st.button(new_status_label, key=f"toggle_{course_id}"):
                    result = api_call(
                        "PUT", f"/api/courses/{course_id}",
                        {"is_active": not active},
                    )
                    if result:
                        st.success(f"Course {new_status_label.lower()}d")
                        st.rerun()
            with col_b:
                if st.button("🗑️ Delete", key=f"del_{course_id}"):
                    result = api_call("DELETE", f"/api/courses/{course_id}")
                    if result:
                        st.success("Course deleted")
                        st.rerun()
