"""Client-demo track — the agent remembers the call, and greets once.

Four defects measured on live calls (logs/voice_assistant.log, 102 calls,
3,781 traced turns), each with the code that caused it:

1. The greeting is spoken by `_run_greeting` in app/main.py, OUTSIDE
   VoiceCallSession, and wrote nothing into `_conversation_history`. So the
   model's context began at the caller's first utterance with no record it had
   already introduced itself, and 39 live answers reopened with "Hello, I'm
   Alex from Meridian University Admissions".
2. Nothing deduplicated the greeting against a duplicated `start` event or a
   WebSocket reconnect, both of which re-enter the branch that spawns it.
3. `_build_llm_prompt` sent `_conversation_history[-6:]` — three exchanges — so
   a fact given on turn 2 had left the prompt by turn 8, and the agent asked
   for it again. 21 live answers parroted "You mentioned…" back at the caller.
4. 46 live answers leaked the model's own scaffolding ("Here's a possible
   response:", "Here's a response as a university admissions advisor:") into
   TTS, because `_META_MARKERS` contained neither phrase and `scrub_meta_leak`
   therefore returned the text untouched at its fast path.

These are behavioural contracts, not implementation details: they assert on the
prompt the model actually receives and on the transforms' outputs, not on the
presence of a particular helper.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import voice_handler as vh  # noqa: E402

GREETING = (
    "Hi, I'm the admissions assistant. Ask me anything about Meridian "
    "University programs, tuition fees, or how to apply."
)


# ── AC-1: the greeting is delivered once per call ────────────────────

def test_greeting_is_recorded_as_something_the_agent_said():
    """The model must be able to SEE that it greeted."""
    s = vh.VoiceCallSession()
    assert s.intro_done is False

    s.note_greeting_spoken(GREETING)

    assert s.intro_done is True
    assert s._conversation_history[0] == f"Assistant: {GREETING}"


def test_greeting_is_recorded_exactly_once():
    """A duplicated `start`, or the reconnect path, must not double it."""
    s = vh.VoiceCallSession()
    s.note_greeting_spoken(GREETING)
    s.note_greeting_spoken(GREETING)

    spoken = [h for h in s._conversation_history if h.startswith("Assistant:")]
    assert len(spoken) == 1


def test_prompt_forbids_re_greeting_once_the_greeting_has_happened():
    s = vh.VoiceCallSession()
    assert "Never greet" not in s._build_llm_prompt("hello?")

    s.note_greeting_spoken(GREETING)
    prompt = s._build_llm_prompt("what programs do you offer?")

    assert "Never greet" in prompt
    assert "already been delivered" in prompt


def test_intro_guard_survives_the_greeting_leaving_the_history_window():
    """The guard is a flag, not a history entry, so a long call keeps it.

    This is the difference between fixing the symptom for the first six
    exchanges and fixing it for the call: once past HISTORY_WINDOW_LINES the
    greeting text itself is no longer sent, and only `intro_done` is left.
    """
    s = vh.VoiceCallSession()
    s.note_greeting_spoken(GREETING)
    for i in range(vh.HISTORY_WINDOW_LINES):
        s._conversation_history.append(f"Caller: turn {i}")

    prompt = s._build_llm_prompt("and the fees?")
    assert "Never greet" in prompt
    assert GREETING not in prompt          # the transcript entry has aged out


def test_the_greeting_is_not_re_spoken_on_a_reconnect():
    """`_claim_greeting` is keyed by CallSid so a reconnect keeps the guard."""
    from app.main import _GREETED_CALLS, _claim_greeting

    _GREETED_CALLS.clear()
    assert _claim_greeting("CA123") is True      # the greeting goes out
    assert _claim_greeting("CA123") is False     # same call, second start
    assert _claim_greeting("CA999") is True      # a redial is a new call

    _GREETED_CALLS.clear()


def test_calls_without_an_identity_are_never_silenced():
    """No CallSid means no way to dedupe; greeting twice beats not at all."""
    from app.main import _GREETED_CALLS, _claim_greeting

    _GREETED_CALLS.clear()
    assert _claim_greeting("") is True
    assert _claim_greeting("") is True

    _GREETED_CALLS.clear()


# ── AC-2: facts are remembered, including corrections ────────────────

@pytest.mark.parametrize("utterance,slot,value", [
    ("Hi, my name is Priya", "name", "Priya"),
    ("my email is priya@example.com", "email", "priya@example.com"),
    ("you can reach me on 9876543210", "phone", "9876543210"),
    ("for the Fall 2026 intake", "intake", "Fall"),
])
def test_a_fact_given_once_is_remembered(utterance, slot, value):
    s = vh.VoiceCallSession()
    assert s.note_facts(utterance) == {slot: value}
    assert s._facts[slot] == value


def test_a_correction_overwrites_the_earlier_value():
    """AC: "no, it's Sam not Sean" replaces the name rather than adding one."""
    s = vh.VoiceCallSession()
    s.note_facts("my name is Sean")

    changed = s.note_facts("no, it's Sam not Sean")

    assert changed == {"name": "Sam"}
    assert s._facts["name"] == "Sam"
    assert "Sean" not in s.known_facts_block()


def test_a_qualification_already_held_is_not_a_program_of_interest():
    """The caller's OWN degree must not be recorded as a programme they want.

    Heard on the 2026-10-10 call: "I completed my bachelor degree" was stored as
    `program: bachelor` and injected under "do not ask for these again", telling
    the model the caller was interested in a bachelor's programme they had
    already finished. Bare "bachelor"/"master" were the cause.
    """
    s = vh.VoiceCallSession()
    assert s.note_facts("I completed my bachelor degree") == {}
    assert s.note_facts("I completed my graduation in math and physics") == {}
    assert s.known_facts_block() == ""


def test_a_named_program_is_still_captured():
    """Removing the bare degree words must not lose the real cases."""
    s = vh.VoiceCallSession()
    assert s.note_facts("I want to do a bachelor in Computer Science") == {
        "program": ["Computer Science"]}
    # A second mention accumulates, so assert on the stored value rather than on
    # the delta -- the delta of a later mention is the whole list.
    s.note_facts("tell me about the B.Tech program")
    assert s._facts["program"] == ["Computer Science", "B.Tech"]


def test_two_programs_are_both_kept():
    """A caller interested in two programs is interested in two programs."""
    s = vh.VoiceCallSession()
    s.note_facts("I want to do the MBA")
    s.note_facts("also interested in B.Tech")

    assert s._facts["program"] == ["MBA", "B.Tech"]


def test_restating_a_fact_is_not_a_change():
    s = vh.VoiceCallSession()
    s.note_facts("my name is Priya")
    assert s.note_facts("yes, my name is Priya") == {}


def test_an_utterance_with_no_facts_learns_nothing():
    s = vh.VoiceCallSession()
    assert s.note_facts("yes please") == {}
    assert s.note_facts("um") == {}
    assert s.note_facts("") == {}


def test_facts_reach_the_prompt_and_carry_the_do_not_ask_rule():
    s = vh.VoiceCallSession()
    s.note_facts("my name is Priya")
    s.note_facts("I want to do the MBA")

    prompt = s._build_llm_prompt("what are the fees?")

    assert "Do not ask for these again" in prompt
    assert "- name: Priya" in prompt
    assert "- program: MBA" in prompt


def test_prompt_carries_no_facts_block_when_nothing_is_known():
    s = vh.VoiceCallSession()
    assert "Known facts" not in s._build_llm_prompt("hello?")


# ── AC-3: the history window no longer drops recent context ──────────

def test_history_window_covers_more_than_three_exchanges():
    """The old `[-6:]` sent three exchanges; a fact aged out by turn 8."""
    s = vh.VoiceCallSession()
    for i in range(5):
        s._conversation_history.append(f"Caller: question {i}")
        s._conversation_history.append(f"Assistant: answer {i}")

    prompt = s._build_llm_prompt("and finally?")

    assert "question 2" in prompt, "an exchange from 3 turns ago must survive"
    assert "answer 2" in prompt


# ── AC-4: scaffolding never reaches TTS ──────────────────────────────

@pytest.mark.parametrize("leak,kept", [
    ('Here\'s a possible response: "Meridian University offers several '
     'programs, including our flagship Business program."',
     "Meridian University offers several programs"),
    ("Here's a possible response as a university admissions advisor: "
     "The B.Tech in Computer Science is a four year program.",
     "B.Tech in Computer Science is a four year program"),
    ("Here's a response as a university admissions advisor: Tuition for the "
     "MBA is $18,500 per year.",
     "Tuition for the MBA is $18,500 per year"),
    # Heard on the live 2026-10-10 call. It reached TTS because the scrubber's
    # fast path consulted only _META_MARKERS, which did not contain this
    # phrase, while _PREFIX_STRIP matched it exactly -- so the text was
    # returned untouched before the strip could run. The fast path now checks
    # both lists, which is what stops this class of leak recurring rather than
    # just this instance.
    ("Here's a response to the student's question: Meridian University offers a "
     "new B.Tech in Computer Science that matches your maths background.",
     "Meridian University offers a new B.Tech in Computer Science"),
])
def test_scaffolded_reply_keeps_the_content_and_drops_the_scaffold(leak, kept):
    """The real answer follows the colon; losing it with the scaffold is a bug.

    These are verbatim shapes from logs/voice_assistant.log — 46 occurrences.
    """
    out = vh.scrub_meta_leak(leak)

    assert out is not None
    assert kept in out
    for scaffold in ("possible response", "as a university admissions advisor"):
        assert scaffold not in out
    assert '"' not in out, f"an orphan quote survived: {out!r}"


def test_a_clean_reply_is_still_untouched():
    clean = "The MBA is a 2-year program with tuition of $18,500 per year."
    assert vh.scrub_meta_leak(clean) == clean


# ── AC-5: replies are short, and the style rule says so ──────────────

def test_style_rules_bound_the_reply_and_forbid_parroting():
    s = vh.VoiceCallSession()
    s._conversation_history.append("Caller: I completed a degree in computer science")

    prompt = s._build_llm_prompt("what are the fees?")

    assert "25 words" in prompt
    assert "at most two short sentences" in prompt
    assert "Do not repeat back what the caller just told you" in prompt
    assert "One question at a time" in prompt
