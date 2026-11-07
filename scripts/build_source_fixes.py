#!/usr/bin/env python3
"""Build the DSPMU source-side handoff from committed facts and notices.

The output is a set of drafts for the university web team:

* ``llms.txt`` — a site-level index for AI retrieval
* ``notices.json`` — a JSON Feed 1.1 feed of public, non-PII notices
* ``redirects_non_pii.csv`` — exact current-to-legacy notice URLs
* redirect rules for nginx and Apache
* CMS-ready fee and examination-notice pages

Nothing here fetches the network and nothing here publishes itself.  Run with
``--check`` to verify the checked-in artifacts are current without writing.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.notice_store import is_likely_pii_title  # noqa: E402
from app.pii import scan_text  # noqa: E402

FACTS_PATH = ROOT / "content" / "dspmu" / "facts.json"
NOTICES_PATH = ROOT / "content" / "dspmu" / "notices.jsonl"
OUT_DIR = ROOT / "content" / "dspmu" / "source_fixes"

CURRENT_HOST = "https://dspmuranchi.ac.in"
LEGACY_HOST = "https://oldsite.dspmuranchi.ac.in"
LEGACY_PREFIX = "/pdf/News/"

#: Basenames that did not match the current archive snapshot on 2026-10-24.
#: They are old-site-only; all of their legacy targets were verified to return
#: a PDF.  They stay in the feed but are marked for review in the redirect CSV.
AUDIT_NEEDS_REVIEW = {
    "PhdApplnFee.pdf",
    "Examination_Schedule_MHA_Sem_IV 24-26.pdf",
    "Fee_submission_Notice_GIS sem 2.pdf",
    "EXAMINATION PROGRAMME visual arts sem VIII 2022-2026.pdf",
    "30.09.2026_Fee_submission Notice LLM Sem III.pdf",
    "EXAMINATION PROGRAMME Fish  Fisheries sem VIII 2022-2026.pdf",
    "Fee_submission Notice_MHA_sem_IV 24-26.pdf",
    "Examination_Schedule_GIS sem II.pdf",
}

# The current archive uses a lowercase extension for this one file.  Keep the
# redirect source on the URL the archive actually publishes; the legacy target
# keeps the uppercase name that the old host serves.
AUDIT_CURRENT_BASENAME = {"registration form.PDF": "registration form.pdf"}


def _load_facts() -> dict:
    return json.loads(FACTS_PATH.read_text(encoding="utf-8"))


def _public_notices() -> list[dict]:
    """Return non-PII notices with current and legacy URLs attached."""
    rows: list[dict] = []
    for line in NOTICES_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        title = row.get("title", "")
        if (
            row.get("pii")
            or is_likely_pii_title(title)
            or scan_text(title).flagged
        ):
            continue
        path = urlsplit(row["url"]).path
        if LEGACY_PREFIX not in path:
            raise ValueError(f"unexpected notice URL: {row['url']}")
        basename = unquote(path.split(LEGACY_PREFIX, 1)[1])
        row["_basename"] = basename
        current_basename = AUDIT_CURRENT_BASENAME.get(basename, basename)
        row["_current_url"] = (
            f"{CURRENT_HOST}/pdf/{quote(current_basename, safe='')}"
        )
        row["_legacy_url"] = (
            f"{LEGACY_HOST}{LEGACY_PREFIX}{quote(basename, safe='')}"
        )
        rows.append(row)
    rows.sort(key=lambda item: (item.get("date", ""), item.get("id", "")), reverse=True)
    return rows


def _md_escape(text: str) -> str:
    """Escape the Markdown table and link delimiters used in this handoff."""
    text = " ".join((text or "").split())
    return re.sub(r"([\\\[\]|])", r"\\\1", text)


def _md_link(text: str, url: str) -> str:
    return f"[{_md_escape(text)}](<{url.replace('>', '%3E')}>)"


def _render_llms(facts: dict) -> str:
    contact = facts["contacts"]
    return f"""# Dr. Shyama Prasad Mukherjee University (DSPMU)

> Official public pages for admissions, fees and examination notices at DSPMU, Ranchi.

## Start here

- [University home]({CURRENT_HOST}/)
- [About DSPMU]({CURRENT_HOST}/cms/about-the-university)
- [Admissions and merit lists]({contact['admission_page']})
- [Fee structure 2026–27 (PDF)]({facts['sources']['fee2026']['url']})
- [All notices]({CURRENT_HOST}/archive-notice)
- [Examination notices]({CURRENT_HOST}/cms/examination-notices)
- [Contact the university]({contact['contact_page']})

## Machine-readable

- [Notice feed (JSON Feed 1.1)]({CURRENT_HOST}/notices.json)
- [Sitemap]({CURRENT_HOST}/sitemap.xml)

## Contact

- Phone: {contact['phone']}
- Email: {contact['emails'][0]}
- Address: {contact['address']}

## Use

These pages are public. Automated clients should follow the site's
[robots.txt]({CURRENT_HOST}/robots.txt) and any written permission from the
university. This file is an index, not a grant of additional rights.
"""


def _render_feed(notices: list[dict]) -> str:
    items = []
    for row in notices:
        category = row.get("category") or "Miscellaneous"
        date = row.get("date") or ""
        published = f"{date}T00:00:00+05:30" if date else None
        scope = (
            "needs_review"
            if row["_basename"] in AUDIT_NEEDS_REVIEW
            else "current_archive"
        )
        summary = f"{category} notice dated {row.get('date_text') or date}."
        item = {
            "id": row["id"],
            "url": row["_current_url"],
            "title": " ".join(row["title"].split()),
            "summary": summary,
            "content_text": summary,
            "tags": [category],
            "_dspmu": {
                "category": category,
                "legacy_url": row["_legacy_url"],
                "source": row.get("source", ""),
                "scope": scope,
                "pii": False,
            },
        }
        if published:
            item["date_published"] = published
        items.append(item)

    feed = {
        "version": "https://jsonfeed.org/version/1.1",
        "title": "DSPMU public notices",
        "home_page_url": f"{CURRENT_HOST}/",
        "feed_url": f"{CURRENT_HOST}/notices.json",
        "description": (
            "Public notices from Dr. Shyama Prasad Mukherjee University, Ranchi. "
            "Result, merit, selection and other student-list notices are excluded."
        ),
        "language": "en-IN",
        "items": items,
    }
    return json.dumps(feed, ensure_ascii=False, indent=2) + "\n"


def _render_redirects(notices: list[dict]) -> str:
    """Render an exact redirect list, restricted to the non-PII feed scope."""
    from io import StringIO

    buffer = StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        ["source_url", "target_url", "status_code", "notice_id", "scope"]
    )
    for row in notices:
        scope = (
            "needs_review"
            if row["_basename"] in AUDIT_NEEDS_REVIEW
            else "current_archive"
        )
        writer.writerow(
            [row["_current_url"], row["_legacy_url"], "301", row["id"], scope]
        )
    return buffer.getvalue()


def _render_nginx() -> str:
    return """# Draft DSPMU source-side bridge.
# Apply only after the PII review described in README.md.
# Current /pdf/<file> links return an HTML soft-404; the old host serves the PDF.
# The negative lookahead avoids rewriting paths already using /pdf/News/.
rewrite ^/pdf/(?!News/)(.+)$ https://oldsite.dspmuranchi.ac.in/pdf/News/$1 permanent;
"""


def _render_apache() -> str:
    return """# Draft DSPMU source-side bridge.
# Apply only after the PII review described in README.md.
# Current /pdf/<file> links return an HTML soft-404; the old host serves the PDF.
RedirectMatch 301 "^/pdf/(?!News/)(.+)$" "https://oldsite.dspmuranchi.ac.in/pdf/News/$1"
"""


def _fee_rows(facts: dict) -> list[dict]:
    return sorted(facts["fees"], key=lambda row: row["programme"].lower())


def _rupees(value: object) -> str:
    if value is None:
        return "—"
    return f"₹{int(value):,}"


def _render_fee_page(facts: dict) -> str:
    fees = _fee_rows(facts)
    source = facts["sources"]["fee2026"]["url"]
    rows = []
    for row in fees:
        rows.append(
            "| {programme} | {gen} | {reserved} | {semester} |".format(
                programme=_md_escape(row["programme"]),
                gen=_rupees(row.get("annual_fee_gen")),
                reserved=_rupees(row.get("annual_fee_reserved")),
                semester=_rupees(row.get("semester_course_fee_gen")),
            )
        )
    return f"""# Fee Structure 2026–27

The table below reproduces the university's official fee sheet for the 2026–27
academic year. The sheet lists an annual fee for each programme and, for some
programmes, a semester course fee.

- **Source:** {_md_link('Fee Structure for the Academic Year 2026 (PDF)', source)}
- **Valid:** 1 July 2026 to 30 June 2027
- **Programmes listed:** {len(fees)}
- **Contact:** {facts['contacts']['phone']} · {facts['contacts']['emails'][0]}

| Programme | Annual fee (GEN) | Annual fee (ST/SC/BC I & II) | Semester course fee (GEN) |
|---|---:|---:|---:|
{chr(10).join(rows)}

The official PDF is the authority. Confirm the total payable, including any
examination or other charges, with the university before making a payment.
"""


def _render_exam_page(notices: list[dict], limit: int = 40) -> str:
    exams = [row for row in notices if row.get("category") == "Examination"][:limit]
    rows = []
    for row in exams:
        rows.append(
            "| {date} | {link} |".format(
                date=_md_escape(row.get("date_text") or row.get("date") or ""),
                link=_md_link(row["title"], row["_current_url"]),
            )
        )
    return f"""# Examination Notices

Official examination notices, newest first. This page lists the latest
{len(exams)} public examination notices and excludes result, merit, selection
and other student-list notices.

| Date | Notice |
|---|---|
{chr(10).join(rows)}

- [All examination notices]({CURRENT_HOST}/news/examination)
- [Complete notice archive]({CURRENT_HOST}/archive-notice)
- [Machine-readable notice feed]({CURRENT_HOST}/notices.json)

For examination fees, schedules, admit cards and result notices, always confirm
against the linked PDF.
"""


def _render_readme(facts: dict, notices: list[dict]) -> str:
    total_rows = sum(
        1 for line in NOTICES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()
    )
    excluded = total_rows - len(notices)
    return f"""# DSPMU source-side fixes — draft handoff

**Draft for university review; not published.** This directory contains the
source-side changes that make DSPMU's own notice pages easier to use and easier
to ground an admissions assistant on. Everything here is generated from
committed DSPMU facts and non-PII notice metadata.

## What is here

| File | Purpose |
|---|---|
| `llms.txt` | Draft site index to publish at `{CURRENT_HOST}/llms.txt`. |
| `notices.json` | JSON Feed 1.1 of {len(notices):,} public, non-PII notices; each item carries `_dspmu.scope`. |
| `redirects_non_pii.csv` | Public notice redirects with a `scope` column: 1,512 `current_archive`, 8 `needs_review` (legacy-only). |
| `redirect-rule.nginx.conf` | One-rule nginx bridge for all `/pdf/` paths. |
| `redirect-rule.apache.conf` | Apache equivalent. |
| `cms/fee-structure.md` | CMS-ready replacement for the empty fee page. |
| `cms/examination-notices.md` | CMS-ready replacement for the empty examination-notices page. |
| `university-request.md` | Copy-ready request for stable URLs, an export and permission. |

## Audit, 24 October 2026

- The current archive page has **4,289 PDF links** covering **4,284 unique
  basenames**.
- Current-host links of the form `{CURRENT_HOST}/pdf/<basename>` return
  **HTTP 200 `text/html` soft-404s**, not PDFs.
- The old host serves the same files at
  `{LEGACY_HOST}/pdf/News/<basename>` as real PDFs. A sample of five
  current-only basenames also resolved there.
- The old and current archives differ at the edges: **7 current-only** and
  **28 old-only** basenames in the comparison. These need a filename-level
  review, not a blind rewrite.
- Of the **{len(notices):,} public notices** in the feed, **1,512** match a
  current archive basename after normalizing one filename's extension case; **8**
  are legacy-only. The legacy target resolves for all eight. The generic rewrite
  supplies their canonical current path; filter the CSV to `scope=current_archive`
  if a strict current-archive scope is required.
- A further **{excluded} titles** were excluded by the stricter title guard and
  the text-level PII scan because they are merit, selection, result, registration
  or other student-list material.
- Do not mass-fetch the 4,289 PDFs. The redirect and feed can be tested with a
  small sample after written permission.

## Deploy in this order

1. **Review the content.** The university web team and the registrar should
   approve the fee table, notice selection and wording.
2. **Apply the non-PII redirects first.** Use `redirects_non_pii.csv` for an
   exact, auditable scope. The generic nginx/Apache rule covers every PDF,
   including result and selection lists, so apply it only after the PII review
   described below.
3. **Publish the feed and index together.** Put `notices.json` at
   `{CURRENT_HOST}/notices.json` and `llms.txt` at
   `{CURRENT_HOST}/llms.txt`, then verify both return the new content rather
   than the CMS HTML shell.
4. **Paste the CMS drafts.** Replace the empty
   `{CURRENT_HOST}/cms/fee-structure` and
   `{CURRENT_HOST}/cms/examination-notices` bodies with the two files in
   `cms/`.
5. **Verify sample links.** Check a spread of recent and old notices, plus the
   five current-only samples, and confirm each returns `application/pdf` with
   the `%PDF-` magic bytes.

## PII boundary

`notices.json` and `redirects_non_pii.csv` exclude titles matching the project's
conservative student-list guard and text-level PII scan. They do not include
candidate names, roll or registration numbers, dates of birth, parent names,
Aadhaar/PAN data, bank details, or merit/selection/result lists.

This is a metadata gate, not document-level clearance. A linked PDF can still
contain a list even when its title does not announce one. The university should
complete a document-level PII review before publishing the feed or the generic
redirect rule.

The **generic** `/pdf/` rewrite would also make currently linked student-list
PDFs resolve. The university should remove or access-control those documents
before enabling it. Until then, use the exact non-PII CSV.

## University request

`university-request.md` is a copy-ready message; replace its four final
placeholders before sending. It asks for:

- stable PDF URLs and a CMS/database export;
- an owner and update cadence for notices;
- PII review and removal for result/merit/selection lists;
- written crawler/AI-use permission and rate limits;
- publication of `llms.txt` and `notices.json`.

## Regenerate

```bash
.venv/bin/python scripts/build_source_fixes.py
.venv/bin/python scripts/build_source_fixes.py --check
```

`--check` compares the checked-in files with the generated output and writes
nothing. The source data are `content/dspmu/facts.json` and
`content/dspmu/notices.jsonl`; the generator does not use the network.
"""


def _render_university_request(facts: dict, notices: list[dict]) -> str:
    return f"""# Request to DSPMU: stable notice URLs and machine-readable access

**To:** Registrar / Web team / Controller of Examinations
**Subject:** DSPMU public notices — stable PDF URLs, a notice feed and written
crawling permission

Dear Sir/Madam,

We are preparing an admissions assistant that answers questions about DSPMU
using the university's own published pages. We would like to request a small set
of source-side improvements so that callers are sent to the correct, current
document rather than to a broken link.

## What we found

- The public archive at `{CURRENT_HOST}/archive-notice` lists 4,289 PDF links.
- Current links of the form `{CURRENT_HOST}/pdf/<basename>` return an HTML page
  with HTTP 200, not the PDF.
- The same files are available on the old host at
  `{LEGACY_HOST}/pdf/News/<basename>`.
- The empty fee and examination-notice CMS pages could carry the public
  information that is currently available only inside PDFs.

## Requested changes

1. **Stable PDF URLs.** Serve each notice at one canonical, permanent URL on
   `{CURRENT_HOST}`, preferably with no spaces in the path and with
   `Content-Type: application/pdf`. If the files remain on the old host, add a
   301 redirect from the current `/pdf/<basename>` path. Include `ETag` or
   `Last-Modified` where possible.
2. **CMS or database export.** Provide a machine-readable export of public
   notice metadata with these fields:
   `id`, `title`, `date`, `category`, `canonical_pdf_url`, `published_status`,
   `pii_flag`, `last_modified`.
   A CSV, JSON or database view is sufficient.
3. **Publish the two draft files.** We have prepared `llms.txt` and
   `notices.json` in `content/dspmu/source_fixes/`. Publishing them at
   `{CURRENT_HOST}/llms.txt` and `{CURRENT_HOST}/notices.json` gives automated
   clients a stable index while keeping the official pages authoritative.
4. **Fill the empty pages.** The draft CMS pages in `cms/` reproduce the 2026
   fee sheet and the latest public examination notices. Please review them for
   accuracy before publishing.
5. **PII review.** Result, merit, selection and student-list notices should be
   removed from the public archive or access-controlled. The feed and exact
   redirect CSV cover only non-PII notices for this reason.
6. **Written permission and rate limits.** Please confirm in writing whether
   automated retrieval of public notice pages is permitted, and state a
   reasonable request rate and a contact for corrections. We will identify our
   crawler, respect `robots.txt`, and stop on request.

## Proposed update cadence

- New or changed notices: daily diff.
- Fee sheet and admission dates: checked against the official PDF before each
  admissions cycle.
- Corrections: within one working day of the university notifying us.

## Contact

- University phone: {facts['contacts']['phone']}
- University email: {facts['contacts']['emails'][0]}
- University address: {facts['contacts']['address']}

We are happy to provide the generated files, a redirect test report or a short
call to walk through the changes. Thank you for considering the request.

Yours faithfully,

[Name]
[Role]
[Email]
[Phone]
"""


def build_files() -> dict[Path, str]:
    facts = _load_facts()
    notices = _public_notices()
    return {
        OUT_DIR / "llms.txt": _render_llms(facts),
        OUT_DIR / "notices.json": _render_feed(notices),
        OUT_DIR / "redirects_non_pii.csv": _render_redirects(notices),
        OUT_DIR / "redirect-rule.nginx.conf": _render_nginx(),
        OUT_DIR / "redirect-rule.apache.conf": _render_apache(),
        OUT_DIR / "cms" / "fee-structure.md": _render_fee_page(facts),
        OUT_DIR / "cms" / "examination-notices.md": _render_exam_page(notices),
        OUT_DIR / "README.md": _render_readme(facts, notices),
        OUT_DIR / "university-request.md": _render_university_request(facts, notices),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify checked-in outputs without writing",
    )
    args = parser.parse_args()

    files = build_files()
    if args.check:
        stale = []
        for path, content in files.items():
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                stale.append(path.relative_to(ROOT))
        if stale:
            print("stale source-fix files:")
            for path in stale:
                print(f"  {path}")
            return 1
        print(f"source-fix files are current ({len(files)} files)")
        return 0

    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    print(f"wrote {len(files)} source-fix files to {OUT_DIR.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
