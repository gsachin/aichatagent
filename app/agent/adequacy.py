"""Evidence adequacy for the text answer path.

A non-empty bundle is not evidence. This module decides whether the retrieved
items are actually relevant enough to answer, or whether the planner must
explore (navigate, list, site search, deep search), clarify, or refuse.

The first version is deliberately lexical and conservative:

* query-specific terms are compared with the title, snippet and text of each
  evidence item;
* a short query must have every specific term somewhere in the bundle;
* a list/programme query must have a list-shaped source, not merely a page
  that happens to contain the word "programme";
* a time-bound query must have evidence dates that cover the requested period;
* verified facts count as strong evidence when they match the query or the
  intent is a fact intent (fee/contact).

It is a gate, not a ranker. The retrieval layer still decides which items to
return; this module decides whether the returned set is sufficient to answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from app.agent.evidence import EvidenceBundle, EvidenceItem
from app.agent.models import Intent
from app.agent.time_scope import parse_time_scope, scope_covers
from app.cache.answer_cache import extract_event_dates
from app.config import settings
from app.sitegraph.search import normalize_query

#: Tokens that carry little topical signal in a query. They are removed only
#: when something more specific remains, so a bare "list" still searches.
_QUERY_FILLER = frozenset({
    "currently", "current", "list", "all", "offer", "offered", "provide",
    "provided", "detail", "details", "information", "info", "show", "give",
    "tell", "about", "please", "kindly", "exact", "exactly", "available",
})

#: If a query contains one of these, it is asking for a set/list rather than a
#: single fact. A list answer needs a list-shaped source.
_LIST_QUERY_TERMS = frozenset({
    "program", "programme", "programmes", "programs",
    "course", "courses", "department", "departments",
    "list", "all", "offer", "offered",
})

#: A title is list-shaped when it names the catalogue/section, not when it
#: merely mentions a programme in prose.
_LIST_TITLE_TERMS = frozenset({
    "program", "programme", "programmes", "programs",
    "course", "courses", "department", "departments",
    "faculty", "faculties", "school", "academic", "syllabus",
    "graduate", "postgraduate", "undergraduate", "ug", "pg",
})

#: A snippet can carry the list shape when it uses one of these phrases.
_LIST_SNIPPET_PHRASES = (
    "course documents",
    "courses offered",
    "programmes offered",
    "programs offered",
    "list of programmes",
    "list of programs",
    "list of courses",
    "programme list",
    "program list",
    "syllabus & course",
    "syllabus and course",
)

_FACT_INTENTS = frozenset({Intent.FEE, Intent.CONTACT})

#: The site mixes "programme", "program" and "course". Retrieval may keep them
#: distinct for exact matching, but the adequacy gate treats them as one
#: concept so a courses page can answer a programme question.
_TOKEN_EQUIVALENTS = {
    "program": "programme",
    "programs": "programme",
    "programmes": "programme",
    "course": "programme",
    "courses": "programme",
    "examination": "exam",
    "examinations": "exam",
    "january": "jan",
    "february": "feb",
    "march": "mar",
    "april": "apr",
    "june": "jun",
    "july": "jul",
    "august": "aug",
    "september": "sep",
    "october": "oct",
    "november": "nov",
    "december": "dec",
}


def _canonical_token(token: str) -> str:
    return _TOKEN_EQUIVALENTS.get(token, token)


#: Action/meta words that add noise to the site's own search. "which programs
#: currently university" is a poor query; "programme" is the page the user
#: wants. The original query is still used for evidence adequacy and answer
#: wording.
_DEEP_SEARCH_FILLER = _QUERY_FILLER | frozenset({
    "check", "find", "search", "look", "lookup", "deep", "fetch", "get",
    "need", "want", "know", "show", "tell", "give", "provide", "can",
    "could", "would", "should", "please",
})


#: The site's own search ranks the plural "programmes" to the Academic
#: Programmes page; the singular "programme" ranks a Spandan schedule PDF
#: first. Keep the deep-search term plural.
_DEEP_SEARCH_TOKEN = {"programme": "programmes"}


def deep_search_query(query: str) -> str:
    """A compact site-search string for the deep-search fallback."""
    normalized = normalize_query(query)
    tokens = []
    for token in normalized.tokens:
        if token in _DEEP_SEARCH_FILLER:
            continue
        canonical = _canonical_token(token)
        tokens.append(_DEEP_SEARCH_TOKEN.get(canonical, canonical))
    return " ".join(tokens) or normalized.text or (query or "").strip()


@dataclass(frozen=True)
class Adequacy:
    """The result of the evidence sufficiency check."""

    adequate: bool
    score: float
    coverage: float
    reasons: tuple[str, ...]
    tokens: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "adequate": self.adequate,
            "score": round(self.score, 4),
            "coverage": round(self.coverage, 4),
            "reasons": list(self.reasons),
            "tokens": list(self.tokens),
        }


def _tokens(text: str) -> set[str]:
    return {
        _canonical_token(token)
        for token in normalize_query(text or "").tokens
    }


def _coverage(query: set[str], candidate: set[str]) -> float:
    if not query:
        return 0.0
    return len(query & candidate) / len(query)


def _specific_tokens(query: str) -> tuple[str, ...]:
    normalized = normalize_query(query)
    tokens = tuple(
        _canonical_token(token)
        for token in normalized.tokens
        if token not in _QUERY_FILLER
    )
    return tokens or tuple(
        _canonical_token(token) for token in normalized.tokens
    )


def _is_list_query(tokens: Iterable[str]) -> bool:
    return bool(set(tokens) & _LIST_QUERY_TERMS)


def _has_list_shape(items: Iterable[EvidenceItem]) -> bool:
    for item in items:
        title = (item.title or "").lower()
        snippet = (item.snippet or "").lower()
        if _tokens(title) & _LIST_TITLE_TERMS:
            return True
        if any(phrase in snippet for phrase in _LIST_SNIPPET_PHRASES):
            return True
    return False


def _item_score(
    query_tokens: set[str],
    item: EvidenceItem,
) -> tuple[float, set[str]]:
    title_tokens = _tokens(item.title)
    snippet_tokens = _tokens(item.snippet)
    text_tokens = _tokens(item.text)
    union = title_tokens | snippet_tokens | text_tokens

    title_coverage = _coverage(query_tokens, title_tokens)
    snippet_coverage = _coverage(query_tokens, snippet_tokens)
    text_coverage = _coverage(query_tokens, text_tokens)

    # Title evidence is the strongest signal; a body match alone is weak
    # because a long page can contain almost any generic token.
    score = max(
        0.65 * title_coverage + 0.35 * snippet_coverage,
        0.45 * text_coverage + 0.15 * title_coverage,
    )
    return score, union


def _time_scope_satisfied(
    query: str,
    items: Iterable[EvidenceItem],
    *,
    now: datetime,
) -> bool:
    scope = parse_time_scope(query, now=now)
    if scope is None:
        return True
    for item in items:
        dates = extract_event_dates(
            f"{item.snippet} {item.text}", now=now
        )
        if dates and scope_covers(min(dates), max(dates), scope):
            return True
    return False


def assess_bundle(
    query: str,
    bundle: EvidenceBundle,
    *,
    now: datetime | None = None,
) -> Adequacy:
    """Decide whether *bundle* is sufficient to answer *query*.

    Returns ``adequate=False`` with a reason for every failure mode:
    ``no_specific_terms``, ``missing_terms``, ``list_shape_missing``,
    ``time_scope_uncovered`` or ``low_score``.
    """
    moment = now or datetime.now(timezone.utc)
    tokens = _specific_tokens(query)
    if not tokens:
        return Adequacy(False, 0.0, 0.0, ("no_specific_terms",), tokens)
    query_tokens = set(tokens)

    reasons: list[str] = []
    best_score = 0.0
    union_tokens: set[str] = set()
    item_full_coverage = False

    for item in bundle.items:
        score, union = _item_score(query_tokens, item)
        union_tokens |= union
        if query_tokens <= union:
            item_full_coverage = True
            # Two independent query terms matching the same item is stronger
            # evidence than one generic term appearing in a long document.
            if len(query_tokens) >= 2:
                score = min(1.0, score + 0.05)
        best_score = max(best_score, score)
        if item.authority == "verified_fact":
            fact_coverage = _coverage(query_tokens, union)
            if fact_coverage >= 0.75:
                best_score = max(best_score, 1.0)

    # A fact intent backed by a verified fact is sufficient even when the
    # wording of the fact does not repeat the query term (a contact fact, for
    # example, may not contain the word "registrar").
    if bundle.intent in _FACT_INTENTS and any(
        item.authority == "verified_fact" for item in bundle.items
    ):
        best_score = max(best_score, 1.0)

    coverage = _coverage(query_tokens, union_tokens)
    # Short queries must be covered by one item, not by different items that
    # each contain a different query word. "library timings" must not be
    # satisfied by one library notice plus one exam-timing notice.
    if len(query_tokens) <= 3 and not item_full_coverage:
        reasons.append("missing_terms")

    list_query = _is_list_query(tokens)
    if list_query and not _has_list_shape(bundle.items):
        reasons.append("list_shape_missing")

    if not _time_scope_satisfied(query, bundle.items, now=moment):
        reasons.append("time_scope_uncovered")

    if best_score < settings.NGA_ADEQUACY_MIN_SCORE:
        reasons.append("low_score")

    # A verified fact is the authority for a fee/contact question. Its exact
    # wording need not repeat the query (a contact fact may not say
    # "registrar"), so the fact intent overrides the lexical gates. A time
    # mismatch still wins: a fact that does not cover the requested period is
    # not sufficient.
    if (
        bundle.intent in _FACT_INTENTS
        and any(item.authority == "verified_fact" for item in bundle.items)
        and "time_scope_uncovered" not in reasons
    ):
        return Adequacy(True, 1.0, coverage, (), tokens)

    adequate = not reasons
    return Adequacy(
        adequate=adequate,
        score=best_score,
        coverage=coverage,
        reasons=tuple(dict.fromkeys(reasons)),
        tokens=tokens,
    )


__all__ = ["Adequacy", "assess_bundle", "deep_search_query"]
