"""Typed navigation graph for the DSPMU live-site agent.

Phases 1–11 provide the compiler, SQLite store, search, read-only tools,
bounded page and PDF refresh, run metrics, deterministic alerts and the
single-writer scheduler. There is no embedding model or vector index. The live answer path
stays behind ``NGA_ENABLED``; the background worker behind
``NGA_SCHEDULER_ENABLED``.
"""

from app.sitegraph.alerts import Alert, AlertThresholds, evaluate_alerts
from app.sitegraph.compiler import compile_graph
from app.sitegraph.documents import (
    DocumentOptions,
    DocumentStats,
    discover_documents,
    refresh_documents,
)
from app.sitegraph.extract import ExtractedPage, extract_html
from app.sitegraph.fetch import FetchError, FetchResult, SiteFetcher
from app.sitegraph.models import (
    Authority,
    NodeStatus,
    PageVersion,
    PiiClass,
    SiteEdge,
    SiteNode,
)
from app.sitegraph.notices import NoticeIndex
from app.sitegraph.refresh import RefreshOptions, RefreshStats, refresh_graph
from app.sitegraph.scheduler import CycleResult, RefreshScheduler
from app.sitegraph.search import SearchHit, normalize_query, search_nodes
from app.sitegraph.store import GraphStore
from app.sitegraph.tools import SiteToolServer

__all__ = [
    "Alert",
    "AlertThresholds",
    "Authority",
    "CycleResult",
    "DocumentOptions",
    "DocumentStats",
    "ExtractedPage",
    "FetchError",
    "FetchResult",
    "GraphStore",
    "NodeStatus",
    "NoticeIndex",
    "PageVersion",
    "PiiClass",
    "RefreshOptions",
    "RefreshScheduler",
    "RefreshStats",
    "SearchHit",
    "SiteEdge",
    "SiteFetcher",
    "SiteNode",
    "SiteToolServer",
    "compile_graph",
    "discover_documents",
    "evaluate_alerts",
    "extract_html",
    "normalize_query",
    "refresh_documents",
    "refresh_graph",
    "search_nodes",
]
