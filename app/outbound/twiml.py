"""
TwiML templates for outbound calls.

When Twilio initiates an outbound call it POSTs to a voice webhook URL
that returns TwiML.  Our outbound calls use <Connect><Stream> to pipe
audio through our WebSocket endpoint for real-time AI processing.

Usage:
    from app.outbound.twiml import outbound_connect_twiml
    twiml = outbound_connect_twiml(host="myapp.trycloudflare.com")
"""


def outbound_connect_body(host: str, params: dict | None = None) -> str:
    """The `<Connect><Stream>` verb plus its post-stream fallback line.

    No `<Response>` wrapper: the language menu needs to place this after a
    `<Gather>`, and Twilio plays the fallback `<Say>` only if the stream ends.
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
        "<Connect>"
        f"{stream}"
        "</Connect>"
        "<Say voice=\"Polly.Joanna\">We seem to have lost the connection. An admissions counselor will follow up with you shortly. Thank you for your time.</Say>"
    )


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
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"{outbound_connect_body(host, params)}"
        "</Response>"
    )


def outbound_language_menu_twiml(
    host: str,
    params: dict | None = None,
    *,
    action_url: str,
    audio_url: str = "",
    prompt: str = "",
    timeout: int = 10,
) -> str:
    """Ask the callee to choose a language before the media stream starts.

    A one-digit DTMF `<Gather>` is the only verb that can collect a choice
    before `<Connect>`: once the stream owns the call, our own pipeline is the
    only thing listening, and it cannot take DTMF. On timeout Twilio falls
    through to the connect body in the call's configured language, so a caller
    who does not press anything still gets a normal conversation.

    The timeout is ten seconds and, per Twilio's documented behaviour, starts
    when the prompt audio completes (or on the caller's first keypress). The
    first-level prompts run 13-15 s, so ten seconds after they end gives the
    caller time to move the phone away from their ear and reach the keypad.
    Five seconds proved too tight on a real handset.
    """
    from xml.sax.saxutils import escape

    action = escape(action_url, {chr(34): "&quot;"})
    if audio_url:
        ask = f"<Play>{escape(audio_url)}</Play>"
    else:
        # The pre-rendered asset is the real path. If it is missing (a fresh
        # clone before the build script runs), fall back to carrier TTS in
        # English -- never Polly reading Devanagari or Gujarati script.
        ask = f'<Say voice="Polly.Joanna">{escape(prompt)}</Say>'
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        # actionOnEmptyResult=false (Twilio's default, stated explicitly):
        # while no digit arrives the Gather times out and Twilio continues
        # with the connect body below, in the call's configured language.
        f'<Gather input="dtmf" numDigits="1" timeout="{int(timeout)}" '
        f'actionOnEmptyResult="false" action="{action}" method="GET">'
        f"{ask}</Gather>"
        f"{outbound_connect_body(host, params)}"
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
