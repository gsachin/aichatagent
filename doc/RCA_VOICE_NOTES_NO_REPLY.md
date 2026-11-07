# 🔍 RCA: Voice Notes — No Text or Audio Reply

**Date:** 2026-07-28
**Severity:** Critical — voice notes produce zero response

---

## Symptom

Voice notes on WhatsApp get no reply at all — neither the "Processing..." acknowledgment nor the text answer nor the audio. Text messages work fine.

---

## Evidence from Server Logs

The server processed the voice note successfully. The entire pipeline ran and the message was sent:

```
1. Voice note received:       INFO WhatsApp voice note from whatsapp:+917757057985
2. Webhook returned 200:      POST /twilio/whatsapp HTTP/1.1 200 OK
3. Audio downloaded:          Downloaded 14323 bytes
4. Transcription:             "Tell me about FDU Tusion Feast"
5. RAG answer:                1431 chars
6. TTS audio generated:       reply_2e4e6b8f.mp3 (152 KB)
7. Twilio API accepted:       201 Created
8. Message SID:               MM5f3ba3f736a1e4cdbc0f610958e9b944
```

Everything on our side ran without errors. Twilio accepted the message with HTTP 201.

---

## Root Cause: Audio URL Points to Dead Tunnel

The `_process_voice_note_async` function builds the audio URL using `TUNNEL_HOST` env var with a hardcoded fallback:

```python
tunnel_host = os.environ.get(
    "TUNNEL_HOST", "boxes-melbourne-binary-balance.trycloudflare.com"
)
audio_url = f"https://{tunnel_host}/audio/{audio_filename}"
```

**The `TUNNEL_HOST` env var was never set when starting the server**, so it fell back to the hardcoded value `boxes-melbourne-binary-balance.trycloudflare.com` — a tunnel from a previous session that is **now dead**.

The current live tunnel is: `considerations-propose-opportunities-fans.trycloudflare.com`

### What Twilio sent to WhatsApp

```
Body:  "FDU tuition is $38,600 per year..."
Media: "https://boxes-melbourne-binary-balance.trycloudflare.com/audio/reply_2e4e6b8f.mp3"
                                                                    ↑
                                                              DEAD TUNNEL
```

### Why no reply at all

WhatsApp rejects messages that contain **unreachable media URLs**. When the media URL points to a dead tunnel:
- WhatsApp tries to fetch the audio → connection fails
- WhatsApp **drops the entire message** — including the text body
- The user sees nothing

This is a WhatsApp/Twilio behavior: an invalid media attachment kills the whole message, not just the audio part.

---

## Secondary Issue: TUNNEL_HOST Not Persisted

The tunnel URL changes every time `cloudflared` restarts. The `TUNNEL_HOST` env var was set in one terminal session but lost when the server was restarted. There's no mechanism to keep it in sync.

---

## Summary

| # | Root Cause | Impact |
|---|-----------|--------|
| 1 | Audio URL uses hardcoded dead tunnel host | WhatsApp rejects entire message (text + audio) |
| 2 | `TUNNEL_HOST` env var not set at server start | Falls back to stale hardcoded default |
| 3 | No runtime tunnel detection | Every restart requires manual env var update |

---

## Fix

Replace the hardcoded default with a writable config that the user updates each time the tunnel URL changes. Or better: automatically detect the tunnel URL at server startup by reading it from a file that the tunnel startup script writes.
