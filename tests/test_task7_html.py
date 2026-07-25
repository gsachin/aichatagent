"""
Task 7 — Create app/static/voice_client.html (Browser Mic Page)
================================================================
Goal: Single self-contained HTML page for browser mic capture + WebSocket streaming.
"""

import re
from pathlib import Path

import pytest


HTML_PATH = Path(__file__).resolve().parent.parent / "app" / "static" / "voice_client.html"


class TestTask7HtmlFile:
    """Verify voice_client.html exists and is well-formed."""

    def test_html_file_exists(self):
        """File must exist at app/static/voice_client.html."""
        assert HTML_PATH.is_file(), f"voice_client.html not found at {HTML_PATH}"

    def test_html_file_not_empty(self):
        """File must be > 500 bytes (not a stub)."""
        size = HTML_PATH.stat().st_size
        assert size > 500, f"voice_client.html is only {size} bytes — should be > 500"

    def test_has_html_doctype(self):
        """File must begin with <!DOCTYPE html> or <html>."""
        content = HTML_PATH.read_text(encoding="utf-8")
        assert content.lstrip().startswith("<!DOCTYPE html>") or "<html" in content[:500], (
            "File does not appear to be an HTML document"
        )

    def test_has_get_user_media(self):
        """Must reference navigator.mediaDevices.getUserMedia for mic capture."""
        content = HTML_PATH.read_text(encoding="utf-8")
        assert "getUserMedia" in content, (
            "Missing getUserMedia — cannot capture microphone"
        )

    def test_has_websocket_client(self):
        """Must contain new WebSocket() to connect to /ws/voice."""
        content = HTML_PATH.read_text(encoding="utf-8")
        assert "WebSocket" in content, (
            "Missing WebSocket constructor — cannot connect to server"
        )

    def test_has_audio_context(self):
        """Must use AudioContext for playback (or web audio API)."""
        content = HTML_PATH.read_text(encoding="utf-8")
        assert "AudioContext" in content or "audioContext" in content, (
            "Missing AudioContext — cannot play received audio"
        )

    def test_has_start_stop_ui(self):
        """Must have a button element for user interaction (Start/Stop)."""
        content = HTML_PATH.read_text(encoding="utf-8")
        assert "<button" in content.lower(), (
            "Missing <button> element — need Start/Stop UI"
        )

    def test_no_external_cdn_dependency(self):
        """Must be self-contained — no <script src='https://...' for critical logic."""
        content = HTML_PATH.read_text(encoding="utf-8")
        # Find script tags
        scripts = re.findall(r'<script[^>]*src="([^"]*)"', content, re.IGNORECASE)
        external = [s for s in scripts if s.startswith("http")]
        # Inline scripts are fine
        assert len(external) == 0, (
            f"External script dependencies found: {external}. Page must be self-contained."
        )

    def test_no_javascript_syntax_errors(self):
        """Inline <script> blocks must parse without syntax errors (basic check)."""
        content = HTML_PATH.read_text(encoding="utf-8")
        # Extract content of all script blocks
        script_contents = re.findall(
            r'<script[^>]*>(.*?)</script>', content, re.DOTALL | re.IGNORECASE
        )
        assert len(script_contents) > 0, "No <script> blocks found"

        for i, script in enumerate(script_contents):
            script = script.strip()
            if not script:
                continue
            # Use compile() for syntax check. JS is not Python, so we can only check
            # for obvious issues like unmatched braces via heuristic
            open_braces = script.count("{")
            close_braces = script.count("}")
            assert open_braces == close_braces, (
                f"Script block {i}: unmatched braces — {open_braces} open vs {close_braces} close"
            )
            open_parens = script.count("(")
            close_parens = script.count(")")
            assert open_parens == close_parens, (
                f"Script block {i}: unmatched parentheses — {open_parens} open vs {close_parens} close"
            )
