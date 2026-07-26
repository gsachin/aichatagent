# 📱 WhatsApp Integration Guide — Twilio Sandbox

**Date:** 2026-07-26
**Status:** Code implemented — ready for manual testing

---

## Architecture

```
Your WhatsApp → Twilio Sandbox → POST webhook → FastAPI (localhost:8000)
                                                     ↓
                                              RAG pipeline
                                              (ChromaDB + Ollama)
                                                     ↓
Your WhatsApp ← Twilio ← TwiML XML response ← FastAPI
```

The tunnel (cloudflared) exposes `localhost:8000` to the internet so Twilio can reach your webhook.

---

## Prerequisites

- [x] Twilio Account SID + Auth Token (in `.env`)
- [x] WhatsApp endpoint code (in `app/main.py`)
- [x] RAG pipeline working (tested via Streamlit)

---

## Step 1: Join Twilio WhatsApp Sandbox

1. Go to **Twilio Console** → **Messaging** → **Try it out** → **Send a WhatsApp message**
2. You'll see a sandbox number like `+14155238886` and a join code like `join <word>`
3. On your phone, open WhatsApp and send: `join <word>` to the sandbox number
4. You'll get a reply: "You have joined the sandbox"

---

## Step 2: Start FastAPI Server

Open Terminal 1:
```
cd D:\university_project_demo
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Verify it's running:
```
curl http://localhost:8000/
# Should return JSON with status "ok" and "whatsapp_webhook" in endpoints
```

---

## Step 3: Start Cloudflare Tunnel

Open Terminal 2:
```
cloudflared tunnel --url http://localhost:8000
```

Look for this line in the output:
```
Your quick Tunnel has been created! Visit it at:
https://xxxxxxxx.trycloudflare.com
```

Copy the `https://xxxxxxxx.trycloudflare.com` URL — this is your public webhook URL.

---

## Step 4: Configure Twilio Sandbox Webhook

1. Go to **Twilio Console** → **Messaging** → **Try it out** → **Send a WhatsApp message**
2. In the **Sandbox Configuration** section, find **"When a message comes in"**
3. Set the webhook URL to:
   ```
   https://xxxxxxxx.trycloudflare.com/twilio/whatsapp
   ```
4. Set Method to **HTTP POST**
5. Click **Save**

---

## Step 5: Test It

On your phone, open WhatsApp and send a message to the sandbox number:

| Send | Expected Response |
|------|-------------------|
| `Hello` | "Hello! Send me a question about UMD or FDU admissions." |
| `What are the admission requirements for UMD?` | Detailed answer about UMD admissions from the PDF |
| `Tell me about FDU tuition fees` | FDU tuition data from the PDF |
| `What programs does UMD offer?` | Program listing from the PDF |

**Response time:** ~5-15 seconds (Ollama LLM + ChromaDB retrieval)

---

## Step 6: Stop

Press `Ctrl+C` in both terminals to stop FastAPI and the tunnel.

---

## Troubleshooting

### "Sorry, I couldn't process your question"
- Check that Ollama is running: `curl http://localhost:11434/api/tags`
- Check FastAPI logs in Terminal 1 for error messages
- Verify ChromaDB exists at `D:\university_project_demo\chroma_local_db`

### Twilio returns 404 or no response
- Verify the tunnel URL is correct in Twilio Console
- Check that the tunnel terminal shows "200 OK" for POST requests
- Test the webhook directly: `curl -X POST https://xxxx.trycloudflare.com/twilio/whatsapp -d "Body=test&From=+123"`

### Long answers are cut off
- Twilio has a 1600-character limit per WhatsApp message
- If the answer is very long, it may be truncated
- Future: split long answers into multiple messages

### RAG takes too long (>15 seconds)
- Twilio's webhook timeout is 15 seconds
- If Ollama is slow, the webhook will time out and Twilio will retry
- For production: use Twilio Messaging API to send replies asynchronously instead of responding to the webhook directly
