"""Lexical search and deterministic ranking over the navigation graph.

No embeddings, no vector store, no similarity top-k. Query normalization maps
Hindi/Gujarati topics to the same English graph vocabulary; SQLite FTS5
filters candidates; the ranker combines title, navigation label, aliases,
path, section, content type and authority. Body text is not indexed yet
(Phase 3), so no body score is applied.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.sitegraph.models import Authority, ContentType, NodeStatus, SiteNode
from app.sitegraph.policy import can_ground, can_navigate
from app.sitegraph.store import GraphStore

#: Hindi/Gujarati topic words -> English graph vocabulary. The site's slugs and
#: titles are English; without this a Devanagari query scores zero.
_ALIASES: dict[str, str] = {
    # Hindi
    "फीस": "fee", "शुल्क": "fee", "पाठ्यक्रम": "syllabus", "सिलेबस": "syllabus",
    "प्रवेश": "admission", "एडमिशन": "admission", "संपर्क": "contact",
    "फोन": "contact", "ईमेल": "contact", "पता": "contact", "छात्रावास": "hostel",
    "पुस्तकालय": "library", "परीक्षा": "examination", "परिणाम": "result",
    "अवकाश": "holiday", "छुट्टी": "holiday", "छात्रवृत्ति": "scholarship",
    "प्लेसमेंट": "placement", "नौकरी": "career", "सुविधा": "facilities",
    "कैंपस": "campus", "परिसर": "campus", "इतिहास": "history",
    "दृष्टि": "vision", "लक्ष्य": "mission", "संकाय": "faculty",
    "विभाग": "department", "खेल": "sports", "पुस्तक": "library",
    # Abbreviations used in the site's own navigation.
    "phd": "ph d", "ph.d": "ph d", "ph.d.": "ph d", "vc": "vice chancellor",
    # Gujarati
    "ફી": "fee", "શુલ્ક": "fee", "અભ્યાસક્રમ": "syllabus", "પ્રવેશ": "admission",
    "સંપર્ક": "contact", "ફોન": "contact", "ઈમેલ": "contact", "સરનામું": "contact",
    "છાત્રાલય": "hostel", "ગ્રંથાલય": "library", "પરીક્ષા": "examination",
    "પરિણામ": "result", "રજા": "holiday", "શિષ્યવૃત્તિ": "scholarship",
    "કારકિર્દી": "career", "રમત": "sports", "સુવિધા": "facilities",
    "કેમ્પસ": "campus", "ઇતિહાસ": "history", "દ્રષ્ટિ": "vision",
    "વિભાગ": "department", "ફેકલ્ટી": "faculty",
}
_ALIAS_KEYS = tuple(sorted(_ALIASES, key=len, reverse=True))

_STOPWORDS = frozenset(
    "a an the is are was were be been being do does did can could would should "
    "i me my we our you your it this that these those of to in on at for from "
    "with by and or not no yes what which when where who whom how why "
    "please tell show give any some latest new information details detail "
    "dspmu ranchi jharkhand official website page site cms online "
    "current available need want know".split()
)
#: Generic tokens that should not outrank a specific topic. They are dropped
#: only when the query has at least one specific token left; a bare
#: "university" query still searches for the word.
_GENERIC_TOKENS = frozenset({"university", "universities", "upcoming"})
_TOKEN_RE = re.compile(r"[a-z0-9ऀ-ॿ઀-૿]{2,}")


@dataclass(frozen=True)
class NormalizedQuery:
    raw: str
    text: str
    tokens: tuple[str, ...]
    language: str


@dataclass(frozen=True)
class SearchHit:
    node: SiteNode
    score: float
    rank: float
    body_match: bool = False


def _language(text: str) -> str:
    if re.search(r"[ऀ-ॿ]", text):
        return "hi"
    if re.search(r"[઀-૿]", text):
        return "gu"
    return "en"


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(_TOKEN_RE.findall((text or "").lower()))


_TOKEN_REPLACEMENTS = {
    "scheduled": "schedule",
    "schedules": "schedule",
    "exams": "exam",
    "examinations": "examination",
    "timetables": "timetable",
    "timetabled": "timetable",
}


def normalize_query(query: str) -> NormalizedQuery:
    """Lowercase, map topic aliases, remove filler and produce FTS tokens."""
    raw = query or ""
    text = unicodedata.normalize("NFKC", raw).lower()
    for source in _ALIAS_KEYS:
        if source in text:
            text = text.replace(source, f" {_ALIASES[source]} ")
    tokens = tuple(
        _TOKEN_REPLACEMENTS.get(token, token)
        for token in _TOKEN_RE.findall(text)
        if token not in _STOPWORDS
    )
    # "university" and "upcoming" are useful context but poor ranking terms.
    # Drop them only when a specific topic remains, so a bare "university"
    # query still searches for the word.
    specific = tuple(
        token for token in tokens if token not in _GENERIC_TOKENS
    )
    if specific:
        tokens = specific
    return NormalizedQuery(
        raw=raw,
        text=" ".join(tokens),
        tokens=tuple(dict.fromkeys(tokens)),
        language=_language(raw),
    )


def _fts_match(tokens: tuple[str, ...]) -> str:
    parts = []
    for token in tokens:
        escaped = token.replace('"', '""')
        parts.append(f'"{escaped}"')
    return " OR ".join(parts)


def build_fts_match(tokens: tuple[str, ...]) -> str:
    """Public form of the FTS5 OR query used by chunk retrieval."""
    return _fts_match(tokens)


def _overlap(query_tokens: set[str], field_tokens: set[str]) -> float:
    if not query_tokens or not field_tokens:
        return 0.0
    return len(query_tokens & field_tokens) / len(query_tokens)


def _content_bonus(node: SiteNode) -> float:
    # A shell is a route, not content. Keep it searchable but do not let its
    # title outrank a real page purely because it is HTML.
    if node.status is NodeStatus.EMPTY_SHELL:
        return 1.0
    if node.content_type is ContentType.HTML:
        return 2.5
    if node.content_type is ContentType.SECTION:
        return 1.5
    if node.content_type is ContentType.PDF:
        return 0.5
    return 0.0


def _authority_bonus(authority: Authority) -> float:
    if authority is Authority.VERIFIED_FACT:
        return 2.0
    if authority is Authority.OFFICIAL_PAGE:
        return 1.0
    if authority is Authority.OFFICIAL_DOCUMENT:
        return 0.5
    if authority is Authority.DERIVED:
        return 0.0
    return 0.0


def node_matches_level(node: SiteNode, level: str | None) -> bool:
    """True when a node is not known to be the opposite education level."""
    if not level:
        return True
    try:
        from app.programs import detect_program_mentions

        levels = {
            mention.level
            for mention in detect_program_mentions(
                f"{node.title} {node.nav_label}"
            )
        }
    except Exception:  # noqa: BLE001 - level filtering must not drop search
        return True
    return not levels or level in levels


def _score(
    node: SiteNode,
    normalized: NormalizedQuery,
    rank: float,
    *,
    body_match: bool = False,
) -> float:
    query_tokens = set(normalized.tokens)
    title = node.title.lower()
    nav = node.nav_label.lower()
    aliases = [alias.lower() for alias in node.aliases]
    title_tokens = set(_tokens(title))
    nav_tokens = set(_tokens(nav))
    alias_tokens: set[str] = set()
    for alias in aliases:
        alias_tokens.update(_tokens(alias))
    path_tokens = set(_tokens(node.canonical_path))
    section_tokens = set(_tokens(node.section))

    exact = 0.0
    if normalized.text and normalized.text == title:
        exact += 5.0
    if normalized.text and normalized.text == nav:
        exact += 4.0
    if normalized.text and normalized.text in aliases:
        exact += 4.0
    term = (
        3.0 * _overlap(query_tokens, title_tokens)
        + 2.0 * _overlap(query_tokens, nav_tokens)
        + 2.0 * _overlap(query_tokens, alias_tokens)
        + 1.5 * _overlap(query_tokens, path_tokens)
        + 1.0 * _overlap(query_tokens, section_tokens)
    )
    # A candidate with no term match and no FTS signal is not a result. The
    # content/authority bonuses must never turn an unrelated node into a hit.
    if exact == 0.0 and term == 0.0 and rank >= 0.0:
        return 0.0
    score = exact + term
    score += _content_bonus(node)
    score += _authority_bonus(node.authority)
    # bm25 is lower-is-better; cap the positive contribution.
    score += min(2.0, max(0.0, -rank) * 0.25)
    if body_match:
        score += 1.5
    score -= min(0.5, 0.05 * node.depth)
    return round(score, 4)


def search_nodes(
    store: GraphStore,
    query: str,
    *,
    section: str | None = None,
    content_type: str | None = None,
    limit: int = 10,
    pii_guard: bool = True,
    level: str | None = None,
) -> tuple[NormalizedQuery, list[SearchHit]]:
    """Rank graph nodes for *query*. No network and no page body."""
    normalized = normalize_query(query)
    limit = max(1, min(50, int(limit)))
    if not normalized.tokens:
        return normalized, []

    match = _fts_match(normalized.tokens)
    candidate_limit = max(20, limit * 5)
    candidates = store.search_fts(
        match,
        limit=candidate_limit,
        section=section,
        content_type=content_type,
        include_shells=True,
    )
    body_candidates = store.search_body_fts(
        match,
        limit=candidate_limit,
        section=section,
        content_type=content_type,
        include_shells=True,
    )

    merged: dict[str, tuple[SiteNode, float, bool]] = {}
    for node, rank in candidates:
        merged[node.node_id] = (node, rank, False)
    for node, rank in body_candidates:
        existing = merged.get(node.node_id)
        if existing is None:
            merged[node.node_id] = (node, rank, True)
        else:
            merged[node.node_id] = (node, min(existing[1], rank), True)

    hits: list[SearchHit] = []
    seen: set[str] = set()
    for node, rank, body_match in merged.values():
        if node.node_id in seen or not can_navigate(node, pii_guard):
            continue
        if not node_matches_level(node, level):
            continue
        seen.add(node.node_id)
        score = _score(node, normalized, rank, body_match=body_match)
        if score > 0:
            hits.append(
                SearchHit(
                    node=node, score=score, rank=rank, body_match=body_match
                )
            )

    if not hits:
        # FTS can miss a punctuation-heavy or alias-only query. The graph is
        # small; a bounded scan is cheaper than a bad empty answer.
        for node in store.load_nodes():
            if not can_navigate(node, pii_guard):
                continue
            if not node_matches_level(node, level):
                continue
            if section and node.section.lower() != section.lower():
                continue
            if content_type and node.content_type.value != content_type:
                continue
            score = _score(node, normalized, 0.0)
            if score > 0:
                hits.append(
                    SearchHit(node=node, score=score, rank=0.0, body_match=False)
                )

    hits.sort(key=lambda hit: (-hit.score, hit.node.title.lower(), hit.node.node_id))
    return normalized, hits[:limit]


def breadcrumb_for(store: GraphStore, node_id: str) -> list[dict]:
    return [
        {"node_id": node.node_id, "title": node.title}
        for node in store.breadcrumb(node_id)
    ]


def node_payload(
    store: GraphStore,
    node: SiteNode,
    *,
    pii_guard: bool = True,
) -> dict:
    content_status = {
        ContentType.HTML: "not_indexed",
        ContentType.SECTION: "section",
        ContentType.PDF: "metadata_only",
        ContentType.EXTERNAL: "external",
    }.get(node.content_type, "unknown")
    if node.status is NodeStatus.EMPTY_SHELL:
        content_status = "empty_shell"
    return {
        "node_id": node.node_id,
        "title": node.title,
        "nav_label": node.nav_label,
        "url": node.url,
        "canonical_path": node.canonical_path,
        "section": node.section,
        "depth": node.depth,
        "content_type": node.content_type.value,
        "authority": node.authority.value,
        "status": node.status.value,
        "pii_class": node.pii_class.value,
        "groundable": can_ground(node, pii_guard),
        "navigable": can_navigate(node, pii_guard),
        "lastmod": node.lastmod,
        "verified_at": node.verified_at,
        "canonical_id": node.canonical_id or node.node_id,
        "aliases": list(node.aliases),
        "languages": list(node.languages),
        "breadcrumb": breadcrumb_for(store, node.node_id),
        "content_status": content_status,
    }
