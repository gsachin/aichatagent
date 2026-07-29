"""
TwiML templates for outbound calls.

When Twilio initiates an outbound call it POSTs to a voice webhook URL
that returns TwiML.  Our outbound calls use <Connect><Stream> to pipe
audio through our WebSocket endpoint for real-time AI processing.

Usage:
    from app.outbound.twiml import outbound_connect_twiml
    twiml = outbound_connect_twiml(host="myapp.trycloudflare.com")
"""


def outbound_connect_twiml(host: str) -> str:
    """
    Return TwiML that connects an outbound call to the Media Streams
    WebSocket for AI conversation.  The AI sends its own TTS greeting
    as soon as the stream starts (handled in main.py).
    """
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        "<Connect>"
        f"<Stream url=\"wss://{host}/ws/twilio-outbound\" />"
        "</Connect>"
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
