"""Read-only notice index for the Phase 2 tool server.

The graph carries pages and navigation; notices are a separate committed
JSONL produced by the existing DSPMU build. This module reads that file
without a network call and applies the same PII title guard as the notice
store. It is not a second retrieval system: ``site.latest`` is a tool over
the existing catalogue.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path

from app.notice_store import is_likely_pii_title

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_NOTICES_PATH = "content/dspmu/notices.jsonl"


@dataclass(frozen=True)
class Notice:
    id: str
    date: str
    date_text: str
    title: str
    category: str
    url: str
    source: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "date": self.date,
            "date_text": self.date_text,
            "title": self.title,
            "category": self.category,
            "url": self.url,
            "source": self.source,
        }


class NoticeIndex:
    """Load and filter the committed non-PII notice JSONL."""

    def __init__(
        self,
        path: str | Path | None = DEFAULT_NOTICES_PATH,
        *,
        pii_guard: bool = True,
    ) -> None:
        self.pii_guard = pii_guard
        if path is None:
            self.path: Path | None = None
        else:
            candidate = Path(path)
            self.path = (
                candidate if candidate.is_absolute()
                else PROJECT_ROOT / candidate
            )
        self._lock = threading.Lock()
        self._cache: tuple[tuple[int, int], tuple[Notice, ...]] | None = None

    @property
    def available(self) -> bool:
        return self.path is not None and self.path.is_file()

    def load(self) -> tuple[Notice, ...]:
        if self.path is None:
            return ()
        try:
            stat = self.path.stat()
        except OSError:
            return ()
        identity = (stat.st_mtime_ns, stat.st_size)
        with self._lock:
            if self._cache is not None and self._cache[0] == identity:
                return self._cache[1]

        rows: list[Notice] = []
        try:
            with self.path.open(encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(row, dict):
                        continue
                    title = str(row.get("title") or "").strip()
                    if not title:
                        continue
                    if self.pii_guard and (
                        row.get("pii") or is_likely_pii_title(title)
                    ):
                        continue
                    rows.append(
                        Notice(
                            id=str(row.get("id") or ""),
                            date=str(row.get("date") or ""),
                            date_text=str(row.get("date_text") or ""),
                            title=title,
                            category=str(row.get("category") or ""),
                            url=str(row.get("url") or ""),
                            source=str(row.get("source") or ""),
                        )
                    )
        except OSError:
            return ()

        rows.sort(
            key=lambda notice: (notice.date or "0000-00-00", notice.title.lower()),
            reverse=True,
        )
        result = tuple(rows)
        with self._lock:
            self._cache = (identity, result)
        return result

    def latest(
        self,
        *,
        since: str | None = None,
        limit: int = 10,
        categories: list[str] | None = None,
    ) -> list[Notice]:
        wanted = {category.strip().lower() for category in (categories or []) if category}
        rows = [
            notice for notice in self.load()
            if (not since or not notice.date or notice.date >= since)
            and (not wanted or notice.category.lower() in wanted)
        ]
        return rows[: max(1, min(50, int(limit)))]
