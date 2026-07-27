# 🎓 Demo Guide — Streamlit + WhatsApp

## One-Click Start

Double-click **`start_demo.bat`**

This opens 5 windows automatically:
1. **Streamlit** — Chat UI server (port 8501)
2. **FastAPI** — WhatsApp backend (port 8000)
3. **Tunnel Streamlit** — public URL for sharing
4. **Tunnel WhatsApp** — public URL for Twilio webhook
5. **Browser** — opens `http://localhost:8501`

---

## Manual Setup (one-time, 2 minutes)

### Step A: Get WhatsApp public URL

Look at the **"TUNNEL — WhatsApp"** window. You'll see:

```
https://xxxxxxxx.trycloudflare.com
```

Copy this URL.

### Step B: Configure Twilio

1. Go to **https://console.twilio.com/us1/develop/sms/try-it-out/whatsapp-learn**
2. Under **Sandbox Configuration**, find **"When a message comes in"**
3. Set to **HTTP POST**
4. Paste: `https://xxxxxxxx.trycloudflare.com/twilio/whatsapp`
5. Click **Save**

### Step C: Join WhatsApp Sandbox

1. On the same Twilio page, find the **join code** (e.g., `join middle-earth`)
2. On your phone, open WhatsApp
3. Send the join code to `+14155238886`
4. You'll get: *"You have joined the sandbox"*

### Step D: Add more testers (optional)

On the Twilio page, under **Sandbox Participants**, add their phone numbers. They will get a WhatsApp message with their own join code.

---

## Testing

| Interface | Local URL | Public URL |
|-----------|-----------|------------|
| **Streamlit** | `http://localhost:8501` | From "TUNNEL Streamlit" window |
| **WhatsApp** | — | Open WhatsApp → sandbox chat |

### Test checklist

- [ ] Type a question in Streamlit — get text + audio reply
- [ ] Click mic in Streamlit — record → get answer
- [ ] Send text on WhatsApp — get reply
- [ ] Send voice note on WhatsApp — get text reply

---

## Stop

Close all 4 terminal windows, or press `Ctrl+C` in each.

---

## ⚠️ Important

- **Tunnel URLs change every time** you restart. Update the Twilio webhook URL each time.
- **Ollama must be running** (🦙 in system tray). The script checks this.
- Streamlit first load takes ~15 seconds (building vector store).
