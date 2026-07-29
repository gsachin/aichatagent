"""
Outbound call engine — University Admissions Voice Assistant.

Provides:
- twiml.py    : TwiML templates for outbound calls (Media Streams connection).
- caller.py   : OutboundCallWorker — background task that polls call_queue
                and initiates outbound calls via the Twilio REST API.
- scheduler.py: FollowUpScheduler — polls follow_ups and enqueues due items.
"""
