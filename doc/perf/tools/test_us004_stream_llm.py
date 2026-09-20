"""US-004 acceptance tests — streamed LLM generation. No network, no model.

Covers the story's build-time contract (PO build-approved 2026-09-19, Phase 4a):
  AC-1   deltas are consumed as they are produced; the caller does not wait
         for the completion
  AC-2   clauses are cut on sentence boundaries, never a half-sentence
  AC-3   an empty/errored stream raises GenerationFailed -> MOD-01's fallback
  AC-4   cancellation mid-stream abandons the generation
  TAC-3  `llm_first_token` is a declared stage WITH an emitter (streamed turns)
  TAC-5  /api/tags is resolved once per process, not per utterance (TRD-10)
  AC-4   LLM_STREAM defaults OFF; the batch chat() is retained unchanged

The ollama client is stubbed (a fake sync stream iterator), so this runs in
milliseconds and never touches the engine.
Run:  .venv/Scripts/python.exe doc/perf/tools/test_us004_stream_llm.py
"""
from __future__ import annotations

import asyncio
import inspect
import os
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJ))
os.chdir(PROJ)

from dotenv import load_dotenv
load_dotenv(PROJ / ".env")

import app.llm_backend as lb                    # noqa: E402
from app.llm_backend import (Clause, ClauseCutter, EngineCounters,  # noqa: E402
                             GenerationCancelled, GenerationFailed)

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}{(' - ' + detail) if detail else ''}")


print("\nUS-004 streamed LLM generation (LLM_STREAM flag)\n")

# ── AC-4 / the flag defaults OFF and the batch path is untouched ───────────
check("AC-4   LLM_STREAM defaults OFF (floors ship disabled; DG-03 adoption gate)",
      lb.LLM_STREAM is False)
src = inspect.getsource(lb._chat_ollama)
check("AC-4   the batch chat() still calls the non-streamed client",
      "stream=True" not in src and "ollama.chat(" in src)

# ── AC-2 / the clause cutter ───────────────────────────────────────────────
c = ClauseCutter()
out = c.feed("Tuition is $17,000 per year. Scholarships ")
check("AC-2   a complete sentence boundary emits exactly one clause",
      len(out) == 1 and out[0].text == "Tuition is $17,000 per year."
      and out[0].is_final is False, str(out))
check("AC-2   a partial trailing clause is never emitted early",
      c._buf == "Scholarships ")
out = c.feed("follow. Apply")
check("AC-2   later deltas complete the next clause",
      len(out) == 1 and out[0].text == "Scholarships follow.", str(out))
check("AC-2   flush() drops a half-sentence under the minimum",
      c.flush() is None, repr(c.flush()))
c.feed(" by March.")
merged = c.flush()
check("AC-2   a short fragment re-merges into the following clause",
      merged is not None and "by March." in merged.text and merged.is_final,
      str(merged))
c2 = ClauseCutter()
c2.feed("It costs $1,700. And more follows.")
whole = c2.flush()
check("AC-2   a price is never cut (digit before the boundary)",
      whole is not None and "$1,700. And more follows." in whole.text,
      str(whole))

# REGRESSION (2026-09-20, run T043413Z): a short first clause whose boundary
# was re-created on the merge made feed() re-match the SAME boundary forever,
# freezing the event loop (py-spy: MainThread inside feed()). This is the
# exact shape that fired it.
c3 = ClauseCutter()
out = c3.feed("Sure. ")
out += c3.feed("The fee is $14,500 per year.")
final = c3.flush()
check("AC-2   a short leading clause merges AND the cutter terminates",
      len(out) == 0 and final is not None
      and final.text == "Sure.The fee is $14,500 per year.",
      f"out={out} final={final}")
# The merge keeps the period (the spoken pause); the dropped whitespace is
# what stops the boundary from re-matching -- and the sentence splitter uses
# the same boundary rule, so the merged clause synthesises as one chunk.
check("AC-2   the merged clause is one sentence for the TTS splitter",
      __import__("app.voice_handler", fromlist=["_split_sentences"])
      ._split_sentences(final.text) == [final.text])

# ── AC-1 / generate_stream: deltas then exactly one EngineCounters ─────────
FAKE_CHUNKS = [
    {"message": {"content": "Tuition is "}},
    {"message": {"content": "here. "}, "prompt_eval_count": 100},
    {"message": {"content": "Scholarships too."}},
    {"message": {"content": ""}, "done": True,
     "prompt_eval_count": 100, "eval_count": 12,
     "prompt_eval_duration": 1_000_000_000, "eval_duration": 2_000_000_000},
]


def fake_chat(**kwargs):
    assert kwargs.get("stream") is True, "streamed path must pass stream=True"
    for chunk in FAKE_CHUNKS:
        yield chunk


import ollama as _ollama_mod  # noqa: E402
_ollama_mod.chat = fake_chat       # generate_stream imports the module, so the
                                   # module attribute is what must be stubbed


async def collect():
    items = []
    async for item in lb.generate_stream("prompt", model="llama3.2:3b"):
        items.append(item)
    return items


items = asyncio.run(collect())
check("AC-1   every delta is yielded in order",
      [i for i in items if isinstance(i, str)] ==
      ["Tuition is ", "here. ", "Scholarships too."])
check("AC-1   exactly one EngineCounters closes the stream, with the engine's numbers",
      len([i for i in items if isinstance(i, EngineCounters)]) == 1
      and items[-1].prompt_eval_count == 100 and items[-1].eval_count == 12)
check("AC-1   the counters are never fabricated zeros",
      items[-1].eval_duration_ns == 2_000_000_000)


async def collect_cancelled():
    ev = asyncio.Event()
    ev.set()
    out = []
    async for item in lb.generate_stream("x", model="m", cancel=ev):
        out.append(item)
    return out


try:
    asyncio.run(collect_cancelled())
    check("AC-4   a set cancel event abandons the stream", False)
except GenerationCancelled:
    check("AC-4   a set cancel event abandons the stream", True)

EMPTY_CHUNKS = [{"message": {"content": ""}, "done": True}]
_ollama_mod.chat = lambda **kw: iter(EMPTY_CHUNKS)


async def collect_empty():
    out = []
    async for item in lb.generate_stream("x", model="m"):
        out.append(item)
    return out


try:
    asyncio.run(collect_empty())
    check("AC-3   an empty stream raises GenerationFailed, never silence", False)
except GenerationFailed:
    check("AC-3   an empty stream raises GenerationFailed, never silence", True)

# ── TAC-5 / model discovery is resolved once per process ───────────────────
lb._tags_cache = None
calls = {"n": 0}


def fake_urlopen(req, timeout=None):
    if "/api/tags" in str(getattr(req, "full_url", "")):
        calls["n"] += 1

        class _R:
            def read(self):
                return b'{"models": [{"name": "llama3.2:3b"}]}'

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return _R()
    raise OSError("no")


_real_urlopen = lb.urllib.request.urlopen
lb.urllib.request.urlopen = fake_urlopen
try:
    m1 = lb.pick_model(["llama3.2:3b"])
    m2 = lb.pick_model(["llama3.2:3b"])
finally:
    lb.urllib.request.urlopen = _real_urlopen
check("TAC-5   pick_model resolves the model (env-first)",
      m1 == m2 == lb.OLLAMA_MODEL, f"{m1!r} != {lb.OLLAMA_MODEL!r}")
check("TAC-5   /api/tags is queried ONCE for two resolutions (TRD-10)",
      calls["n"] == 1, f"{calls['n']} calls")

# ── TAC-3 / the trace stage exists, with an emitter ────────────────────────
import app.perf_trace as pt  # noqa: E402

check("TAC-3   llm_first_token is a declared stage between retrieval_done and llm_done",
      "llm_first_token" in pt.STAGES
      and pt.STAGES.index("retrieval_done") < pt.STAGES.index("llm_first_token")
      < pt.STAGES.index("llm_done"))
vh_src = (PROJ / "app" / "voice_handler.py").read_text(encoding="utf-8")
check("TAC-3   the emitter marks llm_first_token at the first delta",
      'mark("llm_first_token")' in vh_src)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
