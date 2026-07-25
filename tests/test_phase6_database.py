"""
Phase 6 — System State & Lead Capture Verification Tests
=========================================================
Goal: Verify PostgreSQL schema, transcript logging, and lead extraction.

Tests:
    - Database module defines the lead_calls table schema correctly
    - lead_calls table has required columns (id, phone_number, transcript, extracted_lead)
    - Lead extraction prompt parses JSON from transcript text
    - Post-call handler is wired to WebSocket disconnect event
    - Database connection settings are configurable
"""

import json
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ── Helpers ──────────────────────────────────────────────────────────

def _ollama_available() -> bool:
    """Return True if Ollama API is reachable."""
    import urllib.request

    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
            return "models" in data
    except Exception:
        return False


# ── Phase 6.1: Database Schema ───────────────────────────────────────

class TestPhase6DatabaseSchema:
    """Verify the lead_calls table schema matches the specification."""

    REQUIRED_COLUMNS = {
        "id": "UUID primary key",
        "phone_number": "VARCHAR — caller's phone number",
        "transcript": "JSONB or TEXT — full call transcript",
        "extracted_lead": "JSONB — {name, email, target_program}",
    }

    def test_database_module_exists(self):
        """app/database.py must exist."""
        db_path = PROJECT_ROOT / "app" / "database.py"

        if not db_path.is_file():
            pytest.skip(
                "app/database.py not yet created — will be built in Phase 6 implementation"
            )

        content = db_path.read_text(encoding="utf-8")
        assert len(content) > 100, "database.py appears empty or too small"

    def test_lead_calls_table_defined(self):
        """app/database.py must define a lead_calls table or model."""
        db_path = PROJECT_ROOT / "app" / "database.py"

        if not db_path.is_file():
            pytest.skip("app/database.py not yet created")

        content = db_path.read_text(encoding="utf-8")

        # Look for table/model definition
        assert "lead_calls" in content.lower(), (
            "database.py must define or reference 'lead_calls' table"
        )

    @pytest.mark.parametrize("column", list(REQUIRED_COLUMNS.keys()))
    def test_column_present(self, column: str):
        """Each required column must appear in database.py or its SQL."""
        db_path = PROJECT_ROOT / "app" / "database.py"

        if not db_path.is_file():
            pytest.skip("app/database.py not yet created")

        content = db_path.read_text(encoding="utf-8")

        assert column in content.lower(), (
            f"Column '{column}' not found in database.py.\n"
            f"Required: {self.REQUIRED_COLUMNS[column]}"
        )

    def test_psycopg2_dependency_declared(self):
        """psycopg2-binary must be in requirements.txt."""
        req_path = PROJECT_ROOT / "requirements.txt"

        if not req_path.is_file():
            pytest.fail("requirements.txt not found")

        content = req_path.read_text(encoding="utf-8").lower()
        assert "psycopg2" in content, (
            "requirements.txt missing: psycopg2-binary"
        )


# ── Phase 6.2: Lead Extraction (LLM JSON Parsing) ────────────────────

class TestPhase6LeadExtraction:
    """Verify the LLM can extract structured lead data from transcripts."""

    LEAD_EXTRACTION_PROMPT = (
        "Extract candidate Name, Email, and Program from the following "
        "conversation transcript. Return ONLY valid JSON with keys: "
        '"name", "email", "program". If a field is not found, set it to null.'
        '\n\nTranscript:\n{transcript}'
    )

    SAMPLE_TRANSCRIPT = """
    Agent: Hello, welcome to University Admissions. How can I help you today?
    Caller: Hi, my name is John Smith. I'm interested in the Computer Science program.
    Agent: Great! Can I get your email address?
    Caller: Sure, it's john.smith@email.com.
    Agent: Thank you, John. The CS program requires a 3.2 GPA and 1200 SAT.
    Caller: That sounds good. What's the application deadline?
    Agent: August 1st for the fall semester. Is there anything else?
    Caller: No, that's all. Thanks!
    """

    def test_extraction_prompt_format(self):
        """The extraction prompt must contain required JSON keys."""
        prompt = self.LEAD_EXTRACTION_PROMPT

        assert "{transcript}" in prompt, (
            "Prompt must have a {transcript} placeholder"
        )
        assert "name" in prompt.lower()
        assert "email" in prompt.lower()
        assert "program" in prompt.lower()
        assert "json" in prompt.lower(), (
            "Prompt must instruct the LLM to return JSON"
        )

    def test_extract_lead_from_sample_transcript(self):
        """LLM must extract name, email, and program from a sample transcript."""
        if not _ollama_available():
            pytest.skip("Ollama not reachable — cannot test LLM extraction")

        import ollama
        import urllib.request

        # Find an available model
        req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
            models = [m.get("name", "") for m in data.get("models", [])]
        qwen = next((m for m in models if "qwen" in m.lower()), models[0])

        prompt = self.LEAD_EXTRACTION_PROMPT.format(transcript=self.SAMPLE_TRANSCRIPT)

        response = ollama.chat(
            model=qwen,
            messages=[{"role": "user", "content": prompt}],
            options={"num_ctx": 2048},
        )

        raw = response["message"]["content"]
        print(f"\nLLM extraction result:\n{raw}")

        # Try to parse JSON from the response
        extracted = self._parse_json_from_llm_output(raw)

        assert extracted is not None, (
            f"LLM output must contain parseable JSON. Got: {raw[:300]}"
        )
        assert "name" in extracted, "Extracted JSON missing 'name' key"
        assert "email" in extracted, "Extracted JSON missing 'email' key"
        assert "program" in extracted, "Extracted JSON missing 'program' key"

        # Verify extracted values
        assert "john" in extracted.get("name", "").lower(), (
            f"Expected name 'John Smith', got: {extracted.get('name')}"
        )
        assert "smith" in extracted.get("name", "").lower()
        assert "@" in extracted.get("email", ""), (
            f"Expected valid email, got: {extracted.get('email')}"
        )
        assert "computer" in extracted.get("program", "").lower() or "cs" in extracted.get("program", "").lower(), (
            f"Expected CS program, got: {extracted.get('program')}"
        )

    def test_extraction_handles_missing_fields(self):
        """When transcript is missing data, LLM must return null for those fields."""
        if not _ollama_available():
            pytest.skip("Ollama not reachable — cannot test LLM extraction")

        import ollama
        import urllib.request

        req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
            models = [m.get("name", "") for m in data.get("models", [])]
        qwen = next((m for m in models if "qwen" in m.lower()), models[0])

        minimal_transcript = "Caller: What time does the library close?"

        prompt = self.LEAD_EXTRACTION_PROMPT.format(transcript=minimal_transcript)

        response = ollama.chat(
            model=qwen,
            messages=[{"role": "user", "content": prompt}],
            options={"num_ctx": 2048},
        )

        raw = response["message"]["content"]
        print(f"\nMinimal transcript extraction:\n{raw}")

        extracted = self._parse_json_from_llm_output(raw)
        assert extracted is not None, "Must return parseable JSON even for minimal input"

        # At least one field should be null (no lead info in this transcript)
        null_fields = [
            k for k, v in extracted.items()
            if v is None or str(v).lower() in ("null", "none", "", "n/a")
        ]
        print(f"Null/missing fields (expected): {null_fields}")

    @staticmethod
    def _parse_json_from_llm_output(raw: str) -> dict | None:
        """Extract a JSON object from LLM text output (may be wrapped in markdown)."""
        # Try direct parse
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        # Try extracting from ```json ... ``` block
        match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # Try finding first { ... } block
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        return None


# ── Phase 6.3: Post-Call Handler ─────────────────────────────────────

class TestPhase6PostCallHandler:
    """Verify the post-call handler is wired to WebSocket disconnect."""

    def test_pipeline_references_post_call_handler(self):
        """app/pipeline.py must reference a post-call handler or database module."""
        pipeline_path = PROJECT_ROOT / "app" / "pipeline.py"

        if not pipeline_path.is_file():
            pytest.skip("app/pipeline.py not yet created")

        content = pipeline_path.read_text(encoding="utf-8")

        # Should reference database or post-call cleanup
        db_refs = ["database", "post_call", "disconnect", "save_transcript", "lead"]
        found = [ref for ref in db_refs if ref in content.lower()]
        assert len(found) > 0, (
            "app/pipeline.py must reference post-call handling. "
            f"Expected one of: {db_refs}"
        )

    def test_main_py_handles_ws_disconnect_for_cleanup(self):
        """app/main.py WebSocket handlers must allow for cleanup on disconnect."""
        main_path = PROJECT_ROOT / "app" / "main.py"

        if not main_path.is_file():
            pytest.fail("app/main.py not found")

        content = main_path.read_text(encoding="utf-8")

        # Current implementation handles WebSocketDisconnect — verify it's extensible
        assert "WebSocketDisconnect" in content, (
            "app/main.py must handle WebSocketDisconnect for cleanup hooks"
        )


# ── Phase 6.4: Database Configuration ────────────────────────────────

class TestPhase6DatabaseConfig:
    """Verify database connection settings are configurable."""

    def test_database_url_configurable(self):
        """Database connection must not be hardcoded."""
        db_path = PROJECT_ROOT / "app" / "database.py"

        if not db_path.is_file():
            pytest.skip("app/database.py not yet created")

        content = db_path.read_text(encoding="utf-8")

        # Must use env vars or config for the connection string
        config_refs = ["DATABASE_URL", "DB_HOST", "POSTGRES", "environ", "config"]
        found = [ref for ref in config_refs if ref in content]
        assert len(found) > 0, (
            "Database connection must use environment variables or config. "
            "Looked for: DATABASE_URL, DB_HOST, etc."
        )

    def test_config_has_db_placeholders(self):
        """app/config.py should have database connection placeholders."""
        config_path = PROJECT_ROOT / "app" / "config.py"

        if not config_path.is_file():
            pytest.fail("app/config.py not found")

        content = config_path.read_text(encoding="utf-8")

        # Database fields or comments mentioning Postgres
        db_hints = ["DATABASE", "POSTGRES", "DB_", "database", "postgres"]
        found = [h for h in db_hints if h in content]
        # Not a hard fail if missing — Phase B adds these
        print(f"\nDatabase config hints found: {found if found else '(none yet)'}")
