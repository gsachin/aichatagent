"""
Sentiment extraction + composite scoring engine.

Uses the local Ollama LLM to extract structured sentiment from
conversation transcripts, then applies the scoring formula from
the Voice AI Lead Scoring integration guide (v0.5.0):

    S_lead = W1 × S_latest + W2 × delta_S + W3 × I_intent − W4 × F_friction
             clamped to [-1, 1]

Where:
  S_latest    = per-call sentiment from LLM extraction  [-1, 1]
  delta_S     = S_latest − EWMA_baseline (null for first call)
  I_intent    = 0.5 × BANT_completeness + 0.5 × buying_intent_score
  F_friction  = friction score from extraction [0, 1]

Scoring weights are configurable via env vars (W1–W4).

Usage:
    from app.sentiment.scorer import score_transcript, compute_lead_score

    # Dry-run a single transcript
    result = await score_transcript("Customer: I'm ready to enroll...")

    # Compute lead score with history
    composite = await compute_lead_score(lead_id, s_call=0.65)
"""

from __future__ import annotations

import json as _json
import logging
import os
import re
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger("sentiment.scorer")

# ── Scoring weights (configurable via env) ──────────────────────────

W1 = float(os.environ.get("SENTIMENT_W1", "0.30"))   # latest call sentiment
W2 = float(os.environ.get("SENTIMENT_W2", "0.30"))   # momentum (delta)
W3 = float(os.environ.get("SENTIMENT_W3", "0.25"))   # buying intent
W4 = float(os.environ.get("SENTIMENT_W4", "0.15"))   # friction penalty


@dataclass
class ScoreResult:
    """Result of a single transcript scoring."""
    s_call: float = 0.0                # per-call sentiment [-1, 1]
    primary_emotion: str = ""
    buying_intent_score: float = 0.0   # [0, 1]
    friction_score: float = 0.0        # [0, 1]
    objections: list[str] = field(default_factory=list)
    extraction_raw: dict = field(default_factory=dict)


@dataclass
class CompositeScore:
    """Full composite lead score with trajectory."""
    s_call: float = 0.0
    s_lead: float = 0.0
    p_convert: float | None = None
    trajectory: str = "Stable"
    trajectory_flag: str | None = None
    i_intent: float = 0.0
    friction: float = 0.0
    ewma_baseline: float | None = None
    delta_s: float | None = None
    predictive_layer_active: bool = False
    scoring_components: dict = field(default_factory=dict)
    weights_used: dict = field(default_factory=dict)
    category: str = "Nurture"
    extraction: ScoreResult | None = None


# ── Sentiment extraction via LLM ────────────────────────────────────


def _get_llm_model() -> str:
    """Find the best available Ollama model for sentiment extraction."""
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = _json.loads(resp.read())
            models = [m.get("name", "") for m in data.get("models", [])]
        # Prefer qwen, fall back to any available model
        qwen_models = [m for m in models if "qwen" in m.lower()]
        if qwen_models:
            return qwen_models[0]
        if models:
            return models[0]
    except Exception:
        pass
    return "qwen2.5:7b"


SENTIMENT_EXTRACTION_PROMPT = """You are a sales-call sentiment analyzer for a university admissions team.

Analyze the following conversation transcript between a prospective student and an admissions assistant (or AI bot). Extract structured sentiment data.

Return ONLY valid JSON (no markdown, no explanation) with these exact keys:

{{
  "primary_emotion": "one of: excited, interested, neutral, skeptical, frustrated, angry, confused",
  "sentiment_score": <float between -1.0 and 1.0>,
  "buying_intent_score": <float between 0.0 and 1.0>,
  "friction_score": <float between 0.0 and 1.0>,
  "objections": ["pricing", "timeline", "competitor"],
  "bant_completeness": <float between 0.0 and 1.0>
}}

Guidelines:
- If the student explicitly says they want to enroll/apply/join, buying_intent >= 0.7
- If the student raises pricing concerns, friction >= 0.5 and add "pricing" to objections
- If the student mentions a competitor, add "competitor" to objections
- If the transcript is very short or unclear, set scores near 0.0
- Score the STUDENT's sentiment, not the assistant's

Transcript:
{transcript}

Return ONLY the JSON object:"""


async def extract_sentiment(transcript: str) -> ScoreResult:
    """
    Run the LLM to extract structured sentiment from a transcript.

    Returns a ScoreResult with all fields populated.
    On any failure, returns a neutral ScoreResult.
    """
    if not transcript or not transcript.strip():
        return ScoreResult(primary_emotion="neutral")

    # Truncate for LLM context window
    snippet = transcript[-3000:] if len(transcript) > 3000 else transcript

    prompt = SENTIMENT_EXTRACTION_PROMPT.format(transcript=snippet)
    model = _get_llm_model()

    try:
        import ollama

        response = ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            options={"num_ctx": 4096, "temperature": 0.1},
        )
        raw = response["message"]["content"].strip()
        logger.debug(f"Sentiment LLM raw: {raw[:200]}")

        # Parse JSON from response
        parsed = None

        # 1. Direct parse
        try:
            parsed = _json.loads(raw)
        except _json.JSONDecodeError:
            pass

        # 2. Extract from ```json block
        if parsed is None:
            match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', raw, re.DOTALL)
            if match:
                try:
                    parsed = _json.loads(match.group(1))
                except _json.JSONDecodeError:
                    pass

        # 3. Extract first { ... } block
        if parsed is None:
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if match:
                try:
                    parsed = _json.loads(match.group(0))
                except _json.JSONDecodeError:
                    pass

        if parsed is None:
            logger.warning(f"Could not parse JSON from sentiment LLM: {raw[:200]}")
            return ScoreResult(primary_emotion="neutral")

        # Build result with safe defaults
        return ScoreResult(
            s_call=max(-1.0, min(1.0, float(parsed.get("sentiment_score", 0.0)))),
            primary_emotion=str(parsed.get("primary_emotion", "neutral")),
            buying_intent_score=max(0.0, min(1.0, float(parsed.get("buying_intent_score", 0.0)))),
            friction_score=max(0.0, min(1.0, float(parsed.get("friction_score", 0.0)))),
            objections=parsed.get("objections", []) if isinstance(parsed.get("objections"), list) else [],
            extraction_raw=parsed,
        )

    except Exception:
        logger.exception("Sentiment extraction failed — returning neutral")
        return ScoreResult(primary_emotion="neutral")


# ── EWMA baseline ───────────────────────────────────────────────────

EWMA_LAMBDA = float(os.environ.get("SENTIMENT_EWMA_LAMBDA", "0.35"))


async def _get_ewma_baseline(lead_id: str, current_s_call: float) -> float | None:
    """
    Retrieve the EWMA baseline from prior sentiment scores.

    If the lead has no prior scores, returns None (first call).
    """
    from app.sentiment.models import get_lead_sentiment_history

    history = await get_lead_sentiment_history(lead_id, limit=5)
    if not history:
        return None

    # Compute EWMA from past s_call values
    # Start with the oldest, apply EWMA_LAMBDA toward newer
    s_calls = [h["s_call"] for h in reversed(history)]  # oldest first
    ewma = s_calls[0]
    for s in s_calls[1:]:
        ewma = EWMA_LAMBDA * s + (1 - EWMA_LAMBDA) * ewma

    return ewma


# ── Composite scoring ───────────────────────────────────────────────


async def compute_lead_score(
    lead_id: str,
    s_call: float,
    buying_intent_score: float = 0.0,
    friction_score: float = 0.0,
    bant_completeness: float = 0.0,
) -> CompositeScore:
    """
    Compute the full composite lead score using the formula:

      S_lead = W1 × S_latest + W2 × delta_S + W3 × I_intent − W4 × F_friction

    Also classifies trajectory and determines category.
    """
    # Get EWMA baseline from history
    ewma_baseline = await _get_ewma_baseline(lead_id, s_call)

    # Compute delta_S (momentum)
    if ewma_baseline is not None:
        delta_s = s_call - ewma_baseline
    else:
        delta_s = None

    # Composite buying intent
    i_intent = 0.5 * bant_completeness + 0.5 * buying_intent_score

    # Apply weights (delta_s is 0 for first call)
    d = delta_s if delta_s is not None else 0.0
    s_lead_raw = W1 * s_call + W2 * d + W3 * i_intent - W4 * friction_score
    s_lead = max(-1.0, min(1.0, s_lead_raw))

    # Trajectory classification
    trajectory, trajectory_flag = await _classify_trajectory(lead_id, s_call)

    # Predictive layer: inactive until we have enough history
    predictive_layer_active = False
    p_convert = None

    # Try LightGBM if available and enough data
    history = None
    try:
        from app.sentiment.models import get_lead_sentiment_history
        history = await get_lead_sentiment_history(lead_id, limit=10)
    except Exception:
        pass

    if history and len(history) >= int(os.environ.get("MIN_LABELED_OUTCOMES", "100")):
        # Placeholder for future LightGBM integration
        # For now, use a simple sigmoid heuristic
        try:
            p_convert = _heuristic_conversion_probability(s_lead, trajectory)
            predictive_layer_active = True
        except Exception:
            pass

    # Categorize
    from app.sentiment.categorizer import categorize

    category = categorize(s_lead=s_lead, delta_s=delta_s, p_convert=p_convert,
                          friction=friction_score, trajectory=trajectory)

    weights = {"w1": W1, "w2": W2, "w3": W3, "w4": W4}

    return CompositeScore(
        s_call=s_call,
        s_lead=s_lead,
        p_convert=p_convert,
        trajectory=trajectory,
        trajectory_flag=trajectory_flag,
        i_intent=i_intent,
        friction=friction_score,
        ewma_baseline=ewma_baseline,
        delta_s=delta_s,
        predictive_layer_active=predictive_layer_active,
        scoring_components={
            "s_latest": s_call,
            "delta_s": delta_s,
            "i_intent": i_intent,
            "friction": friction_score,
            "ewma_baseline": ewma_baseline,
            "bant_completeness": bant_completeness,
        },
        weights_used=weights,
        category=category,
    )


async def _classify_trajectory(
    lead_id: str,
    current_s_call: float,
) -> tuple[str, str | None]:
    """
    Classify the sentiment trajectory based on the last 5 calls.

    Returns (trajectory_label, flag_or_None).

    Classification order:
      1. Insufficient history → flag
      2. V_W_5 > 0.10 → Volatile
      3. Mean trend > +0.05 → Upward
      4. Mean trend < −0.05 → Degrading
      5. Otherwise → Stable
    """
    from app.sentiment.models import get_lead_sentiment_history

    history = await get_lead_sentiment_history(lead_id, limit=5)
    if not history or len(history) < 2:
        return "Stable", "insufficient_history"

    s_calls = [h["s_call"] for h in history]

    # Include current call
    all_s = [current_s_call] + s_calls
    all_s = all_s[:5]  # last 5

    if len(all_s) < 2:
        return "Stable", "insufficient_history"

    # Variance check
    mean_s = sum(all_s) / len(all_s)
    variance = sum((s - mean_s) ** 2 for s in all_s) / len(all_s)

    if variance > 0.10:
        return "Volatile", None

    # Trend (simple linear slope over the series)
    n = len(all_s)
    indices = list(range(n))
    x_mean = (n - 1) / 2
    y_mean = mean_s
    num = sum((i - x_mean) * (s - y_mean) for i, s in zip(indices, all_s))
    den = sum((i - x_mean) ** 2 for i in indices)
    slope = num / den if den > 0 else 0

    if slope > 0.05:
        return "Upward", None
    elif slope < -0.05:
        return "Degrading", None
    else:
        return "Stable", None


def _heuristic_conversion_probability(s_lead: float, trajectory: str) -> float:
    """
    Simple sigmoid-based conversion probability heuristic.

    Used as fallback until LightGBM is active (100+ labeled outcomes).
    """
    import math

    # Base probability from s_lead
    logit = 2.5 * s_lead  # maps [-1,1] roughly to [0.08, 0.92]

    # Trajectory bonus/penalty
    if trajectory == "Upward":
        logit += 0.5
    elif trajectory == "Degrading":
        logit -= 0.5
    elif trajectory == "Volatile":
        logit -= 0.2

    prob = 1.0 / (1.0 + math.exp(-logit))
    return round(max(0.01, min(0.99, prob)), 4)


# ── Convenience: score a transcript end-to-end ──────────────────────


async def score_transcript(
    transcript: str,
    lead_id: str = "",
    prosody: dict | None = None,
) -> dict:
    """
    Fully score a single transcript (dry-run or live).

    If lead_id is provided, also computes the composite lead score
    (including EWMA momentum) and persists the result.

    Returns a dict with all scoring data suitable for API responses.
    """
    # Step 1: Extract sentiment via LLM
    extraction = await extract_sentiment(transcript)

    # Step 2: Compute composite score (if lead_id provided)
    if lead_id:
        composite = await compute_lead_score(
            lead_id=lead_id,
            s_call=extraction.s_call,
            buying_intent_score=extraction.buying_intent_score,
            friction_score=extraction.friction_score,
            bant_completeness=extraction.extraction_raw.get("bant_completeness", 0.0),
        )
        # Persist
        try:
            await _persist_score(lead_id, extraction, composite, transcript)
        except Exception:
            logger.exception("Failed to persist sentiment score (non-fatal)")
    else:
        # Dry-run: compute scores without history
        i_intent = 0.5 * extraction.buying_intent_score  # no BANT for dry-run
        s_lead_raw = W1 * extraction.s_call + W3 * i_intent - W4 * extraction.friction_score
        s_lead = max(-1.0, min(1.0, s_lead_raw))

        from app.sentiment.categorizer import categorize

        category = categorize(
            s_lead=s_lead, delta_s=None, p_convert=None,
            friction=extraction.friction_score, trajectory="Stable",
        )

        composite = CompositeScore(
            s_call=extraction.s_call,
            s_lead=s_lead,
            p_convert=_heuristic_conversion_probability(s_lead, "Stable"),
            trajectory="Stable",
            trajectory_flag="insufficient_history",
            i_intent=i_intent,
            friction=extraction.friction_score,
            category=category,
            extraction=extraction,
            weights_used={"w1": W1, "w2": W2, "w3": W3, "w4": W4},
        )

    return _build_response(extraction, composite)


async def _persist_score(
    lead_id: str,
    extraction: ScoreResult,
    composite: CompositeScore,
    transcript: str,
) -> None:
    """Save a sentiment score and update the lead record with session-level aggregate."""
    from app.sentiment.models import save_sentiment_score, update_lead_sentiment_fields

    # Save the per-exchange score for history/audit
    await save_sentiment_score(
        lead_id=lead_id,
        s_call=extraction.s_call,
        primary_emotion=extraction.primary_emotion,
        buying_intent_score=extraction.buying_intent_score,
        friction_score=extraction.friction_score,
        objections=extraction.objections,
        s_lead=composite.s_lead,
        p_convert=composite.p_convert,
        trajectory=composite.trajectory,
        category=composite.category,
        extraction_raw=extraction.extraction_raw,
        weights_used=composite.weights_used,
        transcript_snippet=transcript[:500],
    )

    # ── Compute session-level aggregate from ALL history ──────────
    # The per-exchange score (above) captures individual messages.
    # The lead's overall sentiment should reflect the ENTIRE session,
    # not just the last message.  We recompute from all history.
    session_aggregate = await _compute_session_aggregate(lead_id)

    await update_lead_sentiment_fields(
        lead_id=lead_id,
        current_category=session_aggregate["category"],
        overall_sentiment_score=session_aggregate["s_lead"],
        sentiment_trajectory=session_aggregate["trajectory"],
        conversion_probability=session_aggregate["p_convert"],
    )


async def _compute_session_aggregate(lead_id: str) -> dict:
    """
    Compute the lead's OVERALL sentiment from all accumulated exchanges.

    Uses EWMA of all s_call values for the baseline, then applies the
    composite formula to produce a session-level S_lead that reflects
    the entire conversation, not just the last message.
    """
    from app.sentiment.models import get_lead_sentiment_history
    from app.sentiment.categorizer import categorize

    history = await get_lead_sentiment_history(lead_id, limit=20)
    if not history:
        return {"s_lead": 0.0, "category": "Nurture", "trajectory": "Stable",
                "p_convert": None}

    # Extract all s_call values (oldest first for EWMA)
    s_calls = [h["s_call"] for h in reversed(history)]

    # Compute EWMA across all exchanges (session baseline)
    ewma = s_calls[0]
    for s in s_calls[1:]:
        ewma = EWMA_LAMBDA * s + (1 - EWMA_LAMBDA) * ewma

    # Latest (most recent) s_call
    s_latest = s_calls[-1]

    # Average buying intent and friction across session
    buying_ints = [h["buying_intent_score"] for h in history if h.get("buying_intent_score")]
    frictions = [h["friction_score"] for h in history if h.get("friction_score")]
    avg_intent = sum(buying_ints) / len(buying_ints) if buying_ints else 0.0
    avg_friction = sum(frictions) / len(frictions) if frictions else 0.0

    # Average BANT completeness from extraction_raw
    bant_vals = []
    for h in history:
        raw = h.get("extraction_raw", {})
        if isinstance(raw, dict) and "bant_completeness" in raw:
            bant_vals.append(raw["bant_completeness"])
    avg_bant = sum(bant_vals) / len(bant_vals) if bant_vals else 0.0

    # Delta from EWMA baseline
    delta_s = s_latest - ewma

    # Composite intent
    i_intent = 0.5 * avg_bant + 0.5 * avg_intent

    # Session S_lead
    s_lead_raw = W1 * s_latest + W2 * delta_s + W3 * i_intent - W4 * avg_friction
    s_lead = max(-1.0, min(1.0, s_lead_raw))

    # Trajectory from all s_call values
    trajectory, _ = await _classify_trajectory(lead_id, s_latest)
    # Override: if we have 2+ scores and variance is low, trajectory is more reliable
    if len(s_calls) >= 2:
        mean_s = sum(s_calls) / len(s_calls)
        var = sum((s - mean_s) ** 2 for s in s_calls) / len(s_calls)
        if var <= 0.10:
            # Low variance — check trend
            slope = _simple_slope(s_calls)
            if slope > 0.05:
                trajectory = "Upward"
            elif slope < -0.05:
                trajectory = "Degrading"
            else:
                trajectory = "Stable"

    # Most frequent emotion
    from collections import Counter
    emotions = [h.get("primary_emotion", "") for h in history if h.get("primary_emotion")]
    dominant_emotion = Counter(emotions).most_common(1)[0][0] if emotions else "neutral"

    # Conversion probability
    p_convert = _heuristic_conversion_probability(s_lead, trajectory)

    # Collect all objections across session
    all_objections = []
    for h in history:
        objs = h.get("objections", [])
        if isinstance(objs, list):
            all_objections.extend(objs)

    # Categorize with session-level values
    category = categorize(
        s_lead=s_lead, delta_s=delta_s, p_convert=p_convert,
        friction=avg_friction, trajectory=trajectory,
        objections=list(set(all_objections)),
    )

    logger.info(
        f"Session aggregate for {lead_id}: S_lead={s_lead:.2f}, "
        f"category={category}, trajectory={trajectory}, "
        f"from {len(history)} exchanges, dominant_emotion={dominant_emotion}"
    )

    return {
        "s_lead": s_lead,
        "category": category,
        "trajectory": trajectory,
        "p_convert": p_convert,
        "exchanges": len(history),
        "dominant_emotion": dominant_emotion,
    }


def _simple_slope(values: list[float]) -> float:
    """Compute simple linear slope over a series."""
    n = len(values)
    if n < 2:
        return 0.0
    indices = list(range(n))
    x_mean = (n - 1) / 2
    y_mean = sum(values) / n
    num = sum((i - x_mean) * (v - y_mean) for i, v in zip(indices, values))
    den = sum((i - x_mean) ** 2 for i in indices)
    return num / den if den > 0 else 0.0


def _build_response(extraction: ScoreResult, composite: CompositeScore) -> dict:
    """Build the standard API response dict."""
    return {
        "s_lead": composite.s_lead,
        "p_convert": composite.p_convert,
        "trajectory": composite.trajectory,
        "trajectory_flag": composite.trajectory_flag,
        "i_intent": composite.i_intent,
        "friction": composite.friction,
        "category": composite.category,
        "extraction": {
            "primary_emotion": extraction.primary_emotion,
            "buying_intent_score": extraction.buying_intent_score,
            "friction_score": extraction.friction_score,
            "objections": extraction.objections,
        },
        "scoring_components": composite.scoring_components,
        "weights_used": composite.weights_used,
        "predictive_layer_active": composite.predictive_layer_active,
        "note": (
            "Dry-run only — no data persisted, no actions executed."
            if composite.scoring_components.get("ewma_baseline") is None and composite.trajectory_flag == "insufficient_history"
            else None
        ),
    }
