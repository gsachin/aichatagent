"""US-015 quality leg — score a model on the golden set, and report the grade.

`us015_model_benchmark.py` measures the LATENCY leg and says plainly that
quality "needs the frozen golden set (`DG-03`)". That is true of an
**adoption-grade** claim: no result from this set can justify adopting a model
until the Product Owner has approved the ground truth, and 137 of 161 cases are
still `PENDING_PO_SIGNOFF`.

It is not true of a **screen**. Two subsets carry a quality basis that does not
depend on that approval, and this file scores them separately from the rest so
the grade of the evidence travels with the number:

  `verified`       24 cases. The ground truth is a mechanical transcription from
                   the knowledge base, and `checks.py` refuses the fixture if any
                   required fact is not found there. Nobody's judgement is
                   claimed -- and none is needed for a fact that is either in the
                   KB or not.
  `kb_traceable`   59 cases, `PENDING_PO_SIGNOFF`, whose every required fact
                   still appears verbatim in the knowledge base. The *behaviour*
                   is unapproved; the *facts* are checkable.
  everything else  reported, and labelled as what it is: unapproved ground
                   truth, screen-grade only.

    24 + 59 = 83 cases carry a quality basis. They do NOT all produce evidence.
    `check_answer` returns `blocked` for any case that is `critical` without an
    `approved_by`, and that is 53 of the 83 -- so the verdicts that actually
    separate two models are the remaining 30. The blocked count is printed
    beside the pass count for exactly this reason: a pass rate quoted over 83
    would be a lie of arithmetic, not of intent.

So a model can be screened here, and cannot be adopted here. The distinction is
the whole point of `DG-03` and this file does not quietly collapse it.

**Per-case records are written, not just tallies.** A tally cannot answer the
only question that matters between two candidates -- whether they differ, and
on which cases -- and a capped failure list silently drops the tail. The JSON
carries every case's verdict, reasons and counters so the comparison can be
made properly (`us015_pairwise.py`).

Run:  .venv/Scripts/python.exe doc/perf/tools/us015_quality_screen.py
      .venv/Scripts/python.exe doc/perf/tools/us015_quality_screen.py --models qwen2.5:14b,llama3.2:3b
      .venv/Scripts/python.exe doc/perf/tools/us015_quality_screen.py --num-predict 120 --temperature 0.1
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import statistics
import sys
import time
from pathlib import Path

PROJ = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJ))
sys.path.insert(0, str(PROJ / "eval"))
os.chdir(PROJ)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(".env")

import checks as C  # noqa: E402

DEFAULT_MODELS = ["qwen2.5:14b", "llama3.2:3b"]


def grade_of(case: dict, kb_loose: str) -> str:
    """Which evidence grade this case's ground truth carries."""
    if case["ground_truth_status"] == "verified":
        return "verified"
    facts = case.get("required_facts") or []
    if facts and all(C.kb_traceable(f, kb_loose) is True for f in facts):
        return "kb_traceable"
    return "unapproved"


def ask(prompt: str, model: str, opts: dict) -> tuple[str, dict]:
    """One generation, with the engine's own counters returned alongside."""
    import ollama

    t0 = time.perf_counter()
    resp = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        options=dict(opts),
        keep_alive=-1,
    )
    wall_ms = (time.perf_counter() - t0) * 1000.0
    counters = {
        "wall_ms": round(wall_ms, 1),
        "prefill_ms": round(resp.get("prompt_eval_duration", 0) / 1e6, 1),
        "generation_ms": round(resp.get("eval_duration", 0) / 1e6, 1),
        "prompt_tokens": resp.get("prompt_eval_count"),
        "eval_tokens": resp.get("eval_count"),
        "load_ms": round(resp.get("load_duration", 0) / 1e6, 1),
    }
    return resp["message"]["content"], counters


def main() -> int:
    ap = argparse.ArgumentParser(description="US-015 quality screen over the golden set.")
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS))
    ap.add_argument("--num-ctx", type=int, default=int(os.environ.get("OLLAMA_NUM_CTX", "8192")))
    ap.add_argument("--temperature", type=float, default=0.3)
    ap.add_argument("--num-predict", type=int, default=-1,
                    help="cap generated tokens (-1 = engine default). A 2-sentence spoken "
                         "answer does not need 400; the cap is both a format guard and the "
                         "largest single lever on generation time.")
    ap.add_argument("--top-p", type=float, default=None)
    ap.add_argument("--seed", type=int, default=None, help="fix the sampler for a paired comparison")
    ap.add_argument("--tag", default="", help="label this config in the output file")
    ap.add_argument("--limit", type=int, default=0, help="cap the case count (0 = all scorable)")
    ap.add_argument("--out", default="doc/perf/us015-quality-screen.json")
    args = ap.parse_args()

    opts: dict = {"num_ctx": args.num_ctx, "temperature": args.temperature}
    if args.num_predict and args.num_predict > 0:
        opts["num_predict"] = args.num_predict
    if args.top_p is not None:
        opts["top_p"] = args.top_p
    if args.seed is not None:
        opts["seed"] = args.seed

    cases = C.load_cases()
    kb_text = C.KB_PATH.read_text(encoding="utf-8")
    kb_loose = C.loose(kb_text)

    graded: list[tuple[dict, str]] = []
    for c in cases:
        g = grade_of(c, kb_loose)
        if g in ("verified", "kb_traceable"):
            graded.append((c, g))
    if args.limit:
        graded = graded[:args.limit]

    print("=" * 78)
    print("US-015 quality screen -- SCREEN-GRADE, not adoption-grade")
    print("=" * 78)
    print(f"  scorable cases : {len(graded)}")
    for g in ("verified", "kb_traceable"):
        print(f"    {g:<14}: {sum(1 for _, x in graded if x == g)}")
    print(f"  options        : {opts}")
    print(f"  models         : {', '.join(args.models.split(','))}")
    print()
    print("  This is a screen. No case here has Product-Owner approval, and no")
    print("  result from this file justifies adopting a model. What it can do is")
    print("  separate two candidates by a margin large enough to act on.")

    results: dict = {}
    for model in [m.strip() for m in args.models.split(",") if m.strip()]:
        print(f"\n-- {model} " + "-" * (60 - len(model)))
        tally = collections.Counter()
        by_grade = collections.defaultdict(collections.Counter)
        lat: list[dict] = []
        rows: list[dict] = []
        failures: list[str] = []
        t0 = time.time()

        for i, (case, grade) in enumerate(graded, 1):
            try:
                prompt = _prompt_for(case)
                answer, counters = ask(prompt, model, opts)
                v = C.check_answer(case, answer)
                verdict = getattr(v, "verdict", None) or str(v)
                reasons = [str(r) for r in (getattr(v, "reasons", None) or ())]
                tally[verdict] += 1
                by_grade[grade][verdict] += 1
                lat.append(counters)
                rows.append({
                    "case_id": case["case_id"],
                    "intent": case["intent"],
                    "grade": grade,
                    "critical": bool(case["critical"]),
                    "verdict": verdict,
                    "reasons": reasons,
                    # The answer text is kept, not just its length. Diagnosing
                    # the 14B's format failures required re-generating the cases
                    # because only a character count was stored -- and a count
                    # cannot tell a rambling model from an over-eager splitter.
                    "answer": answer,
                    "answer_chars": len(answer or ""),
                    "census": census_of(case, answer),
                    **counters,
                })
                if verdict != "pass":          # checks.py returns lowercase
                    failures.append(f"{case['case_id']} [{grade}] {verdict}: "
                                    f"{(reasons or [''])[0][:90]}")
            except Exception as exc:                      # noqa: BLE001
                tally["ERROR"] += 1
                rows.append({"case_id": case["case_id"], "grade": grade,
                             "verdict": "ERROR", "reasons": [f"{type(exc).__name__}: {exc}"]})
                failures.append(f"{case['case_id']} ERROR {type(exc).__name__}: {exc}")
            if i % 10 == 0 or i == len(graded):
                print(f"    {i}/{len(graded)}  {dict(tally)}")

        wall = time.time() - t0
        scorable = tally.get("pass", 0) + tally.get("fail", 0)
        summary = {
            "model": model,
            "options": opts,
            "tag": args.tag,
            "cases": len(graded),
            "scorable": scorable,
            "blocked": tally.get("blocked", 0),
            "verdicts": dict(tally),
            "by_grade": {g: dict(v) for g, v in by_grade.items()},
            "wall_seconds": round(wall, 1),
            "prefill_ms_p50": _p50([c["prefill_ms"] for c in lat]),
            "generation_ms_p50": _p50([c["generation_ms"] for c in lat]),
            "wall_ms_p50": _p50([c["wall_ms"] for c in lat]),
            "eval_tokens_p50": _p50([c["eval_tokens"] for c in lat]),
            "eval_tokens_max": max((c["eval_tokens"] or 0 for c in lat), default=0),
            "cold_load_ms_max": max((c["load_ms"] for c in lat), default=0),
            "rows": rows,
            "failures": failures,
        }
        results[model] = summary
        print(f"    verdicts: {dict(tally)}")
        print(f"    scorable: {scorable} (blocked {summary['blocked']} by DG-03)")
        print(f"    prefill p50 {summary['prefill_ms_p50']} ms | gen p50 "
              f"{summary['generation_ms_p50']} ms | wall p50 {summary['wall_ms_p50']} ms")

    print("\n" + "=" * 78)
    print("  MODEL                      PASS/SCORABLE   FAIL   BLOCKED  prefill  gen(p50)")
    print("  " + "-" * 74)
    for model, r in results.items():
        v = r["verdicts"]
        passed, failed = v.get("pass", 0), v.get("fail", 0)
        n = r["scorable"]
        rate = f"{passed}/{n} ({100.0 * passed / n:.0f}%)" if n else f"{passed}/0"
        print(f"  {model:<28}{rate:>13}{failed:>7}{r['blocked']:>9}"
              f"{r['prefill_ms_p50']:>9}{r['generation_ms_p50']:>10}")

    print("\n  by evidence grade")
    for model, r in results.items():
        for grade, v in sorted(r["by_grade"].items()):
            n = sum(v.values())
            print(f"    {model:<28} {grade:<14} pass {v.get('pass', 0)}/{n}")

    print("\n  DEFECT CENSUS over ALL cases -- not a score, not a pass rate")
    print("  (a blocked case stays blocked; this only counts mechanical defects)")
    for model, r in results.items():
        tax = collections.Counter()
        for row in r["rows"]:
            for k in (row.get("census") or []):
                tax[k] += 1
        clean = sum(1 for row in r["rows"] if not (row.get("census") or []))
        print(f"    {model:<28} defect-free {clean}/{r['cases']}"
              f"   " + "  ".join(f"{k}={v}" for k, v in tax.most_common()))

    print("\n  SCREEN-GRADE. No case here carries Product-Owner approval; nothing")
    print("  in this table justifies adopting a model (`DG-03`). It separates")
    print("  candidates by margin. Adoption needs the approved ground truth.")

    out = Path(args.out)
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8", newline="\n")
    print(f"\n  written -> {out}")
    return 0


def census_of(case: dict, answer: str) -> list[str]:
    """Mechanical defect census for one answer -- NOT a verdict.

    `check_answer` returns early for a critical case with no approver, so 53 of
    the 83 cases here carry no computable verdict and are useless for
    comparison. But the sub-checks it would have run are mechanical: is a
    forbidden claim present, is a required fact missing, does the answer break
    the spoken format. Those do not depend on the Product Owner's approval of
    the *expected behaviour*, and they are the same function calls `check_answer`
    makes.

    This is therefore a census of defects, and it is deliberately NOT converted
    into a pass count anywhere. A blocked case stays blocked; what is added is
    "how many answers rambled" and "how many missed a fact", which is how two
    models are told apart at a sample size the verdicts cannot reach.

    It never gates anything and is never a substitute for the verdict.
    """
    text = str(answer or "")
    norm = C.normalize(text)
    out: list[str] = []
    for claim in case.get("forbidden_claims", []):
        if C.claim_present(claim, norm):
            out.append("forbidden_claim")
    for pattern in case.get("forbidden_patterns", []):
        if C.pattern_present(pattern, norm):
            out.append("forbidden_pattern")
    if any(not C.fact_present(f, norm) for f in case.get("required_facts", [])):
        out.append("fact_missing")
    for v in C.format_violations(text, case.get("spoken_format", {})):
        out.append("format_sentences" if "sentences" in v
                   else "format_words" if "words" in v
                   else "format_other")
    return out


def _prompt_for(case: dict) -> str:
    """The prompt the VOICE path would build for this question.

    Retrieval included, because a model that answers well from a bare question
    and badly from the real prompt is not a model this stack can use.
    """
    from app.pipeline import build_rag_prompt

    return build_rag_prompt(case["question"])


def _p50(values: list) -> float | None:
    vals = [v for v in values if v]
    return round(statistics.median(vals), 1) if vals else None


if __name__ == "__main__":
    raise SystemExit(main())
