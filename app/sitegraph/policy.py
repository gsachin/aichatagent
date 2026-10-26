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


def is_answerable(node: SiteNode, pii_guard: bool = True) -> bool:
    """True when the node may be used in an answer or a tool result.

    With ``pii_guard=False`` the product decision treats the university's
    published pages as usable. The node must still not be blocked from a
    previous guarded compile; recompile after changing the setting.
    """
    if node.status in (
        NodeStatus.BLOCKED,
        NodeStatus.EMPTY_SHELL,
        NodeStatus.SOFT_404,
        NodeStatus.UNREACHABLE,
        NodeStatus.REMOVED,
    ):
        return False
    if not pii_guard:
        return True
    if node.pii_class is not PiiClass.PUBLIC:
        return False
    return title_pii_class(node.title) is None
