#!/usr/bin/env python3
"""
eval/checks.py -- deterministic, model-free checks for the frozen golden set (DG-03 / TRD-23).

What this file is
-----------------
The first stage of the MOD-06 evaluation runner: the checks that need **no model at all**.
It validates the golden set as a fixture, reports per-intent coverage, and scores a
candidate's answers on everything that can be decided mechanically:

  * required facts present (containment against the knowledge base ground truth)
  * forbidden claims absent (literal phrases and regex patterns)
  * spoken-format compliance (no markdown, no lists, no tables, sentence and word limits)
  * behaviour: the right escalation / refusal / clarification / lead-capture move

It deliberately does **not** import anything from `app.*` and has no model client, so it
runs with the project venv and no stack:

    .venv/Scripts/python.exe eval/checks.py
    .venv/Scripts/python.exe eval/checks.py --answers eval/answers.example.jsonl
    .venv/Scripts/python.exe eval/checks.py --min-cases 8 --json eval/checks_report.json

Two outcomes are deliberately *not* failures
--------------------------------------------
* `BLOCKED`  -- a critical case whose ground truth is not approved. Until a PO signs a case it
  sits at `PENDING_PO_SIGNOFF` and its intent reports BLOCKED; once signed it becomes
  `PO_APPROVED` and the block clears. This is never a pass and never a score of zero
  (US-003 AC-2). Note that `verified` does NOT clear a block: it records a mechanical
  transcription from the knowledge base and claims no human judgement, so the two statuses
  are deliberately not interchangeable.
* `underpowered` -- an intent with too few cases to support a confidence interval, per
  BRD-09. Its result may not be used to justify adoption.

Exit codes: 0 = no problems found, 1 = fixture error, 2 = at least one FAIL, 3 = at least
one BLOCKED (missing/unapproved ground truth).

Nothing in this file writes to the repository, and it holds no secret values.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
GOLDEN_PATH = HERE / "golden_set.jsonl"
HELDOUT_PATH = HERE / "heldout.txt"
KB_PATH = REPO / "content" / "meridian" / "meridian_knowledge_base.md"
CATALOG_PATH = REPO / "doc" / "sdlc" / "09-intent-catalog.md"

#: Fallback intent catalog, used only if `09-intent-catalog.md` cannot be parsed.
#: The intent numbering in that catalog is NOT an ID space; intents are identified by name.
FALLBACK_INTENTS: tuple[str, ...] = (
    "Undergraduate programs", "Postgraduate programs", "Doctoral / PhD programs",
    "Fees structure", "Scholarships / financial aid", "Important dates / deadlines",
    "Admission process / eligibility", "Campus / hostel / logistics",
    "Accreditation / rankings / overview", "Leadership / history", "Contact information",
    "FAQ", "Lead capture", "Escalation / handoff to human", "Callback / appointment change",
    "Outbound call handling", "Opt-out / decline", "Call termination (sign-off)",
    "Noise / fragment / unintelligible", "STT error correction / clarification",
    "Multiple requests in one turn", "Interruption / barge-in", 'Backchannel ("mm-hm")',
    "Caller silence", "Topic change", "Distressed caller", "Out-of-scope",
    "Consequential action confirmation",
)
FALLBACK_PO_SIGNOFF: frozenset[str] = frozenset({
    "Fees structure", "Scholarships / financial aid", "Important dates / deadlines",
    "Admission process / eligibility", "Escalation / handoff to human", "Interruption / barge-in",
})

#: Worst-case 95% Wilson interval half-width at p = 0.5 for n observations. This is the
#: number that decides whether an intent's sample can carry a confidence interval at all.
Z95 = 1.959963984540054

#: Per-intent sample floor. The intent catalog (via US-003) sets 8 cases as the bar for a
#: critical intent; below it BRD-09 requires the intent to be reported as underpowered.
DEFAULT_MIN_CASES = 8
min_cases = DEFAULT_MIN_CASES

EXIT_OK, EXIT_FIXTURE, EXIT_FAIL, EXIT_BLOCKED = 0, 1, 2, 3


class FixtureError(Exception):
    """A malformed case or an unfrozen comparison. The run refuses to start (TAC-2)."""


class JudgeUnavailable(Exception):
    """Never raised here: this stage consults no judge at all. Declared for the runner."""


# ─────────────────────────────── text normalisation ───────────────────────────────

_DASHES = {"‐": "-", "‑": "-", "‒": "-", "–": "-",
           "—": "-", "―": "-", "−": "-"}
_QUOTES = {"‘": "'", "’": "'", "“": '"', "”": '"', "´": "'"}


def normalize(text: str) -> str:
    """Lower-case, straighten quotes and dashes, drop thousands separators, collapse space."""
    out = []
    for ch in text:
        out.append(_DASHES.get(ch) or _QUOTES.get(ch) or ch)
    t = "".join(out).lower()
    t = t.replace(",", "").replace(" ", " ")
    return re.sub(r"\s+", " ", t).strip()


def loose(text: str) -> str:
    """Everything but letters and digits removed. Used for source traceability, not scoring."""
    return re.sub(r"[^a-z0-9]", "", normalize(text))


# ─────────────────────────────── case loading ───────────────────────────────

REQUIRED_KEYS = ("case_id", "intent", "critical", "approved_by", "ground_truth_status",
                 "ground_truth_source", "split", "utterances", "question", "expected",
                 "required_facts", "forbidden_claims", "forbidden_patterns", "spoken_format",
                 "tags")
KINDS = ("answer", "clarify", "escalate", "refuse", "capture-lead")
#: Three provenances, and keeping them distinct is the point.
#:
#:   verified            the ground truth is a mechanical transcription from the
#:                       knowledge base. No human judgement is claimed.
#:   PO_APPROVED         a human with authority read the case and signed it. The
#:                       only status that may carry `approved_by`.
#:   PENDING_PO_SIGNOFF  not yet decided. Counts toward `unapproved`, so it is
#:                       what blocks a critical intent.
#:
#: `verified` and `PO_APPROVED` are NOT interchangeable, and merging them was a
#: real defect: the schema previously offered only `verified` (meaning "copied
#: from the KB") and `PENDING_PO_SIGNOFF` (meaning "not approved"), while the
#: validator forbade `verified` for exactly the PO-sign-off intents and rejected
#: any non-null `approved_by`. The result was that no state satisfied both the
#: validator and the blocker, so DG-03 could not be closed by any action a PO
#: took -- the gate was built to refuse and nothing was built to open it.
STATUSES = ("verified", "PENDING_PO_SIGNOFF", "PO_APPROVED")
SPLITS = ("tuned", "held_out")
TAGS = ("multi_turn", "noisy_asr", "adversarial", "interruption")


def load_cases(path: Path = GOLDEN_PATH) -> list[dict[str, Any]]:
    """Load every case. A malformed or duplicate case raises FixtureError naming it."""
    if not path.exists():
        raise FixtureError(f"golden set not found: {path}")
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            raise FixtureError(f"{path.name}:{lineno}: blank line -- every line is a case")
        try:
            case = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise FixtureError(f"{path.name}:{lineno}: malformed JSON ({exc.msg})") from exc
        if not isinstance(case, dict):
            raise FixtureError(f"{path.name}:{lineno}: case is not a JSON object")
        cid = case.get("case_id") or f"<line {lineno}>"
        missing = [k for k in REQUIRED_KEYS if k not in case]
        if missing:
            raise FixtureError(f"case {cid}: missing required field(s): {', '.join(missing)}")
        if cid in seen:
            raise FixtureError(f"case {cid}: duplicate case_id")
        seen.add(cid)
        cases.append(case)
    if not cases:
        raise FixtureError(f"{path.name}: no cases found")
    return cases


def load_heldout(path: Path = HELDOUT_PATH) -> list[str]:
    if not path.exists():
        raise FixtureError(f"held-out split not found: {path}")
    ids = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if len(ids) != len(set(ids)):
        raise FixtureError(f"{path.name}: duplicate case id in the held-out split")
    return ids


def load_catalog(path: Path = CATALOG_PATH) -> tuple[tuple[str, ...], frozenset[str], str]:
    """Return (intent names, PO-sign-off intent names, note) read from the intent catalog."""
    if not path.exists():
        return FALLBACK_INTENTS, FALLBACK_PO_SIGNOFF, f"catalog not found, using {len(FALLBACK_INTENTS)} embedded intents"
    names: list[str] = []
    po: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or not cells[0].isdigit():
            continue
        name = cells[1].replace("**", "").strip()
        if not name:
            continue
        names.append(name)
        # Rows 4, 5, 6, 7, 14 read "**PO sign-off required**"; row 22 reads
        # "decision pending **PO sign-off**". Match the stem so all six are caught.
        if "PO sign-off" in line:
            po.add(name)
    if len(names) != len(FALLBACK_INTENTS):
        return (FALLBACK_INTENTS, FALLBACK_PO_SIGNOFF,
                f"catalog parse yielded {len(names)} rows, expected {len(FALLBACK_INTENTS)}; using embedded list")
    note = f"intent catalog parsed: {len(names)} intents, {len(po)} marked PO sign-off required"
    return tuple(names), frozenset(po), note


def catalog_critical(path: Path = CATALOG_PATH) -> tuple[set[str], str]:
    """Rows the catalog table flags Critical, plus a note when it disagrees with its own prose."""
    if not path.exists():
        return set(FALLBACK_PO_SIGNOFF), "catalog not found"
    crit: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or not cells[0].isdigit():
            continue
        name = cells[1].replace("**", "").strip()
        if name and "Critical" in line:
            crit.add(name)
    note = f"catalog table marks {len(crit)} rows Critical"
    prose = re.search(r"the (\w+) Critical intents", path.read_text(encoding="utf-8"))
    if prose and prose.group(1).lower() not in {str(len(crit)), "eleven" if len(crit) == 11 else ""}:
        note += (f"; the catalog prose says '{prose.group(1)}' -- a source discrepancy the PO/BA "
                 f"must resolve (this check covers every row the table marks Critical)")
    return crit, note


# ─────────────────────────────── format checks ───────────────────────────────

_LIST_ITEM_RE = re.compile(r"(?:^\s*|(?<=[.!?:;])\s)\s*(?:[-*•·●▪]|\d{1,2}[.)])\s+\S",
                           re.MULTILINE)
_HEADING_RE = re.compile(r"(?m)^\s*#{1,6}\s+\S")
_TABLE_RE = re.compile(r"(?m)^\s*\|.*\|")
_MD_EMPHASIS_RE = re.compile(r"\*\*|__|(?<!\w)\*(?=\S)|~~")
_MD_CODE_RE = re.compile(r"`")
_MD_LINK_RE = re.compile(r"\[[^\]]*\]\([^)]*\)")
_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\U00002190-\U000021FF⬀-⯿]")


def format_violations(answer: str, spec: dict[str, Any]) -> list[str]:
    """Return the spoken-format rules the answer breaks. Empty list means compliant."""
    bad: list[str] = []
    if spec.get("no_markdown"):
        if _HEADING_RE.search(answer):
            bad.append("markdown heading")
        if _MD_EMPHASIS_RE.search(answer):
            bad.append("markdown emphasis/asterisks")
        if _MD_CODE_RE.search(answer):
            bad.append("code formatting")
        if _MD_LINK_RE.search(answer):
            bad.append("markdown link")
        if _TABLE_RE.search(answer):
            bad.append("table")
        if _EMOJI_RE.search(answer):
            bad.append("emoji")
    if spec.get("no_lists") and _LIST_ITEM_RE.search(answer):
        bad.append("list or enumeration")
    sentences = split_sentences(answer)
    max_sentences = spec.get("max_sentences")
    if isinstance(max_sentences, int) and len(sentences) > max_sentences:
        bad.append(f"{len(sentences)} sentences (limit {max_sentences})")
    words = len(answer.split())
    max_words = spec.get("max_words")
    if isinstance(max_words, int) and words > max_words:
        bad.append(f"{words} words (limit {max_words})")
    return bad


_ABBREV = re.compile(r"\b(?:Mr|Mrs|Ms|Dr|St|No|vs|etc|e\.g|i\.e)\.$", re.IGNORECASE)


def split_sentences(text: str) -> list[str]:
    parts: list[str] = []
    buf = ""
    for chunk in re.split(r"(?<=[.!?])\s+", text.strip()):
        buf = (buf + " " + chunk).strip() if buf else chunk
        if _ABBREV.search(buf.rstrip()):
            continue
        parts.append(buf)
        buf = ""
    if buf:
        parts.append(buf)
    return [p for p in parts if p.strip()]


# ─────────────────────────────── fact / claim matching ───────────────────────────────

def _fact_alternatives(fact: Any) -> list[Any]:
    if isinstance(fact, dict):
        if "any_of" in fact:
            return list(fact["any_of"])
        if "re" in fact:
            return [fact]
        raise FixtureError(f"required_facts entry has neither 'any_of' nor 're': {fact!r}")
    return [fact]


def fact_present(fact: Any, answer_norm: str) -> bool:
    """True when the answer expresses this required fact (literal, any_of group, or regex)."""
    for alt in _fact_alternatives(fact):
        if isinstance(alt, dict):
            if "re" in alt and re.search(alt["re"], answer_norm, re.IGNORECASE):
                return True
            if fact_present(alt.get("any_of", []), answer_norm):
                return True
            continue
        if alt.startswith("re:"):
            if re.search(alt[3:], answer_norm, re.IGNORECASE):
                return True
        elif normalize(alt) in answer_norm:
            return True
    return False


def claim_present(claim: str, answer_norm: str) -> bool:
    return normalize(claim) in answer_norm


def pattern_present(pattern: str, answer_norm: str) -> bool:
    return re.search(pattern[3:] if pattern.startswith("re:") else pattern,
                     answer_norm, re.IGNORECASE) is not None


def fact_literals(fact: Any) -> list[str | None]:
    """Literal strings a KB-traceability check can look for; None marks a regex alternative."""
    out: list[str | None] = []
    for alt in _fact_alternatives(fact):
        if isinstance(alt, dict):
            if "kb_quote" in alt:
                out.append(alt["kb_quote"])
            elif "re" in alt:
                out.append(None)
            else:
                out.extend(fact_literals(alt.get("any_of", [])))
        elif isinstance(alt, str) and not alt.startswith("re:"):
            out.append(alt)
        else:
            out.append(None)
    return out


def kb_traceable(fact: Any, kb_loose: str) -> bool | None:
    """True/False when checkable against the knowledge base; None when only a regex is given."""
    lits = fact_literals(fact)
    if any(l is not None for l in lits):
        return any(l is not None and loose(l) in kb_loose for l in lits)
    return None


# ─────────────────────────────── behaviour vocabularies ───────────────────────────────

NO_INFO_MARKERS = (
    "don't have", "do not have", "not have that", "no information", "not sure",
    "i'm not able", "i am not able", "not able to", "can't confirm", "cannot confirm",
    "can't say", "cannot say", "don't have that specific information", "isn't listed",
    "not listed", "don't show", "do not show", "unable to",
)
ESCALATE_MARKERS = (
    "counselor", "counsellor", "human", "admissions office", "admissions team",
    "connect you", "put you in touch", "call you back", "pass this on", "escalate",
    "have someone", "a colleague",
)
ACCEPT_MARKERS = (
    "of course", "understood", "no problem", "certainly", "appreciate", "i understand",
    "thanks for letting me know", "won't", "will not", "remove you", "take you off",
    "respect", "that's fine", "not a problem", "as you wish", "i'll note", "i will note",
    "i'll make sure", "i'll pass", "no pressure",
)
LEAD_MARKERS = (
    "email", "e-mail", "phone", "number", "contact", "reach you", "call you",
    "get back to you", "text you", "whatsapp", "your name", "best way",
)
CLARIFY_DEFAULT = ("which", "confirm", "did you mean", "are you referring", "do you mean",
                   "could you", "can you tell me", "clarify", "just to check")


def markers_for(case: dict[str, Any]) -> tuple[str, ...] | None:
    """The acceptable marker vocabulary for a case's expected behaviour."""
    explicit = case["expected"].get("marker_any")
    if explicit:
        return tuple(explicit)
    kind = case["expected"]["kind"]
    if kind == "escalate":
        return ESCALATE_MARKERS
    if kind == "capture-lead":
        return LEAD_MARKERS
    if kind == "refuse":
        return NO_INFO_MARKERS + ESCALATE_MARKERS + ACCEPT_MARKERS
    if kind == "clarify":
        return CLARIFY_DEFAULT
    return None


# ─────────────────────────────── verdicts ───────────────────────────────

@dataclass
class Verdict:
    case_id: str
    intent: str
    verdict: str                     # pass | fail | blocked
    reasons: tuple[str, ...] = ()
    detail: str = ""


def check_answer(case: dict[str, Any], answer: str) -> Verdict:
    """Deterministic verdict for one case. Never consults a model. Never returns a fourth state."""
    cid, intent = case["case_id"], case["intent"]
    if case["critical"] and not case.get("approved_by"):
        return Verdict(cid, intent, "blocked",
                       ("critical case with no approver (approved_by is null): ground truth "
                        "is not PO-approved, so it is never scored as pass or zero",))
    if answer is None or not str(answer).strip():
        return Verdict(cid, intent, "fail", ("empty answer",))

    reasons: list[str] = []
    answer_norm = normalize(str(answer))
    expected = case["expected"]
    kind = expected["kind"]

    # 1. forbidden content -- absolute, outside any statistical comparison (BRD-09).
    for claim in case.get("forbidden_claims", []):
        if claim_present(claim, answer_norm):
            reasons.append(f"forbidden claim present: {claim!r}")
    for pattern in case.get("forbidden_patterns", []):
        if pattern_present(pattern, answer_norm):
            reasons.append(f"forbidden pattern matched: {pattern!r}")

    # 2. required facts -- containment against the knowledge-base ground truth.
    missing = [f for f in case.get("required_facts", []) if not fact_present(f, answer_norm)]
    for fact in missing:
        label = fact if isinstance(fact, str) else json.dumps(fact, ensure_ascii=False)
        reasons.append(f"required fact missing: {label}")

    # 3. spoken format.
    for violation in format_violations(str(answer), case.get("spoken_format", {})):
        reasons.append(f"spoken-format violation: {violation}")

    # 4. the expected behaviour itself.
    markers = markers_for(case)
    if kind == "clarify" and "?" not in str(answer):
        reasons.append("expected a clarification question; the answer asks nothing")
    if kind == "capture-lead" and markers and not any(m in answer_norm for m in markers):
        reasons.append("expected the caller's contact details to be requested or confirmed; no such marker")
    if kind == "escalate" and markers and not any(m in answer_norm for m in markers):
        reasons.append("expected an offer of a human/counselor handoff; no such marker")
    if kind == "refuse":
        if not any(m in answer_norm for m in (markers or NO_INFO_MARKERS)):
            reasons.append("expected a refusal / no-information / acceptance path; none recognised")
    if kind == "answer" and markers and not any(m in answer_norm for m in markers):
        reasons.append("expected behaviour marker missing")
    if expected.get("no_question") and "?" in str(answer):
        reasons.append("expected no follow-up question; the answer asks one")
    if case["intent"] == "Call termination (sign-off)" and "?" in str(answer):
        reasons.append("a sign-off must not ask a follow-up question")

    return Verdict(cid, intent, "fail" if reasons else "pass", tuple(reasons))


# ─────────────────────────────── coverage ───────────────────────────────

def wilson_halfwidth(n: int, p: float = 0.5, z: float = Z95) -> float:
    """Worst-case 95% Wilson interval half-width for n cases. 1.0 when n == 0."""
    if n <= 0:
        return 1.0
    denom = 1.0 + z * z / n
    centre_term = p * (1 - p) / n + z * z / (4 * n * n)
    return (z / denom) * math.sqrt(centre_term)


@dataclass
class IntentCoverage:
    intent: str
    cases: int = 0
    critical: bool = False
    po_signoff_intent: bool = False
    statuses: dict[str, int] = field(default_factory=dict)
    tags: dict[str, int] = field(default_factory=dict)
    splits: dict[str, int] = field(default_factory=dict)
    verdicts: dict[str, int] = field(default_factory=dict)

    @property
    def unapproved(self) -> int:
        return self.statuses.get("PENDING_PO_SIGNOFF", 0)

    @property
    def blocked(self) -> bool:
        return self.critical and self.unapproved > 0


def coverage(cases: Sequence[dict[str, Any]]) -> dict[str, IntentCoverage]:
    out: dict[str, IntentCoverage] = {}
    for case in cases:
        cov = out.setdefault(case["intent"], IntentCoverage(case["intent"]))
        cov.cases += 1
        cov.critical = cov.critical or bool(case["critical"])
        for key, bucket in (("ground_truth_status", cov.statuses),
                            ("split", cov.splits)):
            bucket[case[key]] = bucket.get(case[key], 0) + 1
        for tag in case.get("tags", []):
            cov.tags[tag] = cov.tags.get(tag, 0) + 1
    return out


def self_test(cases: Sequence[dict[str, Any]]) -> tuple[list[str], dict[str, int]]:
    """Prove the checks discriminate without a model: a synthesized ground-truth answer must
    pass, and two mutations of it (a forbidden claim, a markdown list) must fail."""
    problems: list[str] = []
    stats = {"sampled": 0, "good_passed": 0, "forbidden_rejected": 0,
             "format_rejected": 0, "skipped_unsynthesizable": 0, "mutation_skipped": 0}
    for case in cases:
        if case["critical"] and not case.get("approved_by"):
            continue  # BLOCKED cases are never scored, by design
        pieces: list[str] = []
        for fact in case.get("required_facts", []):
            for lit in fact_literals(fact):
                if lit:
                    pieces.append(lit)
                    break
            else:
                pieces = []
                break
        if len(pieces) != len(case.get("required_facts", [])):
            stats["skipped_unsynthesizable"] += 1
            continue
        markers = markers_for(case) or ()
        body = ", ".join(pieces) if pieces else (markers[0] if markers else "Certainly.")
        if markers and not any(m in normalize(body) for m in markers):
            body += f" {markers[0]}"
        if case["expected"]["kind"] == "clarify" and "?" not in body:
            body += " - is that right?"
        if not body.rstrip().endswith((".", "!", "?")):
            body += "."
        good = check_answer(case, body)
        stats["sampled"] += 1
        if good.verdict == "pass":
            stats["good_passed"] += 1
        else:
            problems.append(f"self-test: synthesized ground-truth answer failed {case['case_id']}: "
                            f"{'; '.join(good.reasons)}")

        # Mutation A -- a forbidden claim, if the case declares one.
        claims = case.get("forbidden_claims", [])
        patterns = case.get("forbidden_patterns", [])
        if claims:
            mutated = body + f" {claims[0]}"
        elif patterns:
            mutated = body + " $1,234"
        else:
            mutated = None
        if mutated is None:
            stats["mutation_skipped"] += 1
        else:
            got = check_answer(case, mutated)
            if got.verdict == "fail":
                stats["forbidden_rejected"] += 1
            else:
                problems.append(f"self-test: forbidden content was NOT rejected on {case['case_id']}")

        # Mutation B -- a markdown list on a case that is not a sign-off turn.
        if not case["expected"].get("no_question"):
            got = check_answer(case, body + "\n- first point\n- second point")
            if got.verdict == "fail":
                stats["format_rejected"] += 1
            else:
                problems.append(f"self-test: markdown/format violation was NOT rejected on {case['case_id']}")
    return problems, stats


# ─────────────────────────────── validation ───────────────────────────────

def validate_fixture(cases: Sequence[dict[str, Any]], kb_loose: str,
                     kb_headings: set[str], intents: Sequence[str],
                     po_signoff: frozenset[str]) -> list[str]:
    """Structural + traceability problems. An empty list means the fixture is well-formed."""
    problems: list[str] = []
    known = set(intents)
    for case in cases:
        cid = case["case_id"]
        if case["intent"] not in known:
            problems.append(f"{cid}: intent {case['intent']!r} is not one of the {len(known)} catalog intents")
        if case["expected"].get("kind") not in KINDS:
            problems.append(f"{cid}: unknown expected.kind {case['expected'].get('kind')!r}")
        if case["ground_truth_status"] not in STATUSES:
            problems.append(f"{cid}: unknown ground_truth_status {case['ground_truth_status']!r}")
        if case["split"] not in SPLITS:
            problems.append(f"{cid}: unknown split {case['split']!r}")
        for tag in case.get("tags", []):
            if tag not in TAGS:
                problems.append(f"{cid}: unknown tag {tag!r}")
        if case["critical"] != (case["intent"] in known and case["intent"] in catalog_critical_cache):
            problems.append(f"{cid}: critical flag disagrees with the intent catalog")
        status = case["ground_truth_status"]
        if case["intent"] in po_signoff and status not in ("PENDING_PO_SIGNOFF", "PO_APPROVED"):
            problems.append(
                f"{cid}: {case['intent']} is a PO sign-off intent; its ground truth must be "
                f"PENDING_PO_SIGNOFF or PO_APPROVED, not {status!r} ('verified' means a "
                f"mechanical KB transcription and claims no human judgement)")
        # The approver field belongs only to a case that is actually approved.
        # This still refuses a self-declared approval on a pending case, which was
        # the original intent of this check -- it just no longer refuses the
        # legitimate approval as well.
        if case.get("approved_by") and status != "PO_APPROVED":
            problems.append(f"{cid}: approved_by is set ({case['approved_by']!r}) but the status is "
                            f"{status!r}; only PO_APPROVED may record an approver")
        if status == "PO_APPROVED" and not (case.get("approved_by") or "").strip():
            problems.append(f"{cid}: PO_APPROVED with no approved_by -- an approval with no name "
                            f"on it is not a sign-off")
        turns = case["utterances"]
        if not turns or turns[-1]["role"] != "caller":
            problems.append(f"{cid}: the last utterance must be the caller's")
        callers = [t["text"] for t in turns if t["role"] == "caller"]
        if case["question"] != callers[-1]:
            problems.append(f"{cid}: question must equal the final caller utterance")
        if "multi_turn" in case.get("tags", []) and len(callers) < 3:
            problems.append(f"{cid}: tagged multi_turn but has only {len(callers)} caller turns")
        if "multi_turn" not in case.get("tags", []) and len(callers) > 1:
            problems.append(f"{cid}: multiple caller turns but not tagged multi_turn")
        spec = case["spoken_format"]
        for key in ("no_markdown", "no_lists"):
            if spec.get(key) is not True:
                problems.append(f"{cid}: spoken_format.{key} must be true")
        for key in ("max_sentences", "max_words"):
            if not isinstance(spec.get(key), int) or spec[key] <= 0:
                problems.append(f"{cid}: spoken_format.{key} must be a positive integer")
        if case["ground_truth_status"] == "verified":
            if not case["ground_truth_source"].startswith("meridian_knowledge_base.md"):
                problems.append(f"{cid}: marked verified but its source is not the knowledge base")
            sections = [normalize(p.split("§", 1)[1]) for p in case["ground_truth_source"].split(";")
                        if "§" in p]
            if not sections or not any(s in kb_headings for s in sections):
                problems.append(f"{cid}: marked verified but ground_truth_source names no KB section "
                                f"({case['ground_truth_source']!r})")
            for fact in case.get("required_facts", []):
                traced = kb_traceable(fact, kb_loose)
                if traced is False:
                    label = fact if isinstance(fact, str) else json.dumps(fact, ensure_ascii=False)
                    problems.append(f"{cid}: marked verified but this required fact is not in the "
                                    f"knowledge base: {label}")
    return problems


catalog_critical_cache: set[str] = set()


# ─────────────────────────────── reporting ───────────────────────────────

def _fmt_pct(x: float) -> str:
    return f"{100 * x:5.1f}%"


def report_fixture(cases, held, intents, po_signoff, crit_note, kb_problems) -> None:
    print("=" * 78)
    print("GOLDEN SET -- deterministic checks (eval/checks.py)")
    print("=" * 78)
    digest = hashlib.sha256(GOLDEN_PATH.read_bytes()).hexdigest()
    print(f"file            : {GOLDEN_PATH}")
    print(f"cases           : {len(cases)}")
    print(f"content sha256  : {digest}")
    print("freeze status   : NOT FROZEN -- ground truth for the critical intents is not approved")
    print(f"critical note   : {crit_note}")
    print()

    print("-- fixture validity " + "-" * 58)
    if kb_problems:
        for p in kb_problems:
            print(f"  PROBLEM  {p}")
    else:
        print("  every case is well-formed; no case was skipped")

    print()
    print("-- per-intent coverage " + "-" * 55)
    print(f"  {'intent':<38} {'n':>3} {'crit':>4} {'appr':>4} {'verif':>5} {'pend':>5} {'held':>4} {'CI+-':>6}  flags")
    cov = coverage(cases)
    underpowered: list[str] = []
    blocked: list[str] = []
    for intent in intents:
        c = cov.get(intent)
        if c is None:
            print(f"  {intent:<38} {0:>3} {'':>4} {'':>4} {'':>5} {'':>5} {'':>4} {'':>6}  UNCOVERED")
            underpowered.append(intent)
            continue
        half = wilson_halfwidth(c.cases)
        flags = []
        if _is_blocked(c, po_signoff):
            flags.append("BLOCKED (ground truth PENDING PO SIGN-OFF)")
            blocked.append(intent)
        if c.cases < min_cases:
            flags.append(f"UNDERPOWERED (<{min_cases} cases, BRD-09)")
            underpowered.append(intent)
        if not c.critical:
            flags.append("coverage only")
        print(f"  {intent:<38} {c.cases:>3} {'yes' if c.critical else '-':>4} "
              f"{c.statuses.get('PO_APPROVED', 0):>4} "
              f"{c.statuses.get('verified', 0):>5} {c.statuses.get('PENDING_PO_SIGNOFF', 0):>5} "
              f"{c.splits.get('held_out', 0):>4} {_fmt_pct(half):>6}  {'; '.join(flags)}")

    print()
    tags: dict[str, int] = {}
    for case in cases:
        for tag in case.get("tags", []):
            tags[tag] = tags.get(tag, 0) + 1
    print("-- category quotas " + "-" * 60)
    for tag in TAGS:
        print(f"  {tag:<14} {tags.get(tag, 0):>3}")

    print()
    print("-- held-out split " + "-" * 60)
    print(f"  {HELDOUT_PATH.name}: {len(held)} cases of {len(cases)} "
          f"({100 * len(held) / len(cases):.1f}%) -- never used for tuning")

    print()
    print("-- headline " + "-" * 65)
    verified = sum(1 for c in cases if c["ground_truth_status"] == "verified")
    pending = sum(1 for c in cases if c["ground_truth_status"] == "PENDING_PO_SIGNOFF")
    approved = sum(1 for c in cases if c["ground_truth_status"] == "PO_APPROVED")
    print(f"  cases                 : {len(cases)}")
    print(f"  PO approved           : {approved}  (a human signed these; carries approved_by)")
    print(f"  KB verified           : {verified}  (mechanical transcription; no human judgement claimed)")
    print(f"  pending sign-off      : {pending}  (NOT approved; nothing here is signed off)")
    print(f"  blocked intents       : {len(blocked)} of "
          f"{sum(1 for i in intents if i in set(catalog_critical_cache))} critical")
    print(f"  underpowered intents  : {len(underpowered)} of {len(intents)}")
    print("  adoption rule         : BRD-09 -- no result from a blocked or underpowered intent may "
          "justify adoption")


def _is_blocked(cov: IntentCoverage, po_signoff: frozenset[str]) -> bool:
    return cov.critical and cov.unapproved > 0


# ─────────────────────────────── main ───────────────────────────────

def main(argv: Sequence[str] | None = None) -> int:
    global min_cases
    ap = argparse.ArgumentParser(description="Deterministic checks for the DG-03 golden set.")
    ap.add_argument("--golden", type=Path, default=GOLDEN_PATH)
    ap.add_argument("--heldout", type=Path, default=HELDOUT_PATH)
    ap.add_argument("--answers", type=Path, default=None,
                    help="JSONL of {case_id, answer} to score; omit to validate the fixture only")
    ap.add_argument("--min-cases", type=int, default=DEFAULT_MIN_CASES,
                    help=f"sample floor per intent before it is reported underpowered "
                         f"(default {DEFAULT_MIN_CASES})")
    ap.add_argument("--json", type=Path, default=None, help="write the report as JSON here")
    ap.add_argument("--no-self-test", action="store_true")
    args = ap.parse_args(argv)
    min_cases = args.min_cases

    try:
        cases = load_cases(args.golden)
        held = load_heldout(args.heldout)
        intents, po_signoff, cat_note = load_catalog()
        crit, crit_note = catalog_critical()
        catalog_critical_cache.clear()
        catalog_critical_cache.update(crit)
    except FixtureError as exc:
        print(f"FIXTURE ERROR: {exc}", file=sys.stderr)
        return EXIT_FIXTURE

    kb_text = KB_PATH.read_text(encoding="utf-8") if KB_PATH.exists() else ""
    kb_loose = loose(kb_text)
    kb_headings = {normalize(m.group(2)) for m in
                   re.finditer(r"(?m)^(#{2,3})\s+(.+?)\s*$", kb_text)}

    problems = validate_fixture(cases, kb_loose, kb_headings, intents, po_signoff)
    problems.extend(_check_heldout(cases, held))

    report_fixture(cases, held, intents, po_signoff, crit_note, problems)
    print(f"  catalog        : {cat_note}")

    stats: dict[str, int] = {}
    if not args.no_self_test:
        st_problems, stats = self_test(cases)
        problems.extend(st_problems)
        print()
        print("-- checker self-test (no model) " + "-" * 46)
        print(f"  cases sampled          : {stats['sampled']} (BLOCKED cases are not scored, by design)")
        print(f"  synthesized truth pass : {stats['good_passed']}")
        print(f"  forbidden claim caught : {stats['forbidden_rejected']}")
        print(f"  format violation caught: {stats['format_rejected']}")
        print(f"  not synthesizable      : {stats['skipped_unsynthesizable']} (regex-only facts)")
        print(f"  no mutation available  : {stats['mutation_skipped']} (case declares no forbidden content)")

    verdicts: list[Verdict] = []
    if args.answers:
        payload = _load_answers(args.answers)
        unknown = sorted(set(payload) - {c["case_id"] for c in cases})
        if unknown:
            print(f"FIXTURE ERROR: answers name unknown cases: {', '.join(unknown[:5])}", file=sys.stderr)
            return EXIT_FIXTURE
        # Partial runs are reported as partial: an unscored case is unscored, never a fail and
        # never extrapolated to the whole set (US-003 T-5, TAC-3).
        verdicts = [check_answer(c, payload[c["case_id"]]) for c in cases
                    if c["case_id"] in payload]
        _report_scores(cases, verdicts, intents)

    if problems:
        print()
        print(f"RESULT: {len(problems)} problem(s) -- see above", file=sys.stderr)
        for p in problems[:40]:
            print(f"  - {p}", file=sys.stderr)
        if args.json:
            _write_json(args, cases, held, intents, problems, verdicts)
        return EXIT_FIXTURE
    rc = EXIT_OK
    if verdicts:
        if any(v.verdict == "blocked" for v in verdicts):
            rc = EXIT_BLOCKED
        if any(v.verdict == "fail" for v in verdicts):
            rc = EXIT_FAIL
    if args.json:
        _write_json(args, cases, held, intents, problems, verdicts)
    return rc


def _check_heldout(cases: Sequence[dict[str, Any]], held: Sequence[str]) -> list[str]:
    problems: list[str] = []
    by_id = {c["case_id"]: c for c in cases}
    for cid in held:
        case = by_id.get(cid)
        if case is None:
            problems.append(f"held-out split names unknown case {cid}")
        elif case["split"] != "held_out":
            problems.append(f"{cid}: in heldout.txt but its split field says {case['split']!r}")
    held_set = set(held)
    for case in cases:
        if case["split"] == "held_out" and case["case_id"] not in held_set:
            problems.append(f"{case['case_id']}: split is held_out but it is not in heldout.txt")
    share = len(held) / max(len(cases), 1)
    if not 0.15 <= share <= 0.25:
        problems.append(f"held-out split is {share:.1%} of the set; the frozen rule is 20%")
    return problems


def _load_answers(path: Path) -> dict[str, str]:
    payload: dict[str, str] = {}
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise FixtureError(f"{path.name}:{lineno}: malformed JSON ({exc.msg})") from exc
        cid = row.get("case_id")
        if not cid:
            raise FixtureError(f"{path.name}:{lineno}: answer row has no case_id")
        payload[cid] = row.get("answer", "")
    return payload


def _report_scores(cases, verdicts: Sequence[Verdict], intents: Sequence[str]) -> None:
    cov = coverage(cases)
    print()
    print("-- scored answers " + "-" * 60)
    for v in verdicts:
        if v.verdict == "fail":
            print(f"  FAIL  {v.case_id}: {'; '.join(v.reasons)}")
    per: dict[str, dict[str, int]] = {}
    for v in verdicts:
        b = per.setdefault(v.intent, {"pass": 0, "fail": 0, "blocked": 0})
        b[v.verdict] += 1
    print()
    print(f"  {'intent':<38} {'pass':>5} {'fail':>5} {'block':>6}  note")
    for intent in intents:
        b = per.get(intent)
        if not b:
            continue
        note = ""
        if cov[intent].blocked:
            note = "BLOCKED -- missing/unapproved ground truth; not a score of zero"
        print(f"  {intent:<38} {b['pass']:>5} {b['fail']:>5} {b['blocked']:>6}  {note}")
    tp = sum(1 for v in verdicts if v.verdict == "pass")
    tf = sum(1 for v in verdicts if v.verdict == "fail")
    tb = sum(1 for v in verdicts if v.verdict == "blocked")
    print(f"  cases_scored: {len(verdicts)} of {len(cases)}"
          + ("  (PARTIAL run -- per-intent coverage is stated above and is never extrapolated to "
             "the whole set)" if len(verdicts) < len(cases) else ""))
    print(f"  totals: {tp} pass, {tf} fail, {tb} blocked")
    print("  note  : deterministic failures are absolute blockers (BRD-09); blocked intents are "
          "reported, never scored as zero")


def _write_json(args, cases, held, intents, problems, verdicts) -> None:
    _, po_signoff, _ = load_catalog()
    still_blocked = sorted(k for k, v in coverage(cases).items() if _is_blocked(v, po_signoff))
    payload = {
        "golden_set": str(GOLDEN_PATH),
        "sha256": hashlib.sha256(GOLDEN_PATH.read_bytes()).hexdigest(),
        "frozen": not still_blocked,
        "frozen_reason": ("all critical-intent ground truth is approved"
                          if not still_blocked else
                          "critical-intent ground truth is PENDING PO SIGN-OFF (DG-03): "
                          + ", ".join(still_blocked)),
        "cases_total": len(cases),
        "held_out": len(held),
        "po_approved": sum(1 for c in cases if c["ground_truth_status"] == "PO_APPROVED"),
        "problems": problems,
        "per_intent": {
            k: {"cases": v.cases, "critical": v.critical,
                "po_approved": v.statuses.get("PO_APPROVED", 0),
                "verified": v.statuses.get("verified", 0),
                "pending": v.statuses.get("PENDING_PO_SIGNOFF", 0),
                "ci_half_width_worst_case": round(wilson_halfwidth(v.cases), 4)}
            for k, v in coverage(cases).items()
        },
        "verdicts": [{"case_id": v.case_id, "intent": v.intent, "verdict": v.verdict,
                      "reasons": list(v.reasons)} for v in verdicts],
    }
    if args.json:
        args.json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return None


if __name__ == "__main__":
    raise SystemExit(main())
