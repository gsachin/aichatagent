#!/usr/bin/env python3
"""Attach substantive legacy DSPMU pages to empty current graph nodes.

Legacy content is stored as its own node with the old-site URL. The current
empty node keeps its identity and gets a ``links_to`` edge; citations always
point at the legacy URL, so old content is never presented as current. The
legacy text is PII-scanned with the same policy as a PDF before insertion.

Usage:
    .venv/bin/python scripts/nga_legacy.py --inventory /tmp/nga_legacy_inventory.json
    .venv/bin/python scripts/nga_legacy.py --inventory /tmp/nga_legacy_inventory.json --apply
    .venv/bin/python scripts/nga_legacy.py --allow-network --apply
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings  # noqa: E402
from app.pii import scan_text  # noqa: E402
from app.sitegraph.models import SiteNode  # noqa: E402
from app.sitegraph.store import GraphStore  # noqa: E402

LEGACY_BASE = "https://oldsite.dspmuranchi.ac.in/"
LEGACY_HOST = "oldsite.dspmuranchi.ac.in"
USER_AGENT = "DSPMU-NGA-Legacy/1.0 (+https://dspmuranchi.ac.in)"
FAR_FUTURE = "2099-01-01T00:00:00Z"


@dataclass(frozen=True)
class LegacySource:
    url: str
    title: str
    targets: tuple[str, ...]
    #: Scan reasons that were manually reviewed and are generic, not values.
    allowed_pii: tuple[str, ...] = ()


# Only high-confidence, substantive pages. Department pages are excluded:
# their legacy text is either already present on the new department pages or
# says "No Record Found". TermsAndConditions.aspx is omitted because the PII
# scan flags bank-account language.
LEGACY_SOURCES: tuple[LegacySource, ...] = (
    LegacySource(
        "https://oldsite.dspmuranchi.ac.in/AntiRaggingCell.aspx",
        "Anti-Ragging Policy",
        ("cms.anti-ragging-policy", "student-life.anti-ragging-policy"),
    ),
    LegacySource(
        "https://oldsite.dspmuranchi.ac.in/SGRC.aspx",
        "Grievance Redressal / SGRC",
        ("cms.grievance-redressal-rti", "cms.grievance-redressal-rti-1"),
    ),
    LegacySource(
        "https://oldsite.dspmuranchi.ac.in/Controller_of_Examination.aspx",
        "Controller of Examinations",
        ("cms.controller-of-examinations", "cms.examination"),
    ),
    LegacySource(
        "https://oldsite.dspmuranchi.ac.in/AsstRegistrarOffice.aspx",
        "Assistant Registrar",
        ("cms.assistant-registrar",),
    ),
    LegacySource(
        "https://oldsite.dspmuranchi.ac.in/DeanOffice.aspx",
        "Dean Office",
        ("cms.administration",),
    ),
    LegacySource(
        "https://oldsite.dspmuranchi.ac.in/DSWOffice.aspx",
        "DSW Office",
        ("cms.student-life",),
    ),
    LegacySource(
        "https://oldsite.dspmuranchi.ac.in/PlcementTrainingCell.aspx",
        "Training & Placement Cell",
        (
            "cms.training-placement",
            "cms.training-placement-careers",
            "cms.opportunities",
        ),
    ),
    LegacySource(
        "https://oldsite.dspmuranchi.ac.in/MajorProjects.aspx",
        "Major Research Projects",
        ("cms.research", "cms.sponsored-projects"),
    ),
    LegacySource(
        "https://oldsite.dspmuranchi.ac.in/GuidelineForGCForm.aspx",
        "Convocation Application",
        ("cms.convocation", "cms.3rd-convocation"),
    ),
    LegacySource(
        "https://oldsite.dspmuranchi.ac.in/AISHE.aspx",
        "AISHE",
        ("cms.aishe",),
    ),
    LegacySource(
        "https://oldsite.dspmuranchi.ac.in/RegistrarOffice.aspx",
        "Registrar Office",
        ("cms.administration",),
    ),
    LegacySource(
        "https://oldsite.dspmuranchi.ac.in/VC-Office.aspx",
        "Vice-Chancellor Office",
        ("cms.administration",),
    ),
    LegacySource(
        "https://oldsite.dspmuranchi.ac.in/Achievements.aspx",
        "Achievements",
        ("cms.activities",),
    ),
    LegacySource(
        "https://oldsite.dspmuranchi.ac.in/IDCEILC.aspx",
        "International Documentation Center",
        ("cms.research-collaborations", "cms.research-innovation"),
    ),
    LegacySource(
        "https://oldsite.dspmuranchi.ac.in/ombudsperson.aspx",
        "Ombudsperson",
        ("cms.grievance-redressal-rti", "cms.grievance-redressal-rti-1"),
    ),
    LegacySource(
        "https://oldsite.dspmuranchi.ac.in/StudentsUnionElection.aspx",
        "Students' Union",
        ("cms.student-life",),
    ),
    LegacySource(
        "https://oldsite.dspmuranchi.ac.in/TermsAndConditions.aspx",
        "Terms & Conditions",
        ("cms.terms-conditions",),
        # The only match is "use their parents' or their own bank account";
        # this is generic payment guidance, not an account number.
        ("bank_account",),
    ),
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clean(text: str) -> str:
    text = re.sub(r"[ \t\r\f\v]+", " ", text or "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract(html: str, url: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.select_one("div.content-heading")
    content = (
        heading.find_parent("div", class_="col-md-9") if heading else None
    )
    if content is None:
        content = soup.select_one("div.col-md-9")
    if content is None:
        return "", ""
    for tag in content.select(
        "script,style,nav,footer,form,iframe,noscript,button"
    ):
        tag.decompose()
    text = _clean(content.get_text("\n", strip=True))
    title_node = content.select_one("h1,h2,h3,h4")
    title = _clean(title_node.get_text(" ", strip=True)) if title_node else ""
    return title, text


def _fetch(session: requests.Session, url: str) -> tuple[int, str]:
    response = session.get(url, timeout=20)
    return response.status_code, response.text


def _legacy_id(url: str) -> str:
    return "legacy." + hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _insert_node(
    store: GraphStore,
    *,
    node_id: str,
    url: str,
    title: str,
    target: SiteNode,
    aliases: tuple[str, ...],
    now: str,
) -> None:
    parent_id = target.parent_id or target.node_id
    depth = target.depth if target.parent_id else target.depth + 1
    con = store._connect()
    try:
        con.execute("BEGIN")
        con.execute(
            """
            INSERT OR IGNORE INTO nga_node (
                node_id, url, canonical_path, parent_id, depth, section,
                title, nav_label, aliases, languages, content_type,
                authority, pii_class, status, lastmod, content_hash,
                fetched_at, verified_at, size_bytes, canonical_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '["en"]', 'html',
                      'official_page', 'public', 'empty_shell', ?, '', '', NULL,
                      0, ?)
            """,
            (
                node_id,
                url,
                urlparse(url).path or "/",
                parent_id,
                depth,
                target.section,
                title,
                title,
                json.dumps(list(aliases), ensure_ascii=False),
                now,
                node_id,
            ),
        )
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _insert_fts(
    store: GraphStore,
    *,
    node_id: str,
    title: str,
    aliases: tuple[str, ...],
    section: str,
    url: str,
) -> None:
    con = store._connect()
    try:
        con.execute(
            """
            INSERT OR REPLACE INTO nga_fts (
                node_id, title, nav_label, aliases, section, canonical_path
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                node_id,
                title,
                title,
                " ".join(aliases),
                section,
                urlparse(url).path or "/",
            ),
        )
        con.commit()
    finally:
        con.close()


def _link(
    store: GraphStore,
    *,
    source_id: str,
    legacy_id: str,
    anchor: str,
) -> None:
    con = store._connect()
    try:
        con.execute("BEGIN")
        ordinal = con.execute(
            "SELECT COALESCE(MAX(ordinal), -1) + 1 AS next FROM nga_edge "
            "WHERE src_id = ? AND kind = 'links_to'",
            (source_id,),
        ).fetchone()["next"]
        con.execute(
            """
            INSERT OR IGNORE INTO nga_edge (
                edge_id, src_id, dst_id, kind, anchor, ordinal
            ) VALUES (?, ?, ?, 'links_to', ?, ?)
            """,
            (
                f"legacy:{source_id}:{legacy_id}",
                source_id,
                legacy_id,
                anchor,
                int(ordinal),
            ),
        )
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _apply_source(
    store: GraphStore,
    source: LegacySource,
    *,
    text: str,
    legacy_title: str,
    min_chars: int,
) -> dict:
    scan = scan_text(text)
    blocked = tuple(
        reason for reason in scan.reasons if reason not in source.allowed_pii
    )
    if blocked:
        return {
            "url": source.url,
            "status": "pii_blocked",
            "reasons": list(blocked),
        }
    if len(text) < min_chars:
        return {
            "url": source.url,
            "status": "too_short",
            "chars": len(text),
        }

    targets: list[tuple[str, SiteNode]] = []
    missing: list[str] = []
    for target_id in source.targets:
        target = store.get_node(target_id)
        if target is None:
            missing.append(target_id)
        else:
            targets.append((target_id, target))
    if not targets:
        return {
            "url": source.url,
            "status": "no_targets",
            "missing": missing,
        }

    _, first = targets[0]
    legacy_id = _legacy_id(source.url)
    existing = store.get_node(legacy_id)
    previous = store.page_version(legacy_id) if existing else None
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    now = _now()
    display = f"{source.title} (Legacy)"
    aliases = tuple(
        dict.fromkeys(
            (
                source.title,
                legacy_title,
                "legacy",
                "old site",
                *(target.title for _, target in targets),
                *(target.nav_label for _, target in targets),
                *(target_id for target_id, _ in targets),
            )
        )
    )

    if not existing:
        _insert_node(
            store,
            node_id=legacy_id,
            url=source.url,
            title=display,
            target=first,
            aliases=aliases,
            now=now,
        )
    if previous is None or previous.content_hash != content_hash:
        store.record_page(
            legacy_id,
            text=(
                f"Official legacy page: {source.url}\n"
                "This page is no longer maintained on the current site; "
                "content may be outdated.\n\n"
                f"{text}"
            ),
            markdown=text,
            tables=(),
            sections=({"heading": source.title, "text": text},),
            links=(),
            documents=(),
            language="en",
            content_hash=content_hash,
            fetched_at=now,
            verified_at=now,
            size_bytes=len(text.encode("utf-8")),
            etag="",
            last_modified="",
            http_status=200,
            next_due_at=FAR_FUTURE,
        )
        _insert_fts(
            store,
            node_id=legacy_id,
            title=display,
            aliases=aliases,
            section=first.section,
            url=source.url,
        )
    for target_id, _target in targets:
        _link(
            store,
            source_id=target_id,
            legacy_id=legacy_id,
            anchor="Legacy official page",
        )

    return {
        "url": source.url,
        "status": "indexed",
        "legacy_node": legacy_id,
        "chars": len(text),
        "linked": [target_id for target_id, _ in targets],
        "missing": missing,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Attach official legacy pages to empty current nodes."
    )
    parser.add_argument("--db", default=settings.serve_db_path)
    parser.add_argument("--inventory", default=None)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--min-chars", type=int, default=120)
    parser.add_argument("--delay", type=float, default=0.25)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    store = GraphStore(args.db)
    inventory: dict[str, dict] | None = None
    if args.inventory:
        payload = json.loads(Path(args.inventory).read_text(encoding="utf-8"))
        inventory = {
            str(page.get("url") or ""): page
            for page in payload.get("pages") or []
            if isinstance(page, dict)
        }
    elif not args.allow_network:
        print(
            "error: pass --inventory or --allow-network",
            file=sys.stderr,
        )
        return 2

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    results: list[dict] = []
    for index, source in enumerate(LEGACY_SOURCES, 1):
        try:
            page = inventory.get(source.url) if inventory is not None else None
            if page is None:
                status, html = _fetch(session, source.url)
                if status != 200:
                    results.append(
                        {
                            "url": source.url,
                            "status": "fetch_failed",
                            "http_status": status,
                        }
                    )
                    continue
                legacy_title, text = _extract(html, source.url)
            else:
                legacy_title = str(page.get("title") or "")
                text = str(page.get("text") or "")
            result = {
                "url": source.url,
                "legacy_title": legacy_title,
                "preview": text[:160].replace("\n", " "),
                "apply": bool(args.apply),
            }
            if args.apply:
                result.update(
                    _apply_source(
                        store,
                        source,
                        text=text,
                        legacy_title=legacy_title,
                        min_chars=max(1, int(args.min_chars)),
                    )
                )
            results.append(result)
        except Exception as exc:  # noqa: BLE001 - one page must not stop the run
            results.append({"url": source.url, "status": "error", "error": str(exc)})
        if index < len(LEGACY_SOURCES):
            time.sleep(max(0.0, float(args.delay)))

    payload = {
        "checked": len(results),
        "applied": bool(args.apply),
        "sources": results,
    }
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"legacy sources checked: {len(results)}")
        for item in results:
            print(
                f"  {item.get('status', 'preview'):12} "
                f"{item['url']}  {item.get('chars', '')}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
