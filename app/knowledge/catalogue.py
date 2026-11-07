"""Programme and department catalogue.

The catalogue is the structured answer to "which programmes are offered" and
the level-aware entity source for programme claims. It is built from two
public, source-backed inputs:

* the 2026 UG fee sheet (``content/dspmu/facts.json``);
* the department pages' syllabus/document sections (``nga_graph.sqlite3``).

Rows are never invented. A row is marked ``draft`` or ``page_derived`` until a
named reviewer signs it off, and the answer text says so.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("knowledge.catalogue")

DEFAULT_PATH = (
    Path(__file__).resolve().parents[2] / "content" / "dspmu" / "programmes.json"
)

LEVELS = ("UG", "PG", "PhD", "Diploma", "Certificate")
_LEVEL_ORDER = {level: index for index, level in enumerate(LEVELS)}
_LEVEL_LABEL = {
    "UG": "Undergraduate (UG)",
    "PG": "Postgraduate (PG)",
    "PhD": "Doctoral (PhD)",
    "Diploma": "Diploma",
    "Certificate": "Certificate",
}
_LEVEL_MARKERS = {
    "PG": re.compile(
        r"\b(?:pg|post\s?graduate|postgraduate|msc|ma|mcom|mba|mca|"
        r"llm|med|mha)\b"
    ),
    "UG": re.compile(
        r"\b(?:ug|under\s?graduate|undergraduate|bsc|ba|bcom|bba|bca|"
        r"bed|bfsc|bjmc)\b"
    ),
    "PhD": re.compile(r"\b(?:phd|doctorate|doctoral)\b"),
    "Diploma": re.compile(r"\bdiploma\b"),
    "Certificate": re.compile(r"\bcertificate\b"),
}

#: A bare degree ("M.Sc", "MA", "BA") identifies a level, not a programme.
#: Keeping it in the alias index makes "M.Sc IT" match the first M.Sc row.
#: The exception is a degree whose canonical name is the programme itself
#: (MBA, MCA, LL.M, M.Com, BBA, BCA, B.Ed, B.F.Sc., Ph.D.).
_BARE_DEGREE_ALIASES = frozenset({
    "ma", "msc", "mcom", "ba", "bsc", "bcom", "mba", "mca", "llm",
    "mha", "bed", "bba", "bca", "bfsc", "phd", "med",
})


def _norm(value: str) -> str:
    """Normalise a name for alias matching.

    Dots are removed so ``M.Sc.`` / ``M Sc`` / ``msc`` match; ``&`` becomes
    ``and`` so the site's two spellings match; punctuation becomes spaces.
    """
    text = (value or "").lower().replace("&", " and ")
    text = text.replace(".", "")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _level_from_text(text: str) -> str | None:
    value = _norm(text)
    for level, pattern in _LEVEL_MARKERS.items():
        if pattern.search(value):
            return level
    return None


@dataclass(frozen=True)
class Programme:
    id: str
    name: str
    level: str
    full_name: str = ""
    degree: str = ""
    subject: str = ""
    department: str = ""
    page_url: str = ""
    document_urls: tuple[str, ...] = ()
    fee_fact_id: str = ""
    source: str = ""
    source_urls: tuple[str, ...] = ()
    verified_at: str | None = None
    status: str = "draft"
    reviewed_by: str | None = None
    aliases: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: dict) -> "Programme":
        level = str(data.get("level") or "").strip()
        if level not in LEVELS:
            raise ValueError(f"unknown level {level!r}")
        name = str(data.get("name") or "").strip()
        if not name:
            raise ValueError("programme name is required")
        identifier = str(data.get("id") or "").strip()
        if not identifier:
            identifier = f"{level.lower()}-{_norm(name).replace(' ', '-')}"
        return cls(
            id=identifier,
            name=name,
            level=level,
            full_name=str(data.get("full_name") or "").strip(),
            degree=str(data.get("degree") or "").strip(),
            subject=str(data.get("subject") or "").strip(),
            department=str(data.get("department") or "").strip(),
            page_url=str(data.get("page_url") or "").strip(),
            document_urls=tuple(
                str(item).strip()
                for item in (data.get("document_urls") or ())
                if str(item).strip()
            ),
            fee_fact_id=str(data.get("fee_fact_id") or "").strip(),
            source=str(data.get("source") or "").strip(),
            source_urls=tuple(
                str(item).strip()
                for item in (data.get("source_urls") or ())
                if str(item).strip()
            ),
            verified_at=(
                str(data.get("verified_at")).strip()
                if data.get("verified_at") else None
            ),
            status=str(data.get("status") or "draft").strip().lower(),
            reviewed_by=(
                str(data.get("reviewed_by")).strip()
                if data.get("reviewed_by") else None
            ),
            aliases=tuple(
                str(item).strip()
                for item in (data.get("aliases") or ())
                if str(item).strip()
            ),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "full_name": self.full_name,
            "level": self.level,
            "degree": self.degree,
            "subject": self.subject,
            "department": self.department,
            "page_url": self.page_url,
            "document_urls": list(self.document_urls),
            "fee_fact_id": self.fee_fact_id,
            "source": self.source,
            "source_urls": list(self.source_urls),
            "verified_at": self.verified_at,
            "status": self.status,
            "reviewed_by": self.reviewed_by,
            "aliases": list(self.aliases),
        }

    @property
    def labels(self) -> tuple[str, ...]:
        values = [
            self.name,
            self.full_name,
            f"{self.degree} {self.subject}".strip(),
            self.subject,
        ]
        return tuple(value for value in values if value)


@dataclass(frozen=True)
class ProgrammeMatch:
    programme: Programme
    alias: str
    confidence: float

    def to_dict(self) -> dict:
        return {
            "id": self.programme.id,
            "name": self.programme.name,
            "level": self.programme.level,
            "alias": self.alias,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class Catalogue:
    rows: tuple[Programme, ...] = ()
    metadata: dict = field(default_factory=dict)
    errors: tuple[str, ...] = ()
    _aliases: tuple[tuple[str, Programme], ...] = field(
        default=(), init=False, repr=False
    )

    def __post_init__(self) -> None:
        self._aliases = self._build_aliases()

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Catalogue":
        target = Path(path) if path else DEFAULT_PATH
        if not target.exists():
            return cls(errors=(f"catalogue_missing:{target}",))
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("catalogue load failed: %s", exc)
            return cls(errors=(f"catalogue_invalid:{type(exc).__name__}",))

        raw_rows = payload.get("programmes") or payload.get("rows") or []
        rows: list[Programme] = []
        errors: list[str] = []
        seen: set[str] = set()
        for index, item in enumerate(raw_rows):
            try:
                row = Programme.from_dict(item)
            except (TypeError, ValueError) as exc:
                errors.append(f"row_{index}:{exc}")
                continue
            if row.id in seen:
                errors.append(f"duplicate_id:{row.id}")
                continue
            seen.add(row.id)
            rows.append(row)
        return cls(
            rows=tuple(rows),
            metadata=dict(payload.get("metadata") or {}),
            errors=tuple(errors),
        )

    def _build_aliases(self) -> tuple[tuple[str, Programme], ...]:
        subject_counts = Counter(
            _norm(row.subject) for row in self.rows if row.subject
        )
        entries: list[tuple[str, Programme]] = []
        seen: set[tuple[str, str]] = set()
        for row in self.rows:
            values = set(row.labels)
            values.update(row.aliases)
            for value in values:
                alias = _norm(value)
                if not alias:
                    continue
                # A bare subject that appears at more than one level cannot
                # identify a programme ("Information Technology" is both UG
                # and PG). The level-specific names remain.
                if alias == _norm(row.subject) and subject_counts[alias] > 1:
                    continue
                if (
                    alias in _BARE_DEGREE_ALIASES
                    and _norm(row.name) != alias
                    and not (row.level == "PhD" and alias == "phd")
                ):
                    continue
                key = (alias, row.id)
                if key in seen:
                    continue
                seen.add(key)
                entries.append((alias, row))
        entries.sort(key=lambda item: (-len(item[0]), item[0], item[1].id))
        return tuple(entries)

    def detect(self, text: str) -> ProgrammeMatch | None:
        """Return the best level-aware programme match for *text*."""
        value = _norm(text)
        if not value:
            return None
        level = _level_from_text(text)
        best: ProgrammeMatch | None = None
        for alias, row in self._aliases:
            if not re.search(
                rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", value
            ):
                continue
            score = float(len(alias))
            if level == row.level:
                score += 8.0
            elif level is not None:
                score -= 4.0
            if best is None or score > best.confidence:
                best = ProgrammeMatch(
                    programme=row, alias=alias, confidence=score
                )
        return best

    def for_level(self, level: str | None = None) -> tuple[Programme, ...]:
        if level:
            return tuple(row for row in self.rows if row.level == level)
        return self.rows

    def list_rows(
        self,
        level: str | None = None,
        *,
        include_draft: bool = True,
    ) -> tuple[Programme, ...]:
        rows = self.for_level(level)
        if not include_draft:
            rows = tuple(row for row in rows if row.status == "verified")
        return tuple(
            sorted(
                rows,
                key=lambda row: (
                    _LEVEL_ORDER.get(row.level, 99),
                    row.department.casefold(),
                    row.name.casefold(),
                ),
            )
        )

    def list_answer(
        self,
        level: str | None = None,
        *,
        include_draft: bool = True,
    ) -> tuple[str, tuple[dict, ...], tuple[Programme, ...]]:
        """Return a deterministic programme-list answer and citation dicts."""
        rows = self.list_rows(level, include_draft=include_draft)
        if not rows:
            return "", (), ()

        lines = [
            "DSPMU publishes these programmes on its public department pages "
            "and 2026 fee sheet:"
        ]
        for group_level in LEVELS:
            group = [row for row in rows if row.level == group_level]
            if not group:
                continue
            lines.append("")
            lines.append(
                f"**{_LEVEL_LABEL[group_level]} — {len(group)}**"
            )
            names = [row.name for row in group]
            # Keep the text compact: a few names per line.
            for start in range(0, len(names), 6):
                lines.append(", ".join(names[start:start + 6]))

        if any(row.status != "verified" for row in rows):
            lines.append("")
            lines.append(
                "These names are compiled from the public department pages "
                "and the 2026 fee sheet; the fee sheet is marked draft and "
                "review is pending. PG fees and seats are outside the "
                "verified fee profile; contact the registrar for those."
            )
        lines.append("")
        lines.append(
            "Official programme page: "
            "https://dspmuranchi.ac.in/cms/academic-programmes"
        )

        citations: list[dict] = []
        seen_urls: set[str] = set()
        for row in rows:
            url = row.page_url or (row.source_urls[0] if row.source_urls else "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            citations.append(
                {
                    "node_id": "",
                    "title": f"{row.level} · {row.name}",
                    "url": url,
                    "snippet": "Programme source",
                    "verified_at": row.verified_at,
                    "authority": (
                        "verified_fact" if row.fee_fact_id else "official_page"
                    ),
                    "fact_id": row.fee_fact_id or None,
                }
            )
            if len(citations) >= 8:
                break
        academic = "https://dspmuranchi.ac.in/cms/academic-programmes"
        if academic not in seen_urls:
            citations.append(
                {
                    "node_id": "cms.academic-programmes",
                    "title": "Academic Programmes",
                    "url": academic,
                    "snippet": "Official programme page",
                    "verified_at": None,
                    "authority": "official_page",
                    "fact_id": None,
                }
            )
        return "\n".join(lines).strip(), tuple(citations), rows

    def counts(self) -> dict[str, int]:
        return dict(Counter(row.level for row in self.rows))

    def validate(self) -> tuple[str, ...]:
        errors: list[str] = []
        if self.errors:
            errors.extend(self.errors)
        ids = Counter(row.id for row in self.rows)
        errors.extend(
            f"duplicate_id:{identifier}"
            for identifier, count in ids.items()
            if count > 1
        )
        for row in self.rows:
            if row.level not in LEVELS:
                errors.append(f"bad_level:{row.id}")
            if not row.name:
                errors.append(f"missing_name:{row.id}")
            if not row.source:
                errors.append(f"missing_source:{row.id}")
            if row.status not in (
                "verified", "draft", "page_derived", "needs_review"
            ):
                errors.append(f"bad_status:{row.id}:{row.status}")
        return tuple(errors)


__all__ = [
    "Catalogue",
    "DEFAULT_PATH",
    "LEVELS",
    "Programme",
    "ProgrammeMatch",
]
