"""DG-03 sign-off packet — make the PO's ground-truth review fast and traceable.

`US-003` cannot freeze the golden set until a human verifies the critical-intent
ground truths, and 137 of 161 cases are still `PENDING_PO_SIGNOFF`. Nothing in
the program can be scored, and nothing that changes what the model returns can
ship, until that happens.

The review is not 137 equal decisions. Two very different kinds of case are
mixed together in the file:

  * KB-sourced cases whose every required fact appears verbatim in the
    knowledge base. The PO is confirming a transcription, and the quote is
    shown inline so it can be checked at a glance.
  * Behaviour-sourced cases (escalation, opt-out, barge-in, lead capture...)
    whose ground truth is a POLICY decision -- what should the assistant do
    when this happens. These have no fact to grep and are the real review.

This module separates the two, prints the evidence for each, and provides the
one thing the file lacks: a way to actually record a decision. Hand-editing
137 JSON lines is why this has stayed pending.

Standard-library only, imports nothing from `app.*` -- the same constraint
`MOD-06` puts on `checks.py`, so a mid-refactor application cannot break the
review tooling.

Usage:
    python eval/signoff_packet.py                    # write the review packet
    python eval/signoff_packet.py --summary          # metrics only
    python eval/signoff_packet.py --approve acc-006 fees-03
    python eval/signoff_packet.py --approve-intent "Fees structure"
    python eval/signoff_packet.py --approve-intent "Fees structure" --by "Pradeep"
    python eval/signoff_packet.py --reject  fees-03 --note "figure changed for 2027"
    python eval/signoff_packet.py --status           # counts, no writes
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import checks as C  # noqa: E402

GOLDEN_PATH = C.GOLDEN_PATH
PACKET_PATH = Path(__file__).resolve().parent / "DG-03-signoff-packet.md"
KB_NAME = "meridian_knowledge_base.md"


# ─────────────────────────────── classification ───────────────────────────────

def kb_evidence(fact, kb_text: str) -> tuple[str, str | None]:
    """Classify one required fact against the KB.

    Returns (verdict, quote). Verdict is one of `traced`, `missing`, `uncheckable`
    -- `uncheckable` meaning the fact is regex-only and no literal can be grepped.
    """
    literals = [l for l in C.fact_literals(fact) if l is not None]
    if not literals:
        return "uncheckable", None
    loose_kb = C.loose(kb_text)
    for lit in literals:
        if C.loose(lit) in loose_kb:
            return "traced", lit
    return "missing", literals[0]


def classify(case: dict, kb_text: str) -> dict:
    """Bucket one pending case and collect the evidence a reviewer needs."""
    src = case.get("ground_truth_source", "")
    kb_sourced = src.startswith(KB_NAME)
    facts = case.get("required_facts", [])
    evidence = [kb_evidence(f, kb_text) for f in facts]
    missing = [f for f, (v, _) in zip(facts, evidence) if v == "missing"]
    traced = [q for v, q in evidence if v == "traced"]

    if kb_sourced and facts and not missing:
        bucket = "bulk"           # confirm a transcription
    elif kb_sourced and not facts:
        bucket = "kb_behaviour"   # KB section names the topic, behaviour is the test
    else:
        bucket = "policy"         # a decision, not a look-up

    return {
        "case": case,
        "bucket": bucket,
        "kb_sourced": kb_sourced,
        "missing": missing,
        "traced": traced,
        "source": src,
    }


#: The packet presents cases AWAITING a PO decision -- exactly
#: `PENDING_PO_SIGNOFF`. A `verified` case is already usable (mechanically
#: transcribed, no judgement claimed) and was never in this packet; a
#: `PO_APPROVED` case has been decided.
#:
#: This was `!= "verified"` until 2026-09-19, which meant an approved case kept
#: counting as pending. Measured: after approving the Out-of-scope intent, nine
#: cases moved to `PO_APPROVED` and `--status` still reported `pending: 137` --
#: the before-number. A Product Owner would approve nine decisions and watch the
#: count not move, conclude the tool was broken, and be right.
#:
#: The same shape as everything else this programme has found: a number that
#: stops describing the artifact and nothing that notices.
AWAITING = ("PENDING_PO_SIGNOFF",)


def load(kb_text: str | None = None) -> tuple[list[dict], str]:
    kb_text = kb_text if kb_text is not None else C.KB_PATH.read_text(encoding="utf-8")
    cases = C.load_cases()
    pending = [c for c in cases if c["ground_truth_status"] in AWAITING]
    return [classify(c, kb_text) for c in pending], kb_text


# ─────────────────────────────── metrics ───────────────────────────────

def summarise(items: list[dict]) -> dict:
    buckets = collections.Counter(i["bucket"] for i in items)
    by_intent = collections.Counter(i["case"]["intent"] for i in items if i["bucket"] == "policy")
    return {
        "pending": len(items),
        "bulk": buckets["bulk"],
        "kb_behaviour": buckets["kb_behaviour"],
        "policy": buckets["policy"],
        "policy_by_intent": dict(by_intent.most_common()),
        # Only a KB-SOURCED case can be wrong for quoting something absent from
        # the KB. A behaviour case cites the system prompt, so its "facts" are
        # things the assistant must say, not things the KB contains -- counting
        # those here would raise a defect every time a case tests general
        # knowledge (adv-007's "Paris" is exactly that, and is correct).
        "untraceable": sum(len(i["missing"]) for i in items if i["kb_sourced"]),
        "off_kb": [(i["case"]["case_id"], f) for i in items if not i["kb_sourced"] for f in i["missing"]],
    }


def report(items: list[dict]) -> None:
    s = summarise(items)
    print("=" * 78)
    print("DG-03 -- what actually stands between here and a frozen golden set")
    print("=" * 78)
    print(f"  pending cases                     : {s['pending']}")
    print(f"  KB transcription (bulk-approvable): {s['bulk']}")
    print(f"  KB topic, behaviour is the test   : {s['kb_behaviour']}")
    print(f"  POLICY decisions (the real review): {s['policy']}")
    print(f"  required facts absent from the KB : {s['untraceable']}")
    print()
    print("  the policy decisions, by intent:")
    for intent, n in s["policy_by_intent"].items():
        print(f"    {n:>3}  {intent}")
    print()
    if s["untraceable"]:
        print("  !! a KB-sourced case quotes something the knowledge base does not contain:")
        for i in items:
            if not i["kb_sourced"]:
                continue
            for f in i["missing"]:
                print(f"     {i['case']['case_id']}: {json.dumps(f, ensure_ascii=False)}")
    if s["off_kb"]:
        print("  note: behaviour-sourced cases whose required output is general knowledge,")
        print("        not Meridian knowledge. Expected, and not a defect:")
        for cid, f in s["off_kb"]:
            print(f"     {cid}: {json.dumps(f, ensure_ascii=False)}")


# ─────────────────────────────── packet ───────────────────────────────

def _fmt_fact(fact) -> str:
    if isinstance(fact, str):
        return fact
    return json.dumps(fact, ensure_ascii=False)


def write_packet(items: list[dict]) -> Path:
    s = summarise(items)
    out: list[str] = []
    w = out.append

    w("# DG-03 — ground-truth sign-off packet")
    w("")
    w(f"**{s['pending']} cases await your decision.** Until they are approved the golden set")
    w("cannot be frozen, no result from it is adoption-grade, and five stories — including")
    w("the two largest latency levers, `US-004` and `US-009` — stay blocked.")
    w("")
    w("This packet sorts them by *what kind of decision each one is*, because they are not")
    w("137 equal judgements:")
    w("")
    w("| Kind | n | What you are deciding |")
    w("|---|---|---|")
    w(f"| KB transcription | {s['bulk']} | The required facts are already in the knowledge base, quoted below. You are confirming the transcription is right. |")
    w(f"| KB topic, behaviour tested | {s['kb_behaviour']} | The topic is in the KB; the test is how the assistant behaves. |")
    w(f"| **Policy decision** | **{s['policy']}** | **No fact to look up. You are deciding what the assistant should do.** |")
    w("")
    w("## How to record a decision")
    w("")
    w("```powershell")
    w("# approve a whole intent at once")
    w('.venv/Scripts/python.exe eval/signoff_packet.py --approve-intent "Fees structure" --by "Your Name"')
    w("")
    w("# approve individual cases")
    w(".venv/Scripts/python.exe eval/signoff_packet.py --approve fees-03 dates-02")
    w("")
    w("# send one back with a reason")
    w('.venv/Scripts/python.exe eval/signoff_packet.py --reject fees-03 --note "fee changed for 2027"')
    w("")
    w("# see where things stand")
    w(".venv/Scripts/python.exe eval/signoff_packet.py --status")
    w("```")
    w("")

    # ── policy decisions first: they are the work
    w("---")
    w("")
    w(f"## 1 · Policy decisions ({s['policy']})")
    w("")
    w("These carry no knowledge-base fact. Each is a choice about how the assistant")
    w("should behave, and each one changes what callers hear.")
    w("")
    by_intent: dict[str, list[dict]] = collections.defaultdict(list)
    for i in items:
        if i["bucket"] == "policy":
            by_intent[i["case"]["intent"]].append(i)
    for intent, group in sorted(by_intent.items(), key=lambda kv: -len(kv[1])):
        w(f"### {intent} — {len(group)} cases")
        w("")
        for i in group:
            c = i["case"]
            expected = c.get("expected", {})
            w(f"**`{c['case_id']}`** — _{c['question']}_")
            w("")
            notes = expected.get("notes")
            if notes:
                w(f"- expected: {notes}")
            marker = expected.get("marker_any") or expected.get("marker_all")
            if marker:
                w(f"- the answer must contain: {', '.join(repr(m) for m in marker)}")
            if c.get("forbidden_claims"):
                w(f"- must NOT say: {', '.join(repr(x) for x in c['forbidden_claims'])}")
            if c.get("required_facts"):
                w(f"- required facts: {', '.join(_fmt_fact(f) for f in c['required_facts'])}")
            w(f"- source: `{i['source']}`")
            w("")

    # ── KB transcriptions: bulk clear
    w("---")
    w("")
    w(f"## 2 · KB transcriptions ({s['bulk']}) — bulk-approvable")
    w("")
    w("Every required fact below was found in the knowledge base, and the quoted text is")
    w("shown so it can be checked without opening the file. Approving these in bulk is")
    w("reasonable; the residual risk is that a fact is *present* but used in the wrong sense.")
    w("")
    by_intent2: dict[str, list[dict]] = collections.defaultdict(list)
    for i in items:
        if i["bucket"] in ("bulk", "kb_behaviour"):
            by_intent2[i["case"]["intent"]].append(i)
    for intent, group in sorted(by_intent2.items(), key=lambda kv: -len(kv[1])):
        w(f"### {intent} — {len(group)} cases")
        w("")
        cmds = " ".join(c["case"]["case_id"] for c in group)
        w("```powershell")
        w(f'.venv/Scripts/python.exe eval/signoff_packet.py --approve {cmds} --by "Your Name"')
        w("```")
        w("")
        w("| case | question | facts found in the KB |")
        w("|---|---|---|")
        for i in group:
            c = i["case"]
            q = c["question"].replace("|", "\\|")
            if len(q) > 70:
                q = q[:67] + "..."
            facts = ", ".join(f"`{t}`" for t in i["traced"]) or "_(none — behaviour test)_"
            w(f"| `{c['case_id']}` | {q} | {facts} |")
        w("")

    w("---")
    w("")
    w("## Why this blocks more than a score")
    w("")
    w("`US-004` streams generation to the caller — it changes *what the model returns*, so it")
    w("is gated on a quality baseline that cannot run until these are approved. The same is")
    w("true of `US-005`, `US-009`, `US-010` and `US-018`. Measured on this stack, generation")
    w("is 1,202 ms of the 4,684 ms a caller waits, and retrieval is another 990 ms — the two")
    w("largest addressable terms, both frozen behind this decision.")
    w("")

    PACKET_PATH.write_text("\n".join(out), encoding="utf-8", newline="\n")
    return PACKET_PATH


# ─────────────────────────────── recording a decision ───────────────────────────────

def _rewrite(updates: dict[str, dict]) -> int:
    """Apply per-case field updates to golden_set.jsonl, preserving line order."""
    lines = GOLDEN_PATH.read_text(encoding="utf-8").splitlines()
    changed = 0
    out = []
    for line in lines:
        if not line.strip():
            out.append(line)
            continue
        case = json.loads(line)
        patch = updates.get(case["case_id"])
        if patch:
            case.update(patch)
            changed += 1
            line = json.dumps(case, ensure_ascii=False)
        out.append(line)
    GOLDEN_PATH.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
    return changed


def approve(ids: list[str], by: str) -> int:
    """Record a PO approval.

    Writes `PO_APPROVED` -- NOT `verified`. The two are different provenances:
    `verified` means the ground truth was transcribed mechanically from the
    knowledge base and claims no human judgement, while `PO_APPROVED` means a
    named person read the case and signed it. `checks.py` enforces the
    distinction, and only `PO_APPROVED` carries an approver.
    """
    return _rewrite({cid: {"ground_truth_status": "PO_APPROVED", "approved_by": by} for cid in ids})


def reject(ids: list[str], note: str) -> int:
    return _rewrite({cid: {"ground_truth_status": "REJECTED", "rejection_note": note} for cid in ids})


def ids_for_intent(items: list[dict], intent: str) -> list[str]:
    want = intent.strip().lower()
    return [i["case"]["case_id"] for i in items if i["case"]["intent"].lower() == want]


def intents_available(items: list[dict]) -> list[str]:
    return sorted({i["case"]["intent"] for i in items})


# ─────────────────────────────── cli ───────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="DG-03 sign-off packet and recorder")
    ap.add_argument("--summary", action="store_true", help="print metrics, write nothing")
    ap.add_argument("--status", action="store_true", help="counts only, no writes")
    ap.add_argument("--approve", nargs="+", metavar="CASE_ID")
    ap.add_argument("--approve-intent", metavar="INTENT")
    ap.add_argument("--reject", nargs="+", metavar="CASE_ID")
    ap.add_argument("--note", default="", help="reason, for --reject")
    ap.add_argument("--by", default="", help="who approved (recorded in approved_by)")
    args = ap.parse_args(argv)

    items, _ = load()

    if args.status:
        s = summarise(items)
        total = len(C.load_cases())
        verified = sum(1 for c in C.load_cases() if c["ground_truth_status"] == "verified")
        print(f"golden set        : {total} cases")
        print(f"verified          : {verified}")
        print(f"pending           : {s['pending']}")
        return 0

    if args.summary:
        report(items)
        return 0

    if args.approve or args.approve_intent:
        if not args.by:
            print("refusing: --by is required. An approval with no name on it is not a sign-off.",
                  file=sys.stderr)
            return 2
        ids = list(args.approve or [])
        if args.approve_intent:
            matched = ids_for_intent(items, args.approve_intent)
            if not matched:
                print(f"no pending cases for intent {args.approve_intent!r}.", file=sys.stderr)
                print("available:", "; ".join(intents_available(items)), file=sys.stderr)
                return 2
            ids.extend(matched)
        n = approve(ids, args.by)
        print(f"approved {n} case(s) by {args.by!r}")
        print("re-run eval/checks.py to see the blocked intents clear.")
        return 0

    if args.reject:
        n = reject(args.reject, args.note)
        print(f"rejected {n} case(s)" + (f": {args.note}" if args.note else ""))
        return 0

    path = write_packet(items)
    report(items)
    print()
    print(f"  review packet written to: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
