"""
Lead categorization engine.

Maps composite sentiment scores to actionable lead categories
following the Voice AI Lead Scoring integration guide (v0.5.0).

Category precedence (first match wins):
  1. Disqualified — S_lead < −0.20 OR explicit non-fit objection
  2. At-Risk      — delta_S < −0.35 OR rising friction
  3. Hot          — S_lead ≥ 0.70 AND P_convert ≥ 0.55 AND positive delta_S AND strong BANT
  4. Warm         — 0.35 ≤ S_lead < 0.70 OR S_lead ≥ 0.70 without full Hot criteria
  5. Nurture      — fallthrough (S_lead < 0.35, no risk signals)

Usage:
    from app.sentiment.categorizer import categorize, explain_categorization

    category = categorize(s_lead=0.78, delta_s=0.12, p_convert=0.82, friction=0.10)
    explanation = explain_categorization(s_lead=0.78, ...)
"""

from __future__ import annotations

import logging
import os
from enum import Enum

logger = logging.getLogger("sentiment.categorizer")


class LeadCategory(str, Enum):
    HOT = "Hot"
    WARM = "Warm"
    NURTURE = "Nurture"
    AT_RISK = "At-Risk"
    DISQUALIFIED = "Disqualified"


CATEGORY_ACTIONS: dict[str, str] = {
    "Hot": "Assign to AE immediately — S_lead ≥ 0.70, strong BANT, positive momentum",
    "Warm": "Follow up, nurture — good score with some gaps to fill",
    "Nurture": "Add to drip campaign — neutral/low score, no risk signals",
    "At-Risk": "Escalate to CS, intervene — sharp sentiment drop or rising friction",
    "Disqualified": "Suppress for 90 days — S_lead < −0.20 or explicit non-fit",
}


def categorize(
    s_lead: float,
    delta_s: float | None = None,
    p_convert: float | None = None,
    friction: float = 0.0,
    trajectory: str = "Stable",
    objections: list[str] | None = None,
) -> str:
    """
    Categorize a lead based on composite sentiment scores.

    Parameters:
        s_lead:     Composite lead score [-1, 1]
        delta_s:    Momentum (change from EWMA baseline). None for first call.
        p_convert:  Conversion probability [0, 1]. None if predictive layer inactive.
        friction:   Friction/resistance score [0, 1]
        trajectory: Trajectory label (Upward/Stable/Degrading/Volatile)
        objections: List of objection strings from extraction

    Returns one of: "Hot", "Warm", "Nurture", "At-Risk", "Disqualified"
    """
    objections = objections or []

    # ── 1. Disqualified ─────────────────────────────────────────
    non_fit_keywords = ["not interested", "wrong fit", "different field",
                        "scam", "fraud", "not a student", "wrong number"]
    has_non_fit = any(
        kw in " ".join(objections).lower() for kw in non_fit_keywords
    )

    if s_lead < -0.20 or has_non_fit:
        return LeadCategory.DISQUALIFIED.value

    # ── 2. At-Risk ─────────────────────────────────────────────
    if delta_s is not None and delta_s < -0.35:
        return LeadCategory.AT_RISK.value

    if friction > 0.70 and trajectory in ("Degrading", "Volatile"):
        return LeadCategory.AT_RISK.value

    # ── 3. Hot ─────────────────────────────────────────────────
    d = delta_s if delta_s is not None else 0.0
    p = p_convert if p_convert is not None else 0.0

    if (s_lead >= 0.70
            and p >= 0.55
            and d > 0
            and trajectory in ("Upward", "Stable")
            and friction < 0.40):
        return LeadCategory.HOT.value

    # ── 4. Warm ────────────────────────────────────────────────
    if s_lead >= 0.35:
        return LeadCategory.WARM.value

    # ── 5. Nurture (fallthrough) ───────────────────────────────
    return LeadCategory.NURTURE.value


def explain_categorization(
    s_lead: float,
    delta_s: float | None = None,
    p_convert: float | None = None,
    friction: float = 0.0,
    trajectory: str = "Stable",
    objections: list[str] | None = None,
    bant_completeness: float = 0.0,
    buying_intent_score: float = 0.0,
    primary_emotion: str = "",
) -> dict:
    """
    Generate a grounded, human-readable explanation for a lead's category.

    Returns a dict with 'rationale' and 'grounded' fields suitable for
    the /api/sentiment/explain-categorization endpoint.
    """
    category = categorize(
        s_lead=s_lead, delta_s=delta_s, p_convert=p_convert,
        friction=friction, trajectory=trajectory, objections=objections,
    )

    d = delta_s if delta_s is not None else 0.0
    p = p_convert if p_convert is not None else 0.0

    checks = []

    # Build the decision trace
    non_fit_keywords = ["not interested", "wrong fit", "different field"]
    has_non_fit = any(
        kw in " ".join(objections or []).lower() for kw in non_fit_keywords
    )

    if s_lead < -0.20 or has_non_fit:
        if s_lead < -0.20:
            checks.append(f"S_lead {s_lead:.2f} < −0.20 ✗ → Disqualified")
        if has_non_fit:
            checks.append("Explicit non-fit objection detected → Disqualified")
    else:
        checks.append(f"S_lead {s_lead:.2f} ≥ −0.20 ✓ (not disqualified)")

    if delta_s is not None and delta_s < -0.35:
        checks.append(f"delta_S {delta_s:.2f} < −0.35 ✗ → At-Risk")

    if friction > 0.70 and trajectory in ("Degrading", "Volatile"):
        checks.append(f"Rising friction ({friction:.2f}) + {trajectory} trajectory → At-Risk")

    hot_checks = []
    hot_checks.append(f"S_lead {s_lead:.2f} ≥ 0.70 {'✓' if s_lead >= 0.70 else '✗'}")
    hot_checks.append(f"P_convert {p:.2f} ≥ 0.55 {'✓' if p >= 0.55 else '✗'}")
    hot_checks.append(f"delta_S {d:.2f} > 0 {'✓' if d > 0 else '✗'}")
    hot_checks.append(f"Trajectory={trajectory} {'✓' if trajectory in ('Upward', 'Stable') else '✗'}")
    hot_checks.append(f"Friction {friction:.2f} < 0.40 {'✓' if friction < 0.40 else '✗'}")

    hot_criteria_met = (
        s_lead >= 0.70 and p >= 0.55 and d > 0
        and trajectory in ("Upward", "Stable") and friction < 0.40
    )
    if hot_criteria_met:
        checks.append("All Hot criteria met ✓ → Hot")
    else:
        checks.append("Hot criteria: " + "; ".join(hot_checks))

    if not hot_criteria_met:
        if s_lead >= 0.35:
            checks.append(f"S_lead {s_lead:.2f} ≥ 0.35 ✓ → Warm")
        else:
            checks.append(f"S_lead {s_lead:.2f} < 0.35 → Nurture (fallthrough)")

    rationale = " | ".join(checks)

    return {
        "category": category,
        "rationale": rationale,
        "grounded": True,
        "scoring_components": {
            "s_latest": s_lead if delta_s is None else round(s_lead - 0.30 * (delta_s or 0), 4),  # approximate
            "s_lead": s_lead,
            "delta_s": delta_s,
            "i_intent": 0.5 * bant_completeness + 0.5 * buying_intent_score,
            "friction": friction,
            "trajectory_flag": None,
            "bant_completeness": bant_completeness,
            "buying_intent_score": buying_intent_score,
            "primary_emotion": primary_emotion,
        },
        "model_version": "rules-v1",
        "suggested_action": CATEGORY_ACTIONS.get(category, ""),
    }

