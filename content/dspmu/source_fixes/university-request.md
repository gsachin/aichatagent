# Request to DSPMU: stable notice URLs and machine-readable access

**To:** Registrar / Web team / Controller of Examinations
**Subject:** DSPMU public notices — stable PDF URLs, a notice feed and written
crawling permission

Dear Sir/Madam,

We are preparing an admissions assistant that answers questions about DSPMU
using the university's own published pages. We would like to request a small set
of source-side improvements so that callers are sent to the correct, current
document rather than to a broken link.

## What we found

- The public archive at `https://dspmuranchi.ac.in/archive-notice` lists 4,289 PDF links.
- Current links of the form `https://dspmuranchi.ac.in/pdf/<basename>` return an HTML page
  with HTTP 200, not the PDF.
- The same files are available on the old host at
  `https://oldsite.dspmuranchi.ac.in/pdf/News/<basename>`.
- The empty fee and examination-notice CMS pages could carry the public
  information that is currently available only inside PDFs.

## Requested changes

1. **Stable PDF URLs.** Serve each notice at one canonical, permanent URL on
   `https://dspmuranchi.ac.in`, preferably with no spaces in the path and with
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
   `https://dspmuranchi.ac.in/llms.txt` and `https://dspmuranchi.ac.in/notices.json` gives automated
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

- University phone: +91 930 455 261
- University email: registrar@dspmuranchi.ac.in
- University address: P.O. - Ranchi University, Morabadi, Ranchi - 834008, Jharkhand

We are happy to provide the generated files, a redirect test report or a short
call to walk through the changes. Thank you for considering the request.

Yours faithfully,

[Name]
[Role]
[Email]
[Phone]
