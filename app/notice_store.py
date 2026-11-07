"""SQLite FTS5 index for the DSPMU non-PII notice catalogue.

The long-tail notice archive is 1,560 rows. Scanning the JSONL on every notice
question works, but it cannot rank matches and it will not carry extracted PDF
text well. This module builds a small, rebuildable FTS5 database beside the
JSONL and exposes a read-only search used by ``app.site_router``.

The JSONL remains the source of truth. A missing, schema-mismatched or
unreadable database returns ``None`` from :func:`search_notices`; the router
then falls back to the old title scan, so the store can never make a turn fail.

Schema:
  notices       display metadata plus normalized search columns
  notices_fts   external-content FTS5 over title/category/body
  triggers      keep the index correct for insert/update/delete
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = 1
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_NOTICES_PATH = "content/dspmu/notices.jsonl"
DEFAULT_DB_PATH = "content/dspmu/notices.sqlite3"

#: Query words that add no notice signal. The router already removes more
#: conversational filler; this is the store's own safe floor.
_QUERY_STOPWORDS = frozenset(
    "a an the and or not no yes is are was were be been being do does did "
    "can could would should i me my we our you your it this that these those "
    "of to in on at for from with by about any some please tell show give "
    "latest new news notice notices notification information details detail "
    "programme program".split()
)

#: The JSONL filter is category/title based and still lets a few selection-list
#: titles through. The store is stricter: a list notice that names candidates
#: is not something a prompt should retrieve.
_LIKELY_PII_TITLE_RE = re.compile(
    r"\b(?:merit|selection|selected|provisional|seelction|waiting)\b"
    r"|\b(?:\d+(?:st|nd|rd|th)|first|second|third|final|revised)\b"
    r"[^\n]{0,40}\blist(?:\b|_)"
    r"|\blist(?:\b|_)[^\n]{0,50}"
    r"\b(?:students?|applicants?|candidates?|teachers?|voters?|bpl|"
    r"members?|guides?|scholars?|faculty|staff|representatives?|medalists?)\b"
    r"|\b(?:students?|applicants?|candidates?|voters?|bpl|members?|guides?|"
    r"scholars?|faculty|staff|representatives?|medalists?)\b[^\n]{0,50}\blist(?:\b|_)",
    re.IGNORECASE,
)

_SCHEMA = """
CREATE TABLE notices (
    id TEXT PRIMARY KEY,
    date TEXT NOT NULL DEFAULT '',
    date_text TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    pii INTEGER NOT NULL DEFAULT 0,
    body TEXT NOT NULL DEFAULT '',
    text_path TEXT,
    content_hash TEXT NOT NULL DEFAULT '',
    ocr INTEGER NOT NULL DEFAULT 0,
    needs_review INTEGER NOT NULL DEFAULT 0,
    title_norm TEXT NOT NULL DEFAULT '',
    category_norm TEXT NOT NULL DEFAULT '',
    body_norm TEXT NOT NULL DEFAULT ''
);

CREATE VIRTUAL TABLE notices_fts USING fts5(
    title_norm,
    category_norm,
    body_norm,
    content='notices',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER notices_ai AFTER INSERT ON notices BEGIN
    INSERT INTO notices_fts(rowid, title_norm, category_norm, body_norm)
    VALUES (new.rowid, new.title_norm, new.category_norm, new.body_norm);
END;

CREATE TRIGGER notices_ad AFTER DELETE ON notices BEGIN
    INSERT INTO notices_fts(notices_fts, rowid, title_norm, category_norm, body_norm)
    VALUES ('delete', old.rowid, old.title_norm, old.category_norm, old.body_norm);
END;

CREATE TRIGGER notices_au AFTER UPDATE ON notices BEGIN
    INSERT INTO notices_fts(notices_fts, rowid, title_norm, category_norm, body_norm)
    VALUES ('delete', old.rowid, old.title_norm, old.category_norm, old.body_norm);
    INSERT INTO notices_fts(rowid, title_norm, category_norm, body_norm)
    VALUES (new.rowid, new.title_norm, new.category_norm, new.body_norm);
END;

PRAGMA user_version = 1;
"""

_SEARCH_SQL = """
SELECT
    n.id,
    n.date,
    n.date_text,
    n.title,
    n.category,
    n.url,
    n.source,
    n.pii,
    bm25(notices_fts, 5.0, 1.0, 1.0) AS rank
FROM notices_fts
JOIN notices AS n ON n.rowid = notices_fts.rowid
WHERE notices_fts MATCH ?
  AND n.pii = 0
ORDER BY rank, n.date DESC
LIMIT ?
"""


class NoticeStoreError(RuntimeError):
    """The JSONL could not be converted into a valid notice store."""


class NoticeStoreUnavailable(NoticeStoreError):
    """This SQLite build has no FTS5 module."""


@dataclass(frozen=True)
class NoticeStoreStats:
    built: bool
    rows: int
    pii_excluded: int
    db_bytes: int
    body_rows: int
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return {
            "built": self.built,
            "rows": self.rows,
            "pii_excluded": self.pii_excluded,
            "db_bytes": self.db_bytes,
            "body_rows": self.body_rows,
            "schema_version": self.schema_version,
        }


def _resolve(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


def is_likely_pii_title(title: str) -> bool:
    """True for a title that names candidates or is a result/selection list."""
    return bool(_LIKELY_PII_TITLE_RE.search(title or ""))


def normalise(text: str) -> str:
    """The exact normalization used for both stored columns and queries.

    Removing dots before splitting is what makes "Ph.D." and "PhD" the same
    token, and it keeps the store's behavior aligned with the router's old
    title scan.
    """
    cleaned = (text or "").lower().replace(".", "")
    cleaned = re.sub(r"[^\w]+", " ", cleaned, flags=re.UNICODE)
    return " ".join(cleaned.split())


def _query_terms(query: str) -> list[str]:
    terms: list[str] = []
    for term in normalise(query).split():
        if term in _QUERY_STOPWORDS or len(term) < 2:
            continue
        if term not in terms:
            terms.append(term)
    return terms


def _match_expression(query: str) -> str:
    """A safe FTS5 MATCH expression: prefixes for 3+ chars, exact for 2."""
    parts = []
    for term in _query_terms(query)[:24]:
        # Terms come from normalise(), so they contain only word characters.
        parts.append(f"{term}*" if len(term) >= 3 else f'"{term}"')
    return " OR ".join(parts)


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    try:
        with path.open(encoding="utf-8") as fh:
            for line_number, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError as exc:
                    raise NoticeStoreError(
                        f"{path}:{line_number}: invalid JSON"
                    ) from exc
                if not isinstance(row, dict):
                    raise NoticeStoreError(
                        f"{path}:{line_number}: notice is not an object"
                    )
                notice_id = str(row.get("id") or "").strip()
                title = str(row.get("title") or "").strip()
                if not notice_id or not title:
                    raise NoticeStoreError(
                        f"{path}:{line_number}: notice needs id and title"
                    )
                if notice_id in seen:
                    raise NoticeStoreError(
                        f"{path}:{line_number}: duplicate notice id {notice_id}"
                    )
                seen.add(notice_id)
                rows.append(row)
    except OSError as exc:
        raise NoticeStoreError(f"cannot read {path}: {exc}") from exc
    return rows


def read_notices(path: str | Path = DEFAULT_NOTICES_PATH) -> list[dict]:
    """Read and validate the JSONL catalogue (the store's source of truth)."""
    return _read_jsonl(_resolve(path))


def _body_for(row: dict, body_dir: Path | None, db_dir: Path) -> tuple[str, str]:
    if body_dir is None:
        return "", ""
    path = body_dir / f"{row['id']}.txt"
    try:
        body = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return "", ""
    try:
        text_path = str(path.resolve().relative_to(db_dir.resolve()))
    except ValueError:
        text_path = str(path)
    return body, text_path


def build_store(
    notices_path: str | Path = DEFAULT_NOTICES_PATH,
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    body_dir: str | Path | None = None,
) -> NoticeStoreStats:
    """Build *db_path* atomically from the JSONL notice catalogue.

    ``body_dir`` is optional and is for text that has ALREADY passed the PII
    scan. This builder filters titles, not body text.
    """
    source = _resolve(notices_path)
    target = _resolve(db_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = _read_jsonl(source)

    tmp = target.with_name(f"{target.name}.tmp")
    if tmp.exists():
        tmp.unlink()

    pii_excluded = 0
    body_rows = 0
    body_root = _resolve(body_dir) if body_dir else None
    try:
        con = sqlite3.connect(tmp)
        try:
            con.execute("PRAGMA journal_mode=DELETE")
            con.execute("PRAGMA synchronous=OFF")
            con.executescript(_SCHEMA)
            for row in rows:
                title = str(row.get("title") or "").strip()
                pii = 1 if row.get("pii") else 0
                if pii or _LIKELY_PII_TITLE_RE.search(title):
                    pii_excluded += 1
                    continue
                body, text_path = _body_for(row, body_root, target.parent)
                if body:
                    body_rows += 1
                category = str(row.get("category") or "").strip()
                content_hash = hashlib.sha256(
                    "\n".join((title, category, body)).encode("utf-8")
                ).hexdigest()
                con.execute(
                    """
                    INSERT INTO notices (
                        id, date, date_text, title, category, url, source, pii,
                        body, text_path, content_hash, ocr, needs_review,
                        title_norm, category_norm, body_norm
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(row.get("id")),
                        str(row.get("date") or ""),
                        str(row.get("date_text") or ""),
                        title,
                        category,
                        str(row.get("url") or ""),
                        str(row.get("source") or ""),
                        pii,
                        body,
                        text_path or None,
                        content_hash,
                        int(bool(row.get("ocr"))),
                        int(bool(row.get("needs_review"))),
                        normalise(title),
                        normalise(category),
                        normalise(body),
                    ),
                )
            con.execute("INSERT INTO notices_fts(notices_fts) VALUES('optimize')")
            con.commit()
        finally:
            con.close()
        os.replace(tmp, target)
    except sqlite3.OperationalError as exc:
        tmp.unlink(missing_ok=True)
        if "fts5" in str(exc).lower() or "no such module" in str(exc).lower():
            raise NoticeStoreUnavailable(
                "SQLite was built without FTS5; the JSONL fallback remains usable"
            ) from exc
        raise NoticeStoreError(f"cannot build {target}: {exc}") from exc
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

    return NoticeStoreStats(
        built=True,
        rows=len(rows) - pii_excluded,
        pii_excluded=pii_excluded,
        db_bytes=target.stat().st_size,
        body_rows=body_rows,
    )


def _connect_readonly(path: Path) -> sqlite3.Connection:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def search_notices(
    query: str,
    *,
    limit: int = 5,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> list[dict] | None:
    """Search the FTS5 store.

    Returns ``None`` when the store is missing, schema-mismatched or
    unreadable; the caller should use its JSONL fallback. An empty query
    against a valid store returns ``[]``.
    """
    path = _resolve(db_path)
    if not path.is_file():
        return None
    expression = _match_expression(query)
    if not expression:
        return []
    bounded = max(0, min(50, int(limit)))
    if bounded == 0:
        return []
    try:
        con = _connect_readonly(path)
        try:
            version = con.execute("PRAGMA user_version").fetchone()
            if not version or int(version[0]) != SCHEMA_VERSION:
                return None
            rows = con.execute(_SEARCH_SQL, (expression, bounded)).fetchall()
        finally:
            con.close()
    except (sqlite3.Error, OSError):
        return None

    results: list[dict] = []
    for row in rows:
        title = str(row[3] or "")
        if _LIKELY_PII_TITLE_RE.search(title):
            continue
        results.append(
            {
                "id": row[0],
                "date": row[1],
                "date_text": row[2],
                "title": title,
                "category": row[4],
                "url": row[5],
                "source": row[6],
                "pii": bool(row[7]),
            }
        )
    return results
