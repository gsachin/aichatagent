"""
Sentiment Analysis & Lead Scoring Module.

Provides:
- Transcript sentiment extraction via LLM (Ollama)
- Composite lead scoring (S_lead formula from integration guide)
- Lead categorization (Hot / Warm / Nurture / At-Risk / Disqualified)
- Sentiment history persistence in PostgreSQL

Based on the Voice AI Lead Scoring integration guide (v0.5.0),
adapted to use the existing Ollama LLM and PostgreSQL infrastructure.

Usage:
    from app.sentiment.scorer import score_transcript, ScoreResult
    from app.sentiment.categorizer import categorize_lead, LeadCategory
    from app.sentiment.models import save_sentiment_score, get_lead_sentiment
"""
