# Live call review — `853548f5`, inbound +17208402670

**Call:** `853548f5-240d-4d6e-8996-496954408b86` (CRM `c5d9afd9`) · stream `MZ54a0e354…` · inbound
**When:** 2026-10-11 00:10:38 → 00:13:09 local · **2 min 31 s**
**Build:** fix round 2, deployed to `:8000` at 00:09:45 (PID 22104)
**Size:** 15 caller/agent pairs · 6 turns with no audio · 2262-char transcript

**Verdict: the original defect is GONE, and the fix that removed it introduced a
worse one.** The agent invented nothing — for the first time in this register, a caller
asked about a programme that does not exist and was told so, immediately, in the right
words. It then said the same true thing six times while the caller begged it to stop.

Nothing here is inferred. Every quote is from `conversations.transcript` for
`853548f5`; every event is a `voice_events` line for stream `MZ54a0e354…`.

---

## 1. What the fix got right — and it is the thing that mattered

```
[ 1] C: I would like to know about the mechanical engineering program.
[ 2] C: Bachelor.
     A: Meridian University does not offer a mechanical engineering program. We
        offer programs in Arts, Science, Commerce, Business, and Computer Science.
```

**That is the shaped answer working exactly as designed**, on the first attempt, with no
guard intervention. Compare `fbced641`, four hours earlier, where the same opening
produced six turns of a fabricated admissions interview.

```
[ 3] C: I wanna know about the computer science.
     A: Meridian University's B.Tech in Computer Science is a 4-year program with
        eligibility of 10+2 in any stream, and tuition is $14,500 per year.
```

Every fact there is in the knowledge base, and all three are correct.

| | `fbced641` (round 1) | `853548f5` (round 2) |
|---|---|---|
| fabrications spoken | **6** | **0** |
| invented tuition / GPA / SAT | yes | no |
| `UNOFFERED_ENTITY` events | **0** | 5 (1 true, 4 false — see §3) |
| `GROUNDING_DROPPED` | 9 | **0** — nothing needed dropping |
| CRM `program` recorded | `Mechanical Engineering` (fabricated) | `Computer Science` ✅ |
| CRM `name` | `'null'` | `''` (BUG-26 did not recur) |

Zero `GROUNDING_DROPPED` is the good outcome: the prompt block pre-empted the
fabrication, so the output boundary never had to catch it. That is the design working —
shape the answer first, enforce second.

---

## 2. The new defect: the block never expires

```
turns repeating the denial : 6 of 15   (turns 2, 5, 8, 11, 12, 15)
DISTINCT agent turns       : 5
turns with no audio        : 6 of 15   (40%)
```

Six of fifteen turns are the **same sentence**, and three of them come after the caller
explicitly moved on:

```
[ 7] C: focus only on computer science. I don't need mechanical engineering now.
[ 9] C: I told you to change the context from mechanical engineering to computer science.
[10] C: We will only discuss about the computer science.
```

And the two that hurt most:

```
[ 8] C: I am looking to take the admission and I wanted to know the fee structure.
     A: Meridian University does not offer a mechanical engineering program. …

[15] C: What is the thesis?
     A: Meridian University does not offer a mechanical engineering program. …
```

Turn 8 is a direct, answerable question about Computer Science fees — and the caller was
told about a programme they had already abandoned. **They never got the fee structure.**

### Why — and this is the part I got wrong twice

The denial is **the model's own output**, not the guard's substitution. There are no
`GROUNDING_DROPPED` events on this call. The model is following
`not_offered_block()`, and that block is computed from `self._unoffered` — a **call-long
accumulator** — and injected into **every** subsequent prompt.

So from turn 2 onward the model is told, on every turn:

> The caller has asked about something Meridian University does NOT offer: mechanical
> engineering. … Say plainly that Meridian does not offer it …

When the model has something grounded to say (turns 3, 7, 14) it answers. When it does
not, it falls back to the one instruction that is always available — and denies
mechanical engineering at a caller who is asking about Computer Science.

**I fixed repetition of WORDS and left repetition of CONTENT.** Round 1's defect was one
fixed sentence spoken nine times; the fix rotated the wording. The subject stayed pinned,
so the loop survived in a new costume: five distinct sentences instead of one, one of
which fires six times.

### And the bound never engaged

`refusal_index < len(UNOFFERED_REFUSALS)` is meant to allow the specific negative twice
before escalating. But `_refusal_streak` **resets on any good turn**, and this call
interleaved them — denial at 2, answer at 3, denial at 5, answer at 7, denial at 8 …
Every reset re-armed the subject line. The bound assumes consecutive refusals; a caller
who alternates defeats it completely.

---

## 3. Detection: one true positive, four false ones

| turn | detected | verdict |
|---|---|---|
| 1 | `mechanical engineering` | ✅ the true positive — the whole point |
| 4 | `too much piece amount` | ❌ STT garbage |
| 6 | `am talking`, `computer science now` | ❌ fragment; and a REAL programme with a stray word |
| 7 | `focus only`, `mechanical engineering now` | ❌ fragment; superstring of the clean one |
| 10 | `only discuss` | ❌ fragment |

Two faults, both in the item-5 widening:

1. **No shape filter.** "Any content run absent from the corpus" is not "a thing the
   caller named that we do not offer" — ordinary English is absent from a course
   catalogue too. The run also over-reaches: `computer science` **is** in the corpus, but
   the run swallowed `now` and the whole phrase read as unknown. `_unoffered` was
   therefore telling the model the university does not offer **computer science**, which
   it does.
2. **The collapse prefers the longer phrase.** `_unoffered` held the clean
   `mechanical engineering`; when `mechanical engineering now` arrived, my substring
   collapse treated it as the superstring and *evicted the good entry*, keeping the noisy
   one. The list degrades toward garbage as a call goes on — the opposite of the intent.

The true positive fired at turn 1 and the block did its job. Everything after that made
the block steadily less accurate.

---

## 4. What else is unchanged

* **6 of 15 turns produced no audio (40%)** — BUG-05/BUG-13, still live and worse than
  the 22% measured on `944b503f`.
* `"What is the thesis?"` (turn 15, STT garble) drew the denial rather than a
  clarification.
* Closing behaviour was not exercised — the caller did not try to hang up.

---

## 5. Fix list

| # | change | why |
|---|---|---|
| 1 | **The not-offered block expires** — inject it only while the subject is live (named by the caller this turn or the last), not for the whole call | turn 8: a CS fee question was answered with a mechanical-engineering denial |
| 2 | **A subject is dropped when the caller moves on** — a different known programme named, or an explicit change cue | caller said it three times and was ignored |
| 3 | **The refusal bound must count across the call**, not just consecutively | interleaved refusals reset the streak and re-armed the subject line |
| 4 | **Content runs need a shape filter** — a candidate must look like a programme name, same grammar discipline as items 1–4 of round 2 | 4 false positives in 5 detections |
| 5 | **The collapse must prefer the shorter, cleaner phrase** | noise evicted the clean detection |
| 6 | Add this call's two denial-after-topic-change turns to the fixtures | the phrasings that beat round 2 |

### The pattern, stated plainly

This is the third round in which the fix for one defect produced a defect of the same
family one level out:

* round 1 removed the name branch to stop false refusals → the guard stopped catching
  fabrications;
* round 1's guard spoke one fixed sentence → the caller got a loop;
* round 2 rotated the sentence but pinned the subject → the caller got the loop's
  content, and lost an answer they asked for twice.

Each time the measurement was green and the caller was not. The lesson that keeps
recurring in this register — *validate the path the caller travels* — is still not being
applied to the **conversation** as a whole. Every round has tested turns. None has
tested a call.
