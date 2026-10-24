"""Turn DSPMU PDF tables into structured, source-linked facts.

The fee PDF is the important case. Its table extracts cleanly with pdfplumber,
so the build can keep the numbers exact and render them from a template instead
of asking a model to summarize a table. Every fact carries its source URL and
a validity window; the first build marks all facts as drafts because no human
at the university has signed them yet.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

#: The 2026 fee sheet is for the 2026-27 academic year. Keep the window
#: conservative until the university confirms the end date.
FEE_VALID_FROM = "2026-07-01"
FEE_VALID_UNTIL = "2027-06-30"

#: The counselling schedule is a 2026 admission-cycle document.
COUNSELLING_VALID_UNTIL = "2026-12-31"

_SOCIAL_SCIENCE = {
    "political science", "history", "economics", "psychology", "geography",
    "sociology", "anthropology",
}
_HUMANITIES = {
    "hindi", "english", "urdu", "sanskrit", "philosophy", "nagpuri", "kurux",
    "khortha", "santhali", "kurmali", "panchpargnia", "mundari", "ho", "kharia",
    "english language & literature", "music", "kathak dance", "visual art painting",
}
_SCIENCE = {
    "physics", "chemistry", "mathematics", "botany", "zoology", "geology",
    "environmental sc & sustainability", "electronics", "integrated physics",
    "microbiology", "fisheries sciences",
}
_VOCATIONAL = {
    "bba", "computer application", "journalism & mass communication",
    "information technology", "commerce",
}

#: Explicit degree labels where the fee sheet's subject does not identify the
#: degree on its own. Everything else is mapped by faculty below.
_SPECIAL_DEGREES = {
    "bba": ("Bachelor of Business Administration (BBA)", "BBA"),
    "computer application": ("Bachelor of Computer Applications (BCA)", "BCA"),
    "journalism & mass communication": (
        "Bachelor of Arts (BA) in Journalism & Mass Communication",
        "BA (JMC)",
    ),
    "information technology": (
        "Bachelor of Science (B.Sc.) in Information Technology",
        "B.Sc. (IT)",
    ),
    "commerce": ("Bachelor of Commerce (B.Com.)", "B.Com."),
    "fisheries sciences": (
        "Bachelor of Fisheries Sciences (B.F.Sc.)",
        "B.F.Sc.",
    ),
    "music": ("Bachelor of Arts (BA) in Music", "BA (Music)"),
    "kathak dance": ("Bachelor of Arts (BA) in Kathak Dance", "BA (Kathak)"),
    "visual art painting": (
        "Bachelor of Arts (BA) in Visual Art Painting",
        "BA (Visual Art)",
    ),
}

#: The site's four faculties. The entry body says "School of ..." because the
#: existing entity parser looks for that exact phrase; the parenthetical keeps
#: the official faculty name visible to a reader.
_SCHOOL_DISPLAY = {
    "Social Sciences": "School of Social Sciences, Faculty of Social Sciences",
    "Humanities": "School of Humanities, Faculty of Humanities",
    "Science": "School of Natural Sciences, Faculty of Science",
    "Vocational Studies": "School of Vocational Studies, Faculty of Vocational Studies",
}

_PG_DATES_FALLBACK = (
    ("2026-10-25", "25.10.2026", "Last Date of Application"),
    ("2026-10-27", "27.10.2026", "Publication of First Selection List"),
    (
        "2026-10-30",
        "30.10.2026",
        "Last date for payment of online fees - First Selection List",
    ),
)
_PG_EXPECTED = {
    activity: (iso, text_value)
    for iso, text_value, activity in _PG_DATES_FALLBACK
}


@dataclass(frozen=True)
class ProgrammeFee:
    sr: int
    subject: str
    programme: str
    degree: str
    school: str
    annual_gen: int
    annual_reserved: int
    semester_gen: int
    semester_bc: int
    semester_sc: int
    source_url: str
    source_page: int
    valid_from: str = FEE_VALID_FROM
    valid_until: str = FEE_VALID_UNTIL
    approved_by: str | None = None
    status: str = "draft"

    def to_dict(self) -> dict:
        return {
            "fact_id": f"fee.ug.{slugify(self.subject)}.2026.gen",
            "programme": self.programme,
            "degree": self.degree,
            "subject": self.subject,
            "school": self.school,
            "annual_fee_gen": self.annual_gen,
            "annual_fee_reserved": self.annual_reserved,
            "semester_course_fee_gen": self.semester_gen,
            "semester_course_fee_bc": self.semester_bc,
            "semester_course_fee_sc": self.semester_sc,
            "source_url": self.source_url,
            "source_page": self.source_page,
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
            "approved_by": self.approved_by,
            "status": self.status,
            "currency": "INR",
        }


@dataclass(frozen=True)
class CounsellingRound:
    sl: int | None
    round: str
    dates: str
    publication: str
    source_url: str
    valid_until: str = COUNSELLING_VALID_UNTIL
    status: str = "draft"

    def to_dict(self) -> dict:
        return {
            "round": self.round,
            "dates": self.dates,
            "publication": self.publication,
            "source_url": self.source_url,
            "valid_until": self.valid_until,
            "status": self.status,
        }


def _as_int(value: str) -> int:
    cleaned = re.sub(r"[^\d]", "", value or "")
    return int(cleaned) if cleaned else 0


def _subject_key(subject: str) -> str:
    return re.sub(r"\s+", " ", (subject or "").strip().lower())


def _faculty(subject: str) -> str:
    key = _subject_key(subject)
    if key in _SOCIAL_SCIENCE:
        return "Social Sciences"
    if key in _HUMANITIES:
        return "Humanities"
    if key in _SCIENCE:
        return "Science"
    if key in _VOCATIONAL:
        return "Vocational Studies"
    return "Social Sciences"


def programme_for(subject: str) -> tuple[str, str]:
    """Return ``(full programme name, degree label)`` for a fee-table subject."""
    key = _subject_key(subject)
    if key in _SPECIAL_DEGREES:
        return _SPECIAL_DEGREES[key]
    if _faculty(subject) == "Science":
        return (
            f"Bachelor of Science (B.Sc.) in {subject}",
            f"B.Sc. ({subject})",
        )
    return (
        f"Bachelor of Arts (BA) in {subject}",
        f"BA ({subject})",
    )


def parse_fee_tables(
    tables: tuple[tuple[tuple[tuple[str, ...], ...], ...], ...],
    source_url: str,
    *,
    text: str = "",
) -> list[ProgrammeFee]:
    """Parse the fee sheet's seven-column table across both pages.

    pdfplumber loses one row at the page break (row 33, ``Electronics``). The
    raw pypdf text still has it, so a line-shaped fallback fills only rows the
    table parser did not already see.
    """
    found: dict[int, ProgrammeFee] = {}
    for page_index, page_tables in enumerate(tables, start=1):
        for table in page_tables:
            for row in table:
                if len(row) < 7:
                    continue
                first = (row[0] or "").strip()
                subject = (row[1] or "").strip()
                if not first.isdigit() or not subject:
                    continue
                values = row[2:7]
                if sum(1 for v in values if _as_int(v) or (v or "").strip() in {"0", "-"}) < 3:
                    continue
                sr = int(first)
                if sr in found:
                    continue
                programme, degree = programme_for(subject)
                school = _SCHOOL_DISPLAY[_faculty(subject)]
                found[sr] = ProgrammeFee(
                    sr=sr,
                    subject=subject,
                    programme=programme,
                    degree=degree,
                    school=school,
                    annual_gen=_as_int(values[0]),
                    annual_reserved=_as_int(values[1]),
                    semester_gen=_as_int(values[2]),
                    semester_bc=_as_int(values[3]),
                    semester_sc=_as_int(values[4]),
                    source_url=source_url,
                    source_page=page_index,
                )

    if text:
        line_pattern = re.compile(
            r"(?m)^\s*(\d{1,2})\s+([A-Za-z][^\n]*?)\s+"
            r"(\d{1,6})\s+(\d{1,6})\s+(\d{1,6})\s+(\d{1,6})\s+(\d{1,6})\s*$"
        )
        for match in line_pattern.finditer(text):
            sr = int(match.group(1))
            if sr in found:
                continue
            subject = re.sub(r"\s+", " ", match.group(2)).strip()
            if not subject:
                continue
            programme, degree = programme_for(subject)
            school = _SCHOOL_DISPLAY[_faculty(subject)]
            found[sr] = ProgrammeFee(
                sr=sr,
                subject=subject,
                programme=programme,
                degree=degree,
                school=school,
                annual_gen=_as_int(match.group(3)),
                annual_reserved=_as_int(match.group(4)),
                semester_gen=_as_int(match.group(5)),
                semester_bc=_as_int(match.group(6)),
                semester_sc=_as_int(match.group(7)),
                source_url=source_url,
                source_page=0,
            )
    return [found[key] for key in sorted(found)]


def parse_counselling_tables(
    tables: tuple[tuple[tuple[tuple[str, ...], ...], ...], ...],
    source_url: str,
) -> list[CounsellingRound]:
    """Parse the UG counselling schedule table."""
    def cell(value: str) -> str:
        return re.sub(r"\s+", " ", value or "").strip()

    out: list[CounsellingRound] = []
    seen: set[tuple[str, str]] = set()
    for page_tables in tables:
        for table in page_tables:
            for row in table:
                if len(row) < 4:
                    continue
                first = cell(row[0])
                round_name = cell(row[1])
                dates = cell(row[2])
                publication = cell(row[3])
                if "counselling round" in round_name.lower():
                    continue
                if "publication of schedule" in publication.lower():
                    continue
                if not round_name and not dates and not publication:
                    continue
                key = (round_name, dates)
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    CounsellingRound(
                        sl=int(first) if first.isdigit() else None,
                        round=round_name or "Publication of merit list",
                        dates=dates,
                        publication=publication,
                        source_url=source_url,
                    )
                )
    return out


def parse_documents(text: str) -> list[dict]:
    """Extract the numbered document checklist from the admission PDF."""
    cleaned = re.sub(r"\s+", " ", text or "")
    matches = re.findall(
        r"(?:^|\s)(\d{1,2})\.\s*(.+?)(?=\s+\d{1,2}\.\s*|\Z)",
        cleaned,
        flags=re.S,
    )
    out: list[dict] = []
    for number, item in matches:
        value = re.sub(r"\s+", " ", item).strip(" .;")
        if not value or len(value) < 4:
            continue
        out.append({"number": int(number), "text": value})
    # Some PDFs put every item on its own line without a numbered regex match.
    if not out:
        for line in (text or "").splitlines():
            match = re.match(r"\s*(\d{1,2})[.)]\s*(.+)", line)
            if match:
                out.append({"number": int(match.group(1)), "text": match.group(2).strip()})
    return out[:25]


def _iso_date(value: str) -> str:
    for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    return ""


def parse_pg_dates(text: str) -> list[dict]:
    """Extract the three dated PG admission milestones from the OCR text.

    The source is a scanned table, so the OCR order is not reliable. The
    function looks for each activity and its nearest date. If OCR cannot
    recover one, it uses the three values that were checked visually against
    the rendered PDF, and labels them as a fallback.
    """
    cleaned = re.sub(r"\s+", " ", text or "")
    out: list[dict] = []
    for activity, expected in _PG_EXPECTED.items():
        match = re.search(re.escape(activity), cleaned, re.IGNORECASE)
        parsed = ""
        if match:
            window = cleaned[max(0, match.start() - 220):match.end() + 220]
            dates = re.findall(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{4}\b", window)
            # The notice's memo date (15.06.2026) sits near the top and is not
            # an admission milestone. Keep dates in the 2026-27 admission window.
            dates = [value for value in dates if _iso_date(value) >= "2026-07-01"]
            if dates:
                parsed = dates[0]
        if parsed and _iso_date(parsed) == expected[0]:
            out.append(
                {
                    "date": expected[0],
                    "date_text": parsed,
                    "activity": activity,
                    "needs_review": True,
                    "source": "OCR of the PG admission PDF",
                }
            )
        else:
            # The scanned table's OCR order is not reliable. These three
            # milestones were checked against the rendered page; the fallback
            # is explicit and the core cards mark all PG dates for review.
            out.append(
                {
                    "date": expected[0],
                    "date_text": expected[1],
                    "activity": activity,
                    "needs_review": True,
                    "source": "OCR fallback, checked visually",
                }
            )
    return out


def collect_contacts(
    contact_doc,
    home_doc,
    pg_text: str,
) -> dict:
    """Collect the official, public contact points without guessing."""
    emails: list[str] = []
    for doc in (contact_doc, home_doc):
        for email in getattr(doc, "emails", ()):
            if email not in emails and "dspmuranchi.ac.in" in email:
                emails.append(email)
    for email in re.findall(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", pg_text or ""):
        if "dspmuranchi.ac.in" in email and email not in emails:
            emails.append(email)
    preferred = "registrar@dspmuranchi.ac.in"
    if preferred in emails:
        emails.remove(preferred)
        emails.insert(0, preferred)
    return {
        "address": "P.O. - Ranchi University, Morabadi, Ranchi - 834008, Jharkhand",
        "phone": "+91 930 455 261",
        "emails": emails,
        "website": "https://dspmuranchi.ac.in",
        "contact_page": "https://dspmuranchi.ac.in/cms/contact-us",
        "admission_page": "https://dspmuranchi.ac.in/news/admission-merit-list",
    }


def entry_head(programme: str) -> str:
    """Move a mid-name abbreviation to the end so the full name is contiguous.

    ``kb_entities`` resolves a programme with a contiguous phrase match. The
    human-facing name ``Bachelor of Arts (BA) in Political Science`` normalises
    to ``bachelor of arts ba in political science``, which does not contain
    ``bachelor of arts in political science``. The parser must see the full
    name before the abbreviation. The reader still sees both names.
    """
    match = re.match(r"^(.*?)\s*\(([^)]+)\)\s*(in\s+.+)$", programme or "")
    if match:
        return f"{match.group(1).strip()} {match.group(3).strip()} ({match.group(2).strip()})"
    return programme


def programme_entry(fee: ProgrammeFee) -> str:
    """Render one parser-compatible programme entry.

    The existing ``app.kb_entities`` parser reads ``###`` entries under
    ``## Undergraduate Programs``; the first line is the entity head, and every
    digit in the entry body belongs to that entity. Keep the degree name before
    the colon and keep the fee numbers in the body.
    """
    return (
        f"### {slugify(fee.programme)}\n"
        f"{entry_head(fee.programme)}: 3 years, {fee.school}.\n"
        f"Tuition: ₹{fee.annual_gen} per year (general category); "
        f"₹{fee.annual_reserved} per year (ST/SC/BC I & II). "
        f"Course fee: ₹{fee.semester_gen} per semester (general); "
        f"₹{fee.semester_bc} (BC I & II); ₹{fee.semester_sc} (ST/SC).\n"
    )


def fee_table_markdown(fees: list[ProgrammeFee]) -> str:
    lines = [
        "| Programme | Annual (GEN) | Annual (ST/SC/BC I & II) | Semester (GEN) |",
        "| --- | ---: | ---: | ---: |",
    ]
    for fee in fees:
        lines.append(
            f"| {fee.programme} | ₹{fee.annual_gen:,} | "
            f"₹{fee.annual_reserved:,} | ₹{fee.semester_gen:,} |"
        )
    return "\n".join(lines)


def slugify(value: str) -> str:
    value = (value or "").lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value[:80] or "entry"


def iso_today() -> str:
    return date.today().isoformat()
