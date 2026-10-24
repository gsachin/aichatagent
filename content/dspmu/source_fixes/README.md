# DSPMU source-side fixes — draft handoff

**Draft for university review; not published.** This directory contains the
source-side changes that make DSPMU's own notice pages easier to use and easier
to ground an admissions assistant on. Everything here is generated from
committed DSPMU facts and non-PII notice metadata.

## What is here

| File | Purpose |
|---|---|
| `llms.txt` | Draft site index to publish at `https://dspmuranchi.ac.in/llms.txt`. |
| `notices.json` | JSON Feed 1.1 of 1,520 public, non-PII notices; each item carries `_dspmu.scope`. |
| `redirects_non_pii.csv` | Public notice redirects with a `scope` column: 1,512 `current_archive`, 8 `needs_review` (legacy-only). |
| `redirect-rule.nginx.conf` | One-rule nginx bridge for all `/pdf/` paths. |
| `redirect-rule.apache.conf` | Apache equivalent. |
| `cms/fee-structure.md` | CMS-ready replacement for the empty fee page. |
| `cms/examination-notices.md` | CMS-ready replacement for the empty examination-notices page. |
| `university-request.md` | Copy-ready request for stable URLs, an export and permission. |

## Audit, 24 October 2026

- The current archive page has **4,289 PDF links** covering **4,284 unique
  basenames**.
- Current-host links of the form `https://dspmuranchi.ac.in/pdf/<basename>` return
  **HTTP 200 `text/html` soft-404s**, not PDFs.
- The old host serves the same files at
  `https://oldsite.dspmuranchi.ac.in/pdf/News/<basename>` as real PDFs. A sample of five
  current-only basenames also resolved there.
- The old and current archives differ at the edges: **7 current-only** and
  **28 old-only** basenames in the comparison. These need a filename-level
  review, not a blind rewrite.
- Of the **1,520 public notices** in the feed, **1,512** match a
  current archive basename after normalizing one filename's extension case; **8**
  are legacy-only. The legacy target resolves for all eight. The generic rewrite
  supplies their canonical current path; filter the CSV to `scope=current_archive`
  if a strict current-archive scope is required.
- A further **40 titles** were excluded by the stricter title guard and
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
   `https://dspmuranchi.ac.in/notices.json` and `llms.txt` at
   `https://dspmuranchi.ac.in/llms.txt`, then verify both return the new content rather
   than the CMS HTML shell.
4. **Paste the CMS drafts.** Replace the empty
   `https://dspmuranchi.ac.in/cms/fee-structure` and
   `https://dspmuranchi.ac.in/cms/examination-notices` bodies with the two files in
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
