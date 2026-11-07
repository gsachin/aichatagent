#!/usr/bin/env python3
"""Build the DSPMU programme/department catalogue from source-backed inputs.

Inputs:
  * content/dspmu/facts.json      — the 2026 UG fee sheet (43 programmes)
  * content/dspmu/nga_graph.sqlite3 — department page versions and document links

The generator never invents a programme. UG rows come from the fee facts. PG,
PhD and Diploma rows come from the department pages' syllabus/document links.
Every row keeps its source page/PDF and is marked draft or page_derived until a
named reviewer signs it off.

Usage:
  .venv/bin/python scripts/nga_catalogue.py
  .venv/bin/python scripts/nga_catalogue.py --check
  .venv/bin/python scripts/nga_catalogue.py --report
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.knowledge.catalogue import Catalogue, _norm  # noqa: E402

DEFAULT_FACTS = PROJECT_ROOT / "content/dspmu/facts.json"
DEFAULT_DB = PROJECT_ROOT / "content/dspmu/nga_graph.sqlite3"
DEFAULT_OUT = PROJECT_ROOT / "content/dspmu/programmes.json"
BASE_URL = "https://dspmuranchi.ac.in"

#: Mapping from a fee-sheet subject to the department page that carries its
#: syllabus/document links. Missing mappings simply leave the row cited to the
#: fee sheet only.
FACTS_PAGE = {
    "political science": "departments.political-science",
    "history": "departments.history",
    "economics": "departments.economics",
    "psychology": "departments.psychology",
    "geography": "departments.geography",
    "sociology": "departments.sociology",
    "anthropology": "departments.anthropology",
    "hindi": "departments.hindi",
    "english": "departments.english",
    "urdu": "departments.urdu",
    "sanskrit": "departments.sanskrit",
    "philosophy": "departments.philosophy",
    "nagpuri": "departments.nagpuri",
    "kurux": "departments.kurux",
    "khortha": "departments.khortha",
    "santhali": "departments.santhali",
    "kurmali": "departments.kurmali",
    "panchpargnia": "departments.panchpargania",
    "mundari": "departments.mundari",
    "ho": "departments.ho",
    "kharia": "departments.kharia",
    "physics": "departments.physics",
    "chemistry": "departments.chemistry",
    "mathematics": "departments.mathematics",
    "botany": "departments.botany",
    "zoology": "departments.zoology",
    "geology": "departments.geology",
    "commerce": "departments.commerce",
    "environmental sc & sustainability": "departments.school-of-environmental-science",
    "bba": "departments.mba",
    "computer application": "departments.b-sc-computer-application",
    "journalism & mass communication": "departments.bachelor-programme-in-journalism-and-mass-communication",
    "electronics": "departments.electronics",
    "english language & literature": "departments.department-of-english-language-and-literature",
    "fisheries sciences": "departments.b-sc-fish-and-fisheries",
    "information technology": "departments.b-sc-information-technology",
    "yogic science": "departments.department-of-yoga",
    "public administration": "departments.m-a-public-administration",
    "visual art painting": "departments.visual-art",
}

#: PG/diploma rows whose level and degree are explicit in the department page.
#: Tuple: (document title, level, degree, subject, primary node_id)
PG_SPECS = [
    ("P.G Anthropology", "PG", "MA", "Anthropology", "departments.anthropology"),
    ("P.G Bangla", "PG", "MA", "Bangla", "departments.bangla"),
    ("P.G Chemistry", "PG", "M.Sc", "Chemistry", "departments.chemistry"),
    ("P.G Economics", "PG", "MA", "Economics", "departments.economics"),
    ("P.G English", "PG", "MA", "English", "departments.english"),
    ("P.G Environmental Science", "PG", "M.Sc", "Environmental Science", "departments.school-of-environmental-science"),
    ("P.G Geography", "PG", "MA", "Geography", "departments.geography"),
    ("P.G Hindi", "PG", "MA", "Hindi", "departments.hindi"),
    ("P.G History", "PG", "MA", "History", "departments.history"),
    ("P.G LL.M", "PG", "LL.M", "Law", "departments.ll-m"),
    ("P.G M.Com", "PG", "M.Com", "Commerce", "departments.commerce"),
    ("P.G Mathematics", "PG", "M.Sc", "Mathematics", "departments.mathematics"),
    ("P.G MBA", "PG", "MBA", "Business Administration", "departments.mba"),
    ("P.G Microbiology", "PG", "M.Sc", "Microbiology", "departments.microbiology"),
    ("P.G Philosophy", "PG", "MA", "Philosophy", "departments.philosophy"),
    ("P.G Political Science", "PG", "MA", "Political Science", "departments.political-science"),
    ("P.G Psychology", "PG", "MA", "Psychology", "departments.psychology"),
    ("P.G Tribal and Regional Languages", "PG", "MA", "Tribal and Regional Languages", "departments.ho"),
    ("P.G Urdu", "PG", "MA", "Urdu", "departments.urdu"),
    ("P.G Zoology", "PG", "M.Sc", "Zoology", "departments.zoology"),
    ("P.G. Botany", "PG", "M.Sc", "Botany", "departments.botany"),
    ("M.Sc. IT", "PG", "M.Sc", "Information Technology", "departments.m-sc-information-technology"),
    ("MCA", "PG", "MCA", "Computer Application", "departments.m-sc-information-technology"),
    ("MHA", "PG", "MHA", "Hospital Administration", "departments.mba"),
    ("MA Political Sc. PG", "PG", "MA", "Political Science", "departments.political-science"),
    ("PG ELL Syllabus", "PG", "MA", "English Language & Literature", "departments.department-of-english-language-and-literature"),
    ("Public Administration", "PG", "MA", "Public Administration", "departments.m-a-public-administration"),
    ("Rural Development", "PG", "MA", "Rural Development", "departments.m-a-rural-development"),
    ("Diploma in Amanat Survey", "Diploma", "Diploma", "Amanat Survey", "departments.department-of-amanat-survey"),
    ("Remote Sensing and GIS", "Diploma", "Diploma", "Remote Sensing & GIS", "departments.department-of-remote-sensing-gis"),
    # Page-derived rows with no syllabus file but an official department page.
    ("", "UG", "B.Ed", "Education", "departments.b-ed"),
    ("", "Diploma", "PG Diploma", "Performing Arts", "departments.pg-diploma-in-performing-arts"),
]

_SUBJECT_DISPLAY = {
    "panchpargnia": "Panch Pargania",
    "environmental sc & sustainability": "Environmental Science & Sustainability",
    "visual art painting": "Visual Art",
    "yogic science": "Yogic Science",
    "journalism & mass communication": "Journalism & Mass Communication",
    "english language & literature": "English Language & Literature",
    "fisheries sciences": "Fisheries Sciences",
    "information technology": "Information Technology",
    "computer application": "Computer Application",
    "integrated physics": "Integrated Physics",
    "kathak dance": "Kathak Dance",
}

_SUBJECT_ALIASES = {
    "information technology": ("IT", "Information Tech"),
    "journalism & mass communication": ("JMC", "B.J.M.C", "BJMC"),
    "english language & literature": ("ELL", "English Literature"),
    "panch pargania": ("PanchPargnia", "Panch Pargania"),
    "environmental science & sustainability": (
        "Environmental Science",
        "Environmental Sc & Sustainability",
    ),
    "visual art": ("Visual Art Painting",),
}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _norm(value)).strip("-")


def _subject_display(subject: str) -> str:
    key = subject.casefold()
    return _SUBJECT_DISPLAY.get(key, subject)


def _short_ug_name(degree: str, subject: str) -> str:
    subject = _subject_display(subject)
    degree = degree.strip()
    if degree.startswith("BA ("):
        if subject.casefold() == "journalism & mass communication":
            return "BA JMC"
        return f"BA {subject}"
    if degree.startswith("B.Sc. ("):
        if subject.casefold() == "information technology":
            return "B.Sc Information Technology"
        return f"B.Sc {subject}"
    if degree == "B.Com.":
        return "B.Com"
    if degree in ("BBA", "BCA", "B.F.Sc."):
        return degree
    return f"{degree} {subject}".strip()


def _full_pg_name(degree: str, subject: str) -> str:
    if degree == "MA":
        return f"Master of Arts in {subject} (MA)"
    if degree == "M.Sc":
        return f"Master of Science in {subject} (M.Sc)"
    if degree == "M.Com":
        return "Master of Commerce (M.Com)"
    if degree == "MBA":
        return "Master of Business Administration (MBA)"
    if degree == "MCA":
        return "Master of Computer Applications (MCA)"
    if degree == "LL.M":
        return "Master of Laws (LL.M)"
    if degree == "MHA":
        return "Master of Hospital Administration (MHA)"
    if degree == "PG Diploma":
        return f"Post Graduate Diploma in {subject}"
    if degree == "Diploma":
        return f"Diploma in {subject}"
    if degree == "B.Ed":
        return "Bachelor of Education (B.Ed)"
    return f"{degree} {subject}".strip()


def _pg_name(degree: str, subject: str) -> str:
    if degree == "PG Diploma":
        return f"PG Diploma in {subject}"
    if degree == "Diploma":
        return f"Diploma in {subject}"
    if degree in ("M.Com", "MBA", "MCA", "LL.M", "MHA", "B.Ed"):
        return degree
    return f"{degree} {subject}"


def _facts_aliases(name: str, full_name: str, degree: str, subject: str) -> list[str]:
    values = [name, full_name]
    # A bare degree is not a programme alias; it makes "M.Sc IT" match the
    # first M.Sc row. Keep the degree only when it is the programme name
    # itself (MBA, MCA, LL.M, M.Com, BBA, BCA, B.Ed, B.F.Sc.).
    if degree.strip().casefold() in {
        "mba", "mca", "ll.m", "m.com", "bba", "bca", "b.ed", "b.f.sc.",
    }:
        values.append(degree)
    values.append(subject)
    key = subject.casefold()
    values.extend(_SUBJECT_ALIASES.get(key, ()))
    if "information technology" in key:
        values.extend(["IT", "B.Sc IT", "BSc IT"])
        if degree.lower().startswith("m.sc"):
            values.extend(["M.Sc IT", "MSc IT", "M Sc IT"])
    if "journalism" in key:
        values.extend(["JMC", "B.J.M.C", "BJMC"])
    if "english language" in key:
        values.extend(["ELL", "English Literature"])
    return [value for value in dict.fromkeys(values) if value]


def _load_pages(db: Path) -> dict[str, dict]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 5000")
    rows = con.execute(
        """
        SELECT n.node_id, n.title, n.url, v.documents, v.fetched_at
        FROM nga_node n
        LEFT JOIN nga_page_version v ON v.node_id = n.node_id
        WHERE n.section = 'Departments'
        """
    ).fetchall()
    con.close()
    pages: dict[str, dict] = {}
    for row in rows:
        try:
            documents = json.loads(row["documents"] or "[]")
        except (TypeError, ValueError):
            documents = []
        docs = {}
        for document in documents:
            title = str(document.get("title") or "").strip()
            url = str(document.get("url") or "").strip()
            if title and url:
                docs[title] = url
        pages[str(row["node_id"])] = {
            "title": str(row["title"] or ""),
            "url": str(row["url"] or ""),
            "docs": docs,
            "fetched_at": str(row["fetched_at"] or "") or None,
        }
    return pages


def _doc_url(page: dict | None, title: str) -> str:
    if not page or not title:
        return ""
    target = _norm(title)
    for doc_title, url in page["docs"].items():
        normalized = _norm(doc_title)
        if normalized == target or normalized.startswith(target + " "):
            return url
    return ""


def _fact_rows(facts: dict, pages: dict[str, dict], generated_at: str) -> list[dict]:
    rows = []
    for fee in facts.get("fees") or []:
        # The fee sheet has line-wrapped subjects ("Environmental Sc &\n
        # Sustainability"); collapse them before matching or display.
        subject = " ".join(str(fee.get("subject") or "").split())
        degree = " ".join(str(fee.get("degree") or "").split())
        programme = " ".join(str(fee.get("programme") or "").split())
        if not subject or not degree or not programme:
            continue
        subject_display = _subject_display(subject)
        name = _short_ug_name(degree, subject)
        node_id = FACTS_PAGE.get(subject.casefold(), "")
        page = pages.get(node_id) if node_id else None
        page_url = page["url"] if page else ""
        document_urls = []
        if page:
            for title, url in page["docs"].items():
                lowered = title.casefold()
                if "ug" in lowered or "b.a" in lowered or "b.sc" in lowered or "b.com" in lowered:
                    document_urls.append(url)
            if not document_urls:
                document_urls = list(page["docs"].values())
        source_urls = [str(fee.get("source_url") or "").strip()]
        if page_url:
            source_urls.append(page_url)
        source_urls.extend(document_urls)
        rows.append(
            {
                "id": f"ug-{_slug(subject_display)}",
                "name": name,
                "full_name": programme,
                "level": "UG",
                "degree": degree,
                "subject": subject_display,
                "department": (page["title"] if page else str(fee.get("school") or subject_display)),
                "page_url": page_url,
                "document_urls": list(dict.fromkeys(document_urls)),
                "fee_fact_id": str(fee.get("fact_id") or ""),
                "source": "facts.json+department_page" if page else "facts.json",
                "source_urls": list(dict.fromkeys(url for url in source_urls if url)),
                "verified_at": generated_at or None,
                "status": str(fee.get("status") or "draft"),
                "reviewed_by": fee.get("approved_by") or None,
                "aliases": _facts_aliases(name, programme, degree, subject_display),
            }
        )
    return rows


def _pg_rows(pages: dict[str, dict]) -> list[dict]:
    rows: list[dict] = []
    for title, level, degree, subject, node_id in PG_SPECS:
        page = pages.get(node_id)
        if page is None:
            continue
        document_url = _doc_url(page, title)
        name = _pg_name(degree, subject)
        full_name = _full_pg_name(degree, subject)
        source_urls = [page["url"]]
        if document_url:
            source_urls.append(document_url)
        row = {
            "id": f"{level.lower()}-{_slug(name)}",
            "name": name,
            "full_name": full_name,
            "level": level,
            "degree": degree,
            "subject": subject,
            "department": page["title"],
            "page_url": page["url"],
            "document_urls": [document_url] if document_url else [],
            "fee_fact_id": "",
            "source": "department_page" if document_url else "department_page_metadata",
            "source_urls": source_urls,
            "verified_at": page["fetched_at"],
            "status": "page_derived",
            "reviewed_by": None,
            "aliases": _facts_aliases(name, full_name, degree, subject),
        }
        rows.append(row)
    return rows


def _shared_pg_sources(rows: list[dict], pages: dict[str, dict]) -> None:
    """Attach every department page that carries the same shared PG document."""
    for row in rows:
        subject = row.get("subject", "").casefold()
        if not subject:
            continue
        for page in pages.values():
            for title, url in page["docs"].items():
                if subject not in title.casefold():
                    continue
                if page["url"] not in row["source_urls"]:
                    row["source_urls"].append(page["url"])
                if url not in row["document_urls"]:
                    row["document_urls"].append(url)
                    row["source_urls"].append(url)


def _special_rows(pages: dict[str, dict]) -> list[dict]:
    rows = []
    bangla = pages.get("departments.bangla")
    if bangla:
        doc = _doc_url(bangla, "B.A Bangla Download / View")
        rows.append(
            {
                "id": "ug-ba-bangla",
                "name": "BA Bangla",
                "full_name": "Bachelor of Arts in Bangla (BA)",
                "level": "UG",
                "degree": "BA",
                "subject": "Bangla",
                "department": bangla["title"],
                "page_url": bangla["url"],
                "document_urls": [doc] if doc else [],
                "fee_fact_id": "",
                "source": "department_page",
                "source_urls": [bangla["url"]] + ([doc] if doc else []),
                "verified_at": bangla["fetched_at"],
                "status": "page_derived",
                "reviewed_by": None,
                "aliases": ["BA Bangla", "B.A Bangla", "Bangla"],
            }
        )
    return rows


def _doctorate_row() -> dict:
    url = f"{BASE_URL}/cms/academic-programmes"
    return {
        "id": "phd-programme",
        "name": "Ph.D. Programme",
        "full_name": "Doctor of Philosophy (Ph.D.)",
        "level": "PhD",
        "degree": "Ph.D.",
        "subject": "Doctoral Programme",
        "department": "Academic Programmes",
        "page_url": url,
        "document_urls": [],
        "fee_fact_id": "",
        "source": "academic_programmes_page",
        "source_urls": [url],
        "verified_at": None,
        "status": "page_derived",
        "reviewed_by": None,
        "aliases": ["PhD", "Ph.D.", "Ph.D", "Doctorate", "Doctoral"],
    }


def _merge(rows: list[dict]) -> list[dict]:
    """Merge duplicate level+name rows (shared PG documents)."""
    merged: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row["level"], row["name"].casefold())
        existing = merged.get(key)
        if existing is None:
            merged[key] = row
            continue
        for field in ("document_urls", "source_urls", "aliases"):
            existing[field] = list(
                dict.fromkeys(existing.get(field, []) + row.get(field, []))
            )
        existing["source"] = "department_page"
    return list(merged.values())


def build(facts_path: Path, db_path: Path) -> dict:
    facts = _load_json(facts_path)
    generated_at = str(facts.get("generated_at") or "")
    pages = _load_pages(db_path)
    rows = []
    rows.extend(_fact_rows(facts, pages, generated_at))
    pg_rows = _pg_rows(pages)
    _shared_pg_sources(pg_rows, pages)
    rows.extend(pg_rows)
    rows.extend(_special_rows(pages))
    rows.append(_doctorate_row())
    rows = _merge(rows)
    for row in rows:
        row["document_urls"] = list(dict.fromkeys(row.get("document_urls", [])))
        row["source_urls"] = list(dict.fromkeys(row.get("source_urls", [])))
    rows.sort(
        key=lambda row: (
            {"UG": 0, "PG": 1, "PhD": 2, "Diploma": 3, "Certificate": 4}.get(
                row["level"], 9
            ),
            row["department"].casefold(),
            row["name"].casefold(),
        )
    )
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["level"]] = counts.get(row["level"], 0) + 1
    return {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "source": "facts.json + department pages",
            "facts_generated_at": generated_at,
            "page_count": len(pages),
            "counts": counts,
            "review_required": True,
            "note": (
                "UG rows come from the 2026 fee facts; PG/PhD/Diploma rows "
                "come from public department pages. Rows are draft or "
                "page-derived until a named reviewer signs them off."
            ),
        },
        "programmes": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the DSPMU programme catalogue.")
    parser.add_argument("--facts", default=str(DEFAULT_FACTS))
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args(argv)

    payload = build(Path(args.facts), Path(args.db))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    catalogue = Catalogue.load(out)
    if args.check or args.report:
        print("NGA programme catalogue")
        print(f"  rows:    {len(catalogue.rows)}")
        print(f"  levels:  {catalogue.counts()}")
        print(f"  errors:  {len(catalogue.errors)}")
        for error in catalogue.errors[:20]:
            print(f"    - {error}")
        if args.report:
            for row in catalogue.list_rows():
                print(
                    f"  {row.level:10} {row.name:40} "
                    f"{row.department[:36]}"
                )
    return 1 if catalogue.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
