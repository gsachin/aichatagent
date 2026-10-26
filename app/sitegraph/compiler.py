"""Compile the DSPMU sitemap and rendered navigation into a typed graph.

Phase 1 is deliberately offline-capable and answer-path-free. The compiler
reads the sitemap and home-page cache (or the live sources when explicitly
allowed), creates one node per navigation point, links parents to children,
and emits a coverage report.

The graph is a tree plus cross-link/document edges, stored relationally. It is
not a hand-maintained knowledge graph and not a graph database. See
``doc/sdlc/nga/02-hld.md`` ADR-011 … ADR-013.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup, NavigableString, Tag

from app.siteindex.discover import parse_nav_links, parse_sitemap
from app.sitegraph.models import (
    Authority,
    ContentType,
    NodeStatus,
    PiiClass,
    SiteEdge,
    SiteNode,
)
from app.sitegraph.policy import title_pii_class

#: Conservative PII URL tokens. The same boundary as the static router and the
#: notices pipeline: no result/merit/selection lists, no staff directories.
_DEFAULT_BLOCKED_TOKENS = frozenset({
    "result", "results", "merit", "selection", "selected", "waiting",
    "answer", "admit", "archive", "awarded", "departments", "faculties",
    "staff", "faculty", "teaching", "nonteaching",
})
_STUDENT_LIST_TOKENS = frozenset({
    "result", "results", "merit", "selection", "selected", "waiting",
    "answer", "admit", "archive", "awarded",
})
_STAFF_DIRECTORY_TOKENS = frozenset({
    "departments", "faculties", "staff", "faculty", "teaching", "nonteaching",
})

_ALIAS_STOPWORDS = frozenset(
    "a an the and or of to in on at for from with by is are was were "
    "this that these those".split()
)

_SECTION_LABELS = {
    "cms": "CMS Pages",
    "page": "Pages",
    "departments": "Departments",
    "faculties": "Faculties",
    "research": "Research",
    "sponsored-projects": "Sponsored Projects",
    "uploads": "Documents",
    "gallery": "Gallery",
    "student-life": "Student Life",
}

_PDF_SUFFIXES = (".pdf",)
_NON_PAGE_SUFFIXES = (
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".zip", ".doc",
    ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
)


@dataclass(frozen=True)
class CompileResult:
    nodes: tuple[SiteNode, ...]
    edges: tuple[SiteEdge, ...]
    coverage: dict
    graph_hash: str
    generated_at: str


@dataclass(frozen=True)
class _NavEntry:
    label: str
    url: str | None
    ancestors: tuple[str, ...]
    depth: int
    order: int


@dataclass
class _Draft:
    node_id: str
    url: str
    canonical_path: str
    title: str
    nav_label: str
    content_type: ContentType
    authority: Authority
    pii_class: PiiClass
    status: NodeStatus
    lastmod: str | None
    aliases: tuple[str, ...]
    languages: tuple[str, ...] = ("en",)
    parent_hint: str | None = None
    parent_id: str | None = None
    depth: int = 0
    section: str = ""
    path_depth: int = 0
    is_section: bool = False
    content_hash: str = ""
    fetched_at: str = ""
    verified_at: str | None = None
    size_bytes: int = 0
    canonical_id: str = ""
    resolved: bool = False
    warnings: list[str] = field(default_factory=list)

    def to_node(self) -> SiteNode:
        return SiteNode(
            node_id=self.node_id,
            url=self.url,
            canonical_path=self.canonical_path,
            parent_id=self.parent_id,
            depth=self.depth,
            section=self.section,
            title=self.title,
            nav_label=self.nav_label,
            aliases=self.aliases,
            languages=self.languages,
            content_type=self.content_type,
            authority=self.authority,
            pii_class=self.pii_class,
            status=self.status,
            lastmod=self.lastmod,
            content_hash=self.content_hash,
            fetched_at=self.fetched_at,
            verified_at=self.verified_at,
            size_bytes=self.size_bytes,
            canonical_id=self.canonical_id or self.node_id,
        )


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_url(raw: str, base_url: str) -> str | None:
    """Canonical absolute URL, or ``None`` when it is not an HTTP(S) URL."""
    raw = (raw or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        parsed = urlparse(urljoin(base_url, raw))
    scheme = (parsed.scheme or "").lower()
    netloc = (parsed.netloc or "").lower()
    if scheme not in ("http", "https") or not netloc:
        return None
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    return urlunparse((scheme, netloc, path, "", parsed.query, ""))


def _hostname(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug or "node"


def _short_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


def _node_id_for_path(path: str) -> str:
    segments = [
        _slug(segment)
        for segment in (path or "/").strip("/").split("/")
        if segment
    ]
    if not segments:
        return "home"
    node_id = ".".join(segments)
    if len(node_id) > 120:
        node_id = node_id[:100] + "-" + _short_hash(path)
    return node_id


def _section_id(label: str, ancestors: tuple[str, ...] = ()) -> str:
    if not ancestors:
        return f"section.{_slug(label)}"
    scope = ">".join((*ancestors, label))
    return f"section.{_slug(label)}-{_short_hash(scope)}"


def _external_id(url: str) -> str:
    parsed = urlparse(url)
    path = _slug(parsed.path.strip("/")) if parsed.path.strip("/") else "root"
    node_id = f"external.{_slug(parsed.hostname or 'unknown')}.{path}"
    if len(node_id) > 120:
        node_id = node_id[:100] + "-" + _short_hash(url)
    return node_id


def _title_from_path(path: str) -> str:
    last = (path or "/").rstrip("/").rsplit("/", 1)[-1]
    if not last:
        return "Home"
    text = re.sub(r"[-_.]+", " ", last)
    words = [word for word in text.split() if word]
    return " ".join(word[:1].upper() + word[1:] for word in words) or "Home"


def _section_for_path(path: str) -> str:
    first = (path or "/").strip("/").split("/")[0]
    if not first:
        return "Home"
    return _SECTION_LABELS.get(first, _title_from_path("/" + first))


def _path_depth(path: str) -> int:
    stripped = (path or "/").strip("/")
    return 0 if not stripped else len([part for part in stripped.split("/") if part])


def _path_parent_path(path: str) -> str:
    stripped = (path or "/").strip("/")
    if not stripped or "/" not in stripped:
        return "/"
    return "/" + stripped.rsplit("/", 1)[0]


def _aliases(title: str, nav_label: str, canonical_path: str) -> tuple[str, ...]:
    values: list[str] = []
    sources = (
        nav_label,
        title,
        canonical_path.replace("/", " ").replace("-", " ").replace(".", " "),
    )
    for source in sources:
        text = re.sub(r"\s+", " ", (source or "").strip().lower())
        if text and text not in values:
            values.append(text)
        for token in re.findall(r"[a-z0-9]+", text):
            if len(token) >= 3 and token not in _ALIAS_STOPWORDS and token not in values:
                values.append(token)
    return tuple(values[:12])


def _blocked_kind(path: str, blocked_tokens: frozenset[str]) -> PiiClass | None:
    tokens = set(re.findall(r"[a-z0-9]+", (path or "").lower()))
    if not tokens & blocked_tokens:
        return None
    if tokens & _STUDENT_LIST_TOKENS:
        return PiiClass.STUDENT_LIST
    if tokens & _STAFF_DIRECTORY_TOKENS:
        return PiiClass.STAFF_DIRECTORY
    return PiiClass.BLOCKED


def _content_kind(
    url: str, path: str, allowed_hosts: frozenset[str]
) -> tuple[ContentType, NodeStatus, Authority]:
    host = _hostname(url)
    lowered = path.lower()
    if host not in allowed_hosts:
        return ContentType.EXTERNAL, NodeStatus.EXTERNAL, Authority.EXTERNAL
    if lowered.endswith(_PDF_SUFFIXES):
        return ContentType.PDF, NodeStatus.OK, Authority.OFFICIAL_DOCUMENT
    if lowered.endswith(_NON_PAGE_SUFFIXES):
        return ContentType.EXTERNAL, NodeStatus.EXTERNAL, Authority.EXTERNAL
    return ContentType.HTML, NodeStatus.OK, Authority.OFFICIAL_PAGE


def _direct_label(li: Tag) -> tuple[str, str]:
    """The label and href belonging directly to this ``li``.

    The DSPMU nav marks a non-link section with ``<a href="#">Label</a>`` or a
    ``span``; both must be read without consuming the child ``ul``.
    """
    for child in li.children:
        if isinstance(child, Tag):
            if child.name == "a":
                return (
                    child.get_text(" ", strip=True),
                    (child.get("href") or "").strip(),
                )
            if child.name in ("span", "strong", "b"):
                return child.get_text(" ", strip=True), ""
            if child.name == "ul":
                break
        elif isinstance(child, NavigableString):
            text = str(child).strip()
            if text:
                return text, ""
    # Fallback: direct text that is not inside the child list.
    parts = [
        str(child).strip()
        for child in li.children
        if isinstance(child, NavigableString) and str(child).strip()
    ]
    return (" ".join(parts).strip(), "")


def _parse_nav_tree(html: str, base_url: str) -> list[_NavEntry]:
    """Walk the rendered nav and keep each item's full ancestor chain.

    ``app.siteindex.discover.parse_nav`` only carries the immediate parent
    label; that is enough for a flat count but not for a graph. This walk
    preserves ``ancestors`` so a nested section cannot be attached to the
    previous top-level section by accident.
    """
    soup = BeautifulSoup(html or "", "lxml")
    nav = soup.find("nav")
    if nav is None:
        return []
    root_ul = nav.find("ul")
    if root_ul is None:
        return []

    entries: list[_NavEntry] = []
    order = 0

    def walk(ul: Tag, ancestors: tuple[str, ...], depth: int) -> None:
        nonlocal order
        for li in ul.find_all("li", recursive=False):
            label, href = _direct_label(li)
            url = None
            if href and not href.startswith(("#", "javascript:", "mailto:", "tel:")):
                url = normalize_url(href, base_url)
            order += 1
            if label or url:
                entries.append(
                    _NavEntry(
                        label=label,
                        url=url,
                        ancestors=ancestors,
                        depth=depth,
                        order=order,
                    )
                )
            child_ul = li.find("ul", recursive=False)
            if child_ul is not None:
                child_ancestors = ancestors + ((label,) if label else ())
                walk(child_ul, child_ancestors, depth + 1)

    walk(root_ul, (), 1)
    return entries


def _graph_hash(nodes: list[SiteNode], edges: list[SiteEdge]) -> str:
    payload = {
        "nodes": [node.to_dict() for node in nodes],
        "edges": [edge.to_dict() for edge in edges],
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _make_page_draft(
    *,
    url: str,
    title: str,
    nav_label: str,
    parent_hint: str | None,
    lastmod: str | None,
    allowed_hosts: frozenset[str],
    blocked_tokens: frozenset[str],
    drafts: dict[str, _Draft],
    pii_guard: bool = True,
) -> _Draft:
    parsed = urlparse(url)
    path = parsed.path or "/"
    effective_title = title or _title_from_path(path)
    content_type, status, authority = _content_kind(url, path, allowed_hosts)
    blocked_kind = _blocked_kind(path, blocked_tokens) if pii_guard else None
    if blocked_kind is None and pii_guard:
        blocked_kind = title_pii_class(effective_title)
    pii_class = PiiClass.PUBLIC
    if blocked_kind is not None:
        pii_class = blocked_kind
        status = NodeStatus.BLOCKED

    if content_type is ContentType.EXTERNAL:
        node_id = _external_id(url)
    else:
        node_id = _node_id_for_path(path)
    if node_id in drafts and drafts[node_id].url != url:
        node_id = f"{node_id}-{_short_hash(url)}"

    return _Draft(
        node_id=node_id,
        url=url,
        canonical_path=path,
        title=effective_title,
        nav_label=nav_label or effective_title,
        content_type=content_type,
        authority=authority,
        pii_class=pii_class,
        status=status,
        lastmod=lastmod,
        aliases=_aliases(title, nav_label, path),
        parent_hint=parent_hint,
        path_depth=_path_depth(path),
    )


def compile_graph(
    sitemap_xml: str,
    home_html: str,
    *,
    base_url: str = "https://dspmuranchi.ac.in",
    sitemap_url: str = "https://dspmuranchi.ac.in/sitemap.xml",
    allowed_hosts: Iterable[str] = ("dspmuranchi.ac.in", "www.dspmuranchi.ac.in"),
    blocked_tokens: Iterable[str] = _DEFAULT_BLOCKED_TOKENS,
    min_coverage: float = 0.95,
    pii_guard: bool = True,
    source_mode: str = "provided",
    generated_at: str | None = None,
) -> CompileResult:
    """Build the navigation graph from a sitemap and the rendered home page."""
    base = base_url.rstrip("/") + "/"
    allowed = frozenset(
        host.strip().lower() for host in allowed_hosts if host and host.strip()
    )
    blocked = frozenset(
        token.strip().lower() for token in blocked_tokens if token and token.strip()
    )
    generated = generated_at or _now_utc()
    warnings: list[str] = []

    entries = parse_sitemap(sitemap_xml)
    nav_entries = _parse_nav_tree(home_html, base)
    nav_links = parse_nav_links(home_html, base)

    sitemap_urls: dict[str, str | None] = {}
    invalid_sitemap: list[dict[str, str]] = []
    for entry in entries:
        raw = entry.get("loc") or ""
        url = normalize_url(raw, base)
        if url is None:
            invalid_sitemap.append({"url": raw, "reason": "invalid_url"})
            continue
        sitemap_urls.setdefault(url, entry.get("lastmod") or None)
    declared_sitemap = set(sitemap_urls)

    home_url = normalize_url(base, base) or base
    sitemap_urls.setdefault(home_url, None)

    drafts: dict[str, _Draft] = {}
    draft_by_url: dict[str, _Draft] = {}
    entry_key_to_order: dict[tuple[tuple[str, ...], str], int] = {}
    entry_order_to_node: dict[int, str] = {}
    nav_by_url: dict[str, _NavEntry] = {}
    nav_seen_urls: set[str] = set()
    nav_duplicates: list[str] = []

    home = _make_page_draft(
        url=home_url,
        title="Home",
        nav_label="Home",
        parent_hint=None,
        lastmod=sitemap_urls.get(home_url),
        allowed_hosts=allowed,
        blocked_tokens=blocked,
        drafts=drafts,
        pii_guard=pii_guard,
    )
    home.node_id = "home"
    home.is_section = False
    drafts["home"] = home
    draft_by_url[home_url] = home

    def entry_node(entry: _NavEntry) -> str:
        if not entry.ancestors:
            return "home"
        parent_key = (entry.ancestors[:-1], entry.ancestors[-1])
        parent_order = entry_key_to_order.get(parent_key)
        if parent_order is None:
            return "home"
        return entry_order_to_node.get(parent_order, "home")

    for entry in nav_entries:
        if not entry.label and not entry.url:
            continue
        parent_node_id = entry_node(entry)
        label = entry.label or (_title_from_path(urlparse(entry.url or "").path))

        if entry.url:
            url = entry.url
            if url in nav_seen_urls:
                nav_duplicates.append(url)
            else:
                nav_seen_urls.add(url)
            if url in draft_by_url:
                node_id = draft_by_url[url].node_id
                nav_by_url.setdefault(url, entry)
            else:
                draft = _make_page_draft(
                    url=url,
                    title=label,
                    nav_label=label,
                    parent_hint=parent_node_id,
                    lastmod=sitemap_urls.get(url),
                    allowed_hosts=allowed,
                    blocked_tokens=blocked,
                    drafts=drafts,
                    pii_guard=pii_guard,
                )
                if draft.node_id in drafts and drafts[draft.node_id].url != url:
                    draft.node_id = f"{draft.node_id}-{_short_hash(url)}"
                drafts[draft.node_id] = draft
                draft_by_url[url] = draft
                nav_by_url[url] = entry
                node_id = draft.node_id
            entry_key_to_order[(entry.ancestors, label)] = entry.order
            entry_order_to_node[entry.order] = node_id
        else:
            node_id = _section_id(label, entry.ancestors)
            if node_id in drafts:
                node_id = f"{node_id}-{entry.order}"
            section_slug = _slug(node_id)
            section_pii = title_pii_class(label) if pii_guard else None
            draft = _Draft(
                node_id=node_id,
                url=f"{base.rstrip('/')}/_nav/{section_slug}",
                canonical_path=f"/_nav/{section_slug}",
                title=label,
                nav_label=label,
                content_type=ContentType.SECTION,
                authority=Authority.OFFICIAL_PAGE,
                pii_class=section_pii or PiiClass.PUBLIC,
                status=(
                    NodeStatus.BLOCKED if section_pii is not None else NodeStatus.OK
                ),
                lastmod=None,
                aliases=_aliases(label, label, label),
                parent_hint=parent_node_id,
                path_depth=entry.depth,
                is_section=True,
            )
            drafts[node_id] = draft
            entry_key_to_order[(entry.ancestors, label)] = entry.order
            entry_order_to_node[entry.order] = node_id

    section_by_slug: dict[str, str] = {}
    for draft in sorted(
        (item for item in drafts.values() if item.is_section),
        key=lambda item: (item.path_depth, item.node_id),
    ):
        section_by_slug.setdefault(_slug(draft.title), draft.node_id)

    def fallback_parent(url: str) -> str:
        path = urlparse(url).path or "/"
        parent_url = normalize_url(
            base.rstrip("/") + _path_parent_path(path), base
        )
        parent = draft_by_url.get(parent_url or "")
        if parent is not None:
            return parent.node_id
        first = path.strip("/").split("/")[0].lower()
        section_id = section_by_slug.get(first)
        return section_id or "home"

    for nav in nav_links:
        url = normalize_url(nav.url, base)
        if url is None or url in draft_by_url:
            continue
        label = nav.label.strip() or _title_from_path(urlparse(url).path)
        draft = _make_page_draft(
            url=url,
            title=label,
            nav_label=label,
            parent_hint=fallback_parent(url),
            lastmod=sitemap_urls.get(url),
            allowed_hosts=allowed,
            blocked_tokens=blocked,
            drafts=drafts,
            pii_guard=pii_guard,
        )
        drafts[draft.node_id] = draft
        draft_by_url[url] = draft

    for url in sorted(set(sitemap_urls) | {home_url}):
        if url in draft_by_url:
            continue
        path = urlparse(url).path or "/"
        title = _title_from_path(path)
        draft = _make_page_draft(
            url=url,
            title=title,
            nav_label=title,
            parent_hint=fallback_parent(url),
            lastmod=sitemap_urls.get(url),
            allowed_hosts=allowed,
            blocked_tokens=blocked,
            drafts=drafts,
            pii_guard=pii_guard,
        )
        drafts[draft.node_id] = draft
        draft_by_url[url] = draft

    # ── resolve parents, depths and sections ──────────────────────
    def resolve(node_id: str, stack: frozenset[str]) -> None:
        draft = drafts[node_id]
        if draft.resolved:
            return
        parent_id = draft.parent_hint or "home"
        if parent_id not in drafts or parent_id == node_id or parent_id in stack:
            parent_id = "home"
        if parent_id == "home":
            home.resolved = True
            home.parent_id = None
            home.depth = 0
            home.section = "Home"
        else:
            resolve(parent_id, stack | {node_id})
        parent = drafts[parent_id]
        draft.parent_id = parent_id
        draft.depth = 0 if node_id == "home" else parent.depth + 1
        if node_id == "home":
            draft.section = "Home"
        elif parent_id == "home":
            draft.section = _section_for_path(draft.canonical_path)
        elif parent.is_section:
            draft.section = parent.title
        else:
            draft.section = parent.title or parent.section
        draft.resolved = True

    for node_id in list(drafts):
        resolve(node_id, frozenset())

    nodes = sorted(
        (draft.to_node() for draft in drafts.values()),
        key=lambda node: (node.depth, node.node_id),
    )

    # ── edges ─────────────────────────────────────────────────────
    child_ordinals: dict[str, int] = {}
    edges: list[SiteEdge] = []
    for node in nodes:
        if node.parent_id is None:
            continue
        ordinal = child_ordinals.get(node.parent_id, 0)
        child_ordinals[node.parent_id] = ordinal + 1
        edges.append(
            SiteEdge(
                edge_id=f"parent_of:{node.parent_id}:{node.node_id}",
                src_id=node.parent_id,
                dst_id=node.node_id,
                kind="parent_of",
                anchor=node.nav_label,
                ordinal=ordinal,
            )
        )
        if node.content_type is ContentType.PDF:
            edges.append(
                SiteEdge(
                    edge_id=f"documents:{node.parent_id}:{node.node_id}",
                    src_id=node.parent_id,
                    dst_id=node.node_id,
                    kind="documents",
                    anchor=node.title,
                    ordinal=ordinal,
                )
            )
        elif node.content_type is ContentType.EXTERNAL:
            edges.append(
                SiteEdge(
                    edge_id=f"links_to:{node.parent_id}:{node.node_id}",
                    src_id=node.parent_id,
                    dst_id=node.node_id,
                    kind="links_to",
                    anchor=node.title,
                    ordinal=ordinal,
                )
            )
    edges.sort(key=lambda edge: (edge.kind, edge.src_id, edge.ordinal, edge.dst_id))

    node_urls = {node.url for node in nodes}
    represented = {url for url in declared_sitemap if url in node_urls}
    represented_sitemap = sorted(represented)
    missing_sitemap = list(invalid_sitemap)
    missing_sitemap.extend(
        {"url": url, "reason": "not_represented"}
        for url in sorted(declared_sitemap - represented)
    )
    coverage_pct = (
        len(represented_sitemap) / len(declared_sitemap)
        if declared_sitemap else 0.0
    )
    section_nodes = [node for node in nodes if node.content_type is ContentType.SECTION]
    child_parents = {edge.src_id for edge in edges if edge.kind == "parent_of"}
    top_level_sections = sorted(
        node.title for node in nodes
        if node.parent_id == "home"
        and (node.content_type is ContentType.SECTION or node.node_id in child_parents)
    )
    blocked_nodes = [node for node in nodes if node.status is NodeStatus.BLOCKED]
    orphans = [
        node.node_id for node in nodes
        if node.parent_id is None and node.node_id != "home"
    ]

    if nav_duplicates:
        warnings.append(f"duplicate_nav_urls:{len(nav_duplicates)}")
    if not nav_entries:
        warnings.append("nav_parse_empty")
    if not declared_sitemap:
        warnings.append("sitemap_empty")
    if invalid_sitemap:
        warnings.append(f"invalid_sitemap_urls:{len(invalid_sitemap)}")
    if blocked_nodes:
        warnings.append(f"blocked_nodes:{len(blocked_nodes)}")

    graph_hash = _graph_hash(nodes, edges)
    coverage = {
        "generated_at": generated,
        "source_mode": source_mode,
        "base_url": base.rstrip("/"),
        "sitemap_url": sitemap_url,
        "sitemap_urls": len(declared_sitemap),
        "nav_nodes": len(nav_seen_urls),
        "nav_links": len(nav_links),
        "nav_sections": len([entry for entry in nav_entries if not entry.url]),
        "nav_urls_represented": sum(1 for url in nav_by_url if url in node_urls),
        "unique_nodes": len(nodes),
        "represented_sitemap_urls": len(represented_sitemap),
        "missing_sitemap_urls": missing_sitemap,
        "coverage_pct": round(coverage_pct, 4),
        "min_coverage": min_coverage,
        "meets_gate": coverage_pct >= min_coverage,
        "pii_guard": pii_guard,
        "sections": len(section_nodes),
        "pages": sum(1 for node in nodes if node.content_type is ContentType.HTML),
        "documents": sum(1 for node in nodes if node.content_type is ContentType.PDF),
        "external": sum(1 for node in nodes if node.content_type is ContentType.EXTERNAL),
        "blocked": len(blocked_nodes),
        "departments": sum(
            1 for node in nodes
            if node.canonical_path.startswith("/departments/")
        ),
        "top_level_sections": top_level_sections,
        "orphans": orphans,
        "warnings": warnings,
        "graph_hash": graph_hash,
    }
    return CompileResult(
        nodes=tuple(nodes),
        edges=tuple(edges),
        coverage=coverage,
        graph_hash=graph_hash,
        generated_at=generated,
    )
