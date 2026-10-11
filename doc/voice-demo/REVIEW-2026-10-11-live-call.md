# Live call review — `fbced641`, inbound +17208402670

**Call:** `fbced641-7592-4dfe-9033-d7ce6d475f21` · stream `MZa1b5379…` · inbound
**When:** 2026-10-10 23:37:43 → 23:42:04 local (UTC+7) · **4 min 21 s**
**Build:** the BUG-17/BUG-18 fix, deployed to `:8000` at 23:35 (PID 20380)
**Size:** 25 caller/agent pairs · 6 turns with no audio · 4159-char transcript

**Outcome: the fix failed on this call, in both directions, and introduced a third
defect that did not exist before.** The caller diagnosed all three themselves and ended
the call on it.

Everything below is from `conversations.transcript` for `fbced641` and the
`voice_events` lines for stream `MZa1b5379…`. Nothing is inferred.

---

## Headline

| | expected | actual |
|---|---|---|
| A caller asks about a programme that does not exist | a plain negative | **six turns asserting the programme**, then an admissions interview |
| A caller pushes back on a refusal | the position holds | it held — but the damage was already done |
| A caller stops repeating | the agent stops | **9 identical refusals in 8 of 25 turns**, and the caller said so |
| The CRM records the call | the real subject | `program: 'Mechanical Engineering'`, `name: 'null'` |

---

## 1. Six fabrications reached the caller

Every one of these was **spoken**, and none was refused:

```
[ 2] To be eligible for our mechanical engineering program, you'll need to have
     completed a bachelor's degree in mechanical engineering or a related field.
[ 3] To be eligible, you'll need to have completed a bachelor's degree in
     mechanical engineering or a related field. Can you tell me what your GPA is?
[ 5] That's a long-term experience in mechanical engineering, which is impressive.
     What inspired you to pursue a graduate degree at this stage in your career?
[ 6] It's great that you're considering furthering your education. What specific
     areas of mechanical engineering interest you the most?
[ 8] We have a few different program options available, including a Master's in
     Mechanical Engineering and a PhD in Mechanical Engineering.
[ 9] We have a Master's in Mechanical Engineering with a focus on advanced
     materials and manufacturing, and a PhD in Mechanical Engineering with a
     specialization in robotics and automation.
```

By turn 3 the agent was **screening the caller for a programme the university does not
have** — it asked for a GPA, got `3.6`, called it "competitive for our program", then
asked about work experience and got 15 years. For six turns this was a functioning
admissions interview about nothing.

### Why the guard missed all six — verified

```
"We have a few different program options available, including a Master's in
 Mechanical Engineering and a PhD in Mechanical Engineering."

  phrases : ['Master s', 'PhD in Mechanical Engineering']
  known   : ['Master s']            <- IS in the corpus ("a relevant master's degree")
  CLAIMS  : []                      <- gate suppresses everything; both SPOKEN
```

`Master's` tokenises as `Master` + `s`, producing the phrase `Master s`, which the corpus
contains. **The gate then suppresses the entire sentence** — including
`PhD in Mechanical Engineering`, a second fabrication sitting in the same breath.
`bachelor's` (turns 2, 3) is the same hole.

Two faults, and the second is the amplifier:

* the possessive artifact creates a phrase that looks like a known entity;
* the gate is **all-or-nothing per sentence** — one known-looking phrase disables name
  checking for everything else in it.

That gate was written to stop the BUG-16 false positives, where a *list* of programme
names legitimately contains one slightly-wrong name. A list excuses its neighbours. It
must not excuse an unrelated clause elsewhere in the sentence.

---

## 2. BUG-17's shaped negative never fired — not once

`UNOFFERED_ENTITY` appears **0 times** in the entire log.

The caller named the subject in fragments — `"in mechanical engineering."`,
`"Bachelor degree."` — with no keyword-anchored phrase, so `unoffered_entities()`
returned nothing, `_unoffered` stayed empty, and `not_offered_block()` was never injected.
The model was never told the thing does not exist, so it did what the prompt rule alone
had always failed to prevent.

**The shaping half of the BUG-17 fix contributed nothing on this call.** The output
boundary did hold from turn 7 onward — `PhD in Mechanical Engineering`, `program in
Mechanical Engineering`, `Kalki` and others were correctly dropped — but the boundary is
the *second* line. By the time it engaged, six turns of fabrication had already been
spoken, and the caller had answered questions about a non-existent degree.

Detection must not depend on the caller using a keyword. It has to work from the corpus
against the caller's words, the way the guard does.

---

## 3. NEW — the refusal loop, which the fix made deterministic

```
occurrences of "I don't have that detail in front of me. I can check and come
back to you, or help with something else about the programme."   :  9
turns containing it                                             :  8 of 25
```

Turn 13 contained it **twice in one breath**. The caller's own words:

```
[14] C: Why are you repeating? I don't have any detail with you. You have detail. I know.
[16] C: this is a defect in your system.
[17] C: and you ask your developer team to fix this.
[18] C: then stop repeating the thing.
[25] C: You stop the call.
```

**This is a defect I introduced.** `GROUNDED_REFUSAL` is a single fixed string, and the
boundary substitutes it verbatim every time it fires. Before this change the model
produced varied refusals some of the time; now the boundary produces the *same* sentence
*every* time. BUG-08 already recorded repetition as a defect — I took something the model
did occasionally and made it unconditional.

A guard that refuses is right. A guard that refuses **identically and indefinitely** is a
loop, and the caller cannot escape it. It needs a bound (say two), then escalation to the
human channel, and an explicit "stop repeating" signal the agent honours.

---

## 4. Knock-on damage

**The CRM recorded the fabrication — again.** Seven seconds after the call:

```
extracted_lead = {'name': 'null', 'email': 'null', 'program': 'Mechanical Engineering'}
```

BUG-25 and BUG-26, second occurrence tonight, on the same phone number. The previous
row was hand-fixed to `Mechanical Engineering` as a *name*; this one stores it as the
*programme*. Both are fabrications promoted to business records.

**Meta-leak escalated.** Asked to pass feedback to the developers, the agent accepted the
frame:

```
[17] A: "I apologize for the inconvenience. We're taking steps to improve our system
        to provide more accurate information."
[20] A: "...what you think would make our system more accurate in providing program details?"
```

It discussed "our system" with the caller and claimed improvement work. BUG-01/03 class —
and a direct consequence of having nothing else to say while looping.

**The close was ignored again.** `"Thank you."` → interrupted. `"the call."` →
`"I think there may be some confusion about the call. Is that correct?"` (the BUG-22
template). `"You stop the call."` → `"I'm going to stop the call as you've requested."`
Three attempts. No closing path fired. BUG-14.

---

## 5. What held

Not everything failed, and it matters for the next fix:

* **The boundary fired 9 times, and 7 were correct** — `PhD in Mechanical Engineering`
  (×3), `program in Mechanical Engineering`, `Kalki`, and two turns where the fabrication
  was the only content. The branch is neither dead nor indiscriminate.
* **The one phrasing that always worked was the one with a connective**:
  `PhD in X`, `program in X`. That is rule 1 in the proposed fix, and it is already
  carrying the load — which is why it should be made explicit rather than incidental.
* **Three false positives** now confirmed: `program specifically`, `interdisciplinary`,
  `providing` — all single non-name words swallowed by the greedy walk. Rule 3
  (keyword-last needs ≥2 words) covers all three.

---

## 6. What the call taught that my test plan did not

I gave a four-turn script (ask about the programme, push back, ask for the list, ask the
MBA fee). The caller did something else, and it was more informative:

* they named the subject in **fragments**, which defeated the unoffered detection;
* they **answered the agent's questions** rather than pushing back, so the fabrication ran
  for six turns unchallenged — a real caller follows the agent's lead;
* they gave up on the substance and **attacked the repetition**, which is what actually
  ended the call.

The archive gate passed 7/7 because every archived phrasing was `Bachelor of Mechanical
Engineering program`. The model, live, produced `a bachelor's degree in mechanical
engineering` and `a Master's in Mechanical Engineering`. **The gate validated the
phrasings it had.** That is the fifth time this register has recorded the same mistake,
and the first time the author of the change made it.

---

## 7. Fix list

| # | change | why |
|---|---|---|
| 1 | **Gate per phrase, not per sentence** — a known name excuses only a coordinated neighbour, never an unrelated clause | turn 8: one possessive hid two fabrications |
| 2 | **Possessives are one token** (`bachelor's`, `Master's`) | removes the artifact that made the gate pass |
| 3 | **Keyword-first names require a connective** (`School of X`, `PhD in X`) | kills `program specifically`; formalises what already works |
| 4 | **Keyword-last names need ≥2 words** | kills `interdisciplinary`, `providing` |
| 5 | **Unoffered detection from the caller's words against the corpus**, not keyword-anchored only | BUG-17's shaping half never fired |
| 6 | **The refusal must vary and terminate** — bounded repeats, then the human channel | 9 identical refusals ended this call |
| 7 | Add this call's sentences to the replay fixtures | the phrasings that beat the gate |

Items 1–4 are the name extractor. Item 5 is BUG-17. **Item 6 is mine to own** — it is not
in the original register because the register's authors never wrote a guard that spoke.
