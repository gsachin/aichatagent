"""PII policy for the navigation graph and tool server.

The URL-token guard in the compiler catches paths such as ``/cms/results``.
Some pages announce a result or staff list in the title while sitting on a
harmless path (for example a "Results" page under ``/news/examination``).
This module applies the same conservative title guard at compile, search and
tool-response time. A blocked node is never answerable, even if a future
compiler change misses its URL.
"""

from __future__ import annotations

import re

from app.notice_store import is_likely_pii_title
from app.sitegraph.models import NodeStatus, PiiClass, SiteNode

_STUDENT_TITLE_TOKENS = frozenset({
    "result", "results", "merit", "selection", "selected", "waiting",
    "answer", "roll", "registration", "awarded", "topper", "toppers",
})
_STAFF_TITLE_TOKENS = frozenset({
    "staff", "faculty", "faculties", "teaching", "nonteaching",
})


def title_pii_class(title: str) -> PiiClass | None:
    """Return the PII class implied by a page/section title, or ``None``."""
    text = (title or "").strip()
    if not text:
        return None
    if is_likely_pii_title(text):
        return PiiClass.STUDENT_LIST
    tokens = set(re.findall(r"[a-z0-9]+", text.lower()))
    if tokens & _STUDENT_TITLE_TOKENS:
        return PiiClass.STUDENT_LIST
    if tokens & _STAFF_TITLE_TOKENS:
        return PiiClass.STAFF_DIRECTORY
    return None


def _pii_allowed(node: SiteNode, pii_guard: bool) -> bool:
    if not pii_guard:
        return True
    # Student result/merit/selection pages stay blocked. Public staff/faculty
    # directories are institutional information and may be used.
    if node.pii_class is PiiClass.STUDENT_LIST:
        return False
    title_class = title_pii_class(node.title)
    if title_class is PiiClass.STUDENT_LIST:
        return False
    if node.pii_class is PiiClass.STAFF_DIRECTORY:
        return True
    if node.pii_class is not PiiClass.PUBLIC:
        return False
    return title_class in (None, PiiClass.STAFF_DIRECTORY)


def can_ground(node: SiteNode, pii_guard: bool = True) -> bool:
    """True when this node's text may ground a factual claim.

    A shell, a soft-404, an unreachable page or a PII page cannot ground an
    answer. This is the old ``is_answerable`` contract and remains the default
    for evidence assembly.
    """
    if node.status in (
        NodeStatus.BLOCKED,
        NodeStatus.EMPTY_SHELL,
        NodeStatus.SOFT_404,
        NodeStatus.UNREACHABLE,
        NodeStatus.REMOVED,
    ):
        return False
    return _pii_allowed(node, pii_guard)


def can_navigate(node: SiteNode, pii_guard: bool = True) -> bool:
    """True when this node may be returned as a route.

    An empty CMS shell is navigable: it has a URL, a title, a parent and often
    siblings/documents. It is *not* groundable, so it must never be used as
    factual evidence. A blocked PII/result page is not navigable either.
    """
    if node.status in (
        NodeStatus.BLOCKED,
        NodeStatus.SOFT_404,
        NodeStatus.UNREACHABLE,
        NodeStatus.REMOVED,
    ):
        return False
    return _pii_allowed(node, pii_guard)


def is_answerable(node: SiteNode, pii_guard: bool = True) -> bool:
    """Backward-compatible alias for :func:`can_ground`."""
    return can_ground(node, pii_guard)
