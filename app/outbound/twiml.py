"""
TwiML templates for outbound calls.

When Twilio initiates an outbound call it POSTs to a voice webhook URL
that returns TwiML.  Our outbound calls use <Connect><Stream> to pipe
audio through our WebSocket endpoint for real-time AI processing.

Usage:
    from app.outbound.twiml import outbound_connect_twiml
    twiml = outbound_connect_twiml(host="myapp.trycloudflare.com")
"""


def outbound_connect_twiml(host: str, params: dict | None = None) -> str:
    """
    Return TwiML that connects an outbound call to the Media Streams
    WebSocket for AI conversation.  The AI sends its own TTS greeting
    as soon as the stream starts (handled in main.py).

    *params* become `<Parameter>` children on `<Stream>`, which is the only way
    anything from the dial reaches the media stream — the WebSocket `start`
    event carries no application data of its own. The outbound caller already
    knows who it is dialling, so this is how the lead id and number cross that
    boundary and let the call be linked to the CRM.
    """
    # NOTE: Twilio's <Stream> verb has no echoCancellation/AEC attribute
    # (verified against the TwiML reference — only url/name/track/
    # statusCallback are supported). Assistant-TTS bleed into the caller's
    # audio track is instead mitigated downstream: faster-whisper's
    # internal VAD plus the STT confidence/fragment noise gate in
    # app/voice_handler.py.
    from xml.sax.saxutils import escape

    carried = "".join(
        f'<Parameter name="{escape(str(name))}" '
        f'value="{escape(str(value), {chr(34): "&quot;"})}" />'
        for name, value in (params or {}).items()
        if value
    )
    stream = (
        f'<Stream url="wss://{host}/ws/twilio-outbound">{carried}</Stream>'
        if carried
        else f'<Stream url="wss://{host}/ws/twilio-outbound" />'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        "<Connect>"
        f"{stream}"
        "</Connect>"
        "<Say voice=\"Polly.Joanna\">We seem to have lost the connection. An admissions counselor will follow up with you shortly. Thank you for your time.</Say>"
        "</Response>"
    )


def outbound_say_twiml(message: str) -> str:
    """
    Simple TwiML that speaks a message using Twilio's built-in TTS
    (used as a fallback when Media Streams is unavailable).
    """
    escaped = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"<Say voice=\"Polly.Joanna\">{escaped}</Say>"
        "</Response>"
    )
