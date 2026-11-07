"""
Offer letter PDF builder using fpdf2.

Produces a professional A4 PDF with university branding, student details,
program information, terms and conditions, and a signature block.

Usage:
    from app.offers.pdf import build_offer_pdf
    pdf_path = build_offer_pdf(lead, course, offer, out_dir)
"""

from __future__ import annotations

from pathlib import Path

from fpdf import FPDF

# ── Template constants ───────────────────────────────────────────────────
from app.config import settings

DEFAULT_TERMS = (
    "1. This offer is valid until the date stated above.\n"
    "2. Admission is contingent upon verification of all submitted documents.\n"
    "3. A non-refundable tuition deposit is required to reserve your seat.\n"
    "4. This offer is for the program and intake term stated above.\n"
    "5. Any change to submitted information may void this offer.\n"
    "6. International students must complete visa documentation by the deadline."
)

HEADER_BG = (25, 55, 109)   # Dark blue
HEADER_FG = (255, 255, 255)  # White
BODY_FG = (30, 30, 30)


class OfferLetterPDF(FPDF):
    """Custom PDF with page-number footer."""

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 9)
        self.set_text_color(120, 120, 120)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")


def build_offer_pdf(
    lead: dict,
    course: dict | None,
    offer: dict,
    out_dir: Path,
) -> Path:
    """
    Generate a branded offer-letter PDF.

    Args:
        lead:   Lead dict (name, email, program_interest, …)
        course: Course dict (name, duration, fees, intake, …) or None
        offer:  Offer-letter dict (id, offer_date, valid_until, terms, …)
        out_dir: Directory to write the PDF to (created if missing).

    Returns:
        Path to the generated PDF file.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / f"{offer['id']}.pdf"

    pdf = OfferLetterPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()

    lead_name = lead.get("name") or "Prospective Student"
    program = offer.get("program") or lead.get("program_interest", "")
    course_name = course.get("name", program) if course else program
    duration = course.get("duration", "") if course else ""
    fees = course.get("fees", "") if course else ""
    intake = course.get("intake", "") if course else ""
    offer_date = offer.get("offer_date", "")
    valid_until = offer.get("valid_until", "")
    terms = offer.get("terms") or DEFAULT_TERMS
    uni_name = settings.UNIVERSITY_NAME
    offer_ref = f"OL-{offer_date[:4] if offer_date else '2026'}-{offer['id'][:6].upper()}"

    # ── Header band ──────────────────────────────────────────────────
    pdf.set_fill_color(*HEADER_BG)
    pdf.rect(0, 0, 210, 38, "F")

    pdf.set_y(8)
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(*HEADER_FG)
    pdf.cell(0, 10, uni_name, align="C", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, "OFFICIAL OFFER OF ADMISSION", align="C", new_x="LMARGIN", new_y="NEXT")

    # ── Offer reference ──────────────────────────────────────────────
    pdf.ln(12)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(95, 5, f"Reference: {offer_ref}", align="L")
    pdf.cell(0, 5, f"Date: {offer_date}", align="R", new_x="LMARGIN", new_y="NEXT")
    if valid_until:
        pdf.cell(0, 5, f"Valid Until: {valid_until}", align="R", new_x="LMARGIN", new_y="NEXT")

    # ── Salutation ───────────────────────────────────────────────────
    pdf.ln(10)
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(*BODY_FG)
    pdf.cell(0, 7, f"Dear {lead_name},", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    # ── Congratulations paragraph ────────────────────────────────────
    pdf.set_font("Helvetica", "", 10.5)
    pdf.multi_cell(
        0, 5.5,
        f"Congratulations! We are pleased to inform you that you have been "
        f"accepted for admission to the following program at {uni_name}. "
        f"Your application demonstrated outstanding potential, and we are "
        f"excited to welcome you to our academic community.",
    )
    pdf.ln(4)

    # ── Details table ────────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_fill_color(240, 240, 245)
    pdf.cell(0, 7, "Program Details", fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    rows = [
        ("Student Name", lead_name),
        ("Program", course_name),
    ]
    if duration:
        rows.append(("Duration", duration))
    if fees:
        rows.append(("Tuition Fees", fees))
    if intake:
        rows.append(("Intake", intake))
    rows.append(("Offer Date", offer_date))
    if valid_until:
        rows.append(("Valid Until", valid_until))

    pdf.set_font("Helvetica", "", 10.5)
    col_w = 45
    for label, value in rows:
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(col_w, 7, label, border=0)
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 7, value, border=0, new_x="LMARGIN", new_y="NEXT")

    # ── Payment ──────────────────────────────────────────────────────
    payment_link = (course or {}).get("payment_link", "") or settings.DEFAULT_PAYMENT_LINK
    if payment_link:
        pdf.ln(8)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_fill_color(220, 240, 220)
        pdf.cell(0, 7, "Payment Instructions", fill=True, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(*BODY_FG)
        pdf.cell(0, 6, "To confirm your seat, please complete the payment:", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(35, 6, "Payment Link:", border=0)
        pdf.set_font("Helvetica", "", 9.5)
        pdf.set_text_color(25, 55, 109)
        pdf.cell(0, 6, payment_link, border=0, new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*BODY_FG)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(0, 6, "A non-refundable seat reservation fee is required to secure your admission.", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*BODY_FG)

    # ── Terms and conditions ─────────────────────────────────────────
    pdf.ln(8)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_fill_color(240, 240, 245)
    pdf.cell(0, 7, "Terms and Conditions", fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    pdf.set_font("Helvetica", "", 9.5)
    pdf.set_text_color(60, 60, 60)
    pdf.multi_cell(0, 5, terms)
    pdf.set_text_color(*BODY_FG)

    # ── Signature block ──────────────────────────────────────────────
    pdf.ln(12)
    pdf.set_font("Helvetica", "", 10.5)
    pdf.cell(0, 6, "Sincerely,", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(8)
    pdf.cell(80, 0.5, "", border="T", new_x="LMARGIN", new_y="NEXT")  # signature line
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 6, "Dr. A. Chancellor, Dean of Admissions", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 5, settings.OFFER_EMAIL, new_x="LMARGIN", new_y="NEXT")

    # ── Footer note ──────────────────────────────────────────────────
    pdf.ln(6)
    pdf.set_font("Helvetica", "I", 7.5)
    pdf.multi_cell(
        0, 4,
        "This is a computer-generated offer letter and does not require a "
        "physical signature. For questions, contact the Admissions Office at "
        f"{settings.OFFER_EMAIL}.",
    )

    # ── Output ───────────────────────────────────────────────────────
    pdf.output(str(pdf_path))
    return pdf_path
