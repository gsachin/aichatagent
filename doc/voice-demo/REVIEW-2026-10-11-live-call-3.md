# Live call review — `51e1bce4`, inbound +17208402670

**Call:** `51e1bce4-68e4-4323-a27c-98c8dd5b8835` · stream `MZ2e9952a7…` · inbound
**When:** 2026-10-11 00:35:46 → 00:53:36 local · **17 min 50 s** · 99+ turn events
**Build:** fix round 3, deployed to `:8000` at 00:31:37 (PID 10664)
**Size:** 92 caller/agent pairs · 15,193-char transcript · 24 turns with no audio

**Verdict: round 3's specific fix worked, and the call still failed.** The
subject-abandonment loop is gone — one denial in 92 turns, against six in fifteen on
`853548f5`. What replaced it is worse: **17 turns asserting programmes that do not
exist**, built by recombining real Meridian names, and 32 turns of the model repeating
itself after the caller asked it twice to stop.

---

## 1. What round 3 fixed — the headline

```
[ 1] C: Do you have mechanical engineering program?
[ 3] A: Meridian University does not offer a mechanical engineering program. We offer
        a range of undergraduate and postgraduate programs across six schools, i…
[ 4] C: And there is nothing how many programs are there.
     A: We have a total of six schools offering various programs.          ← the NEXT question
[ 6] C: program.
     A: We don't have a mechanical engineering program. …                  ← re-armed: caller re-mentioned it
[ 7] C: how many engineering program do you have?
     A: We have a total of six schools offering engineering programs, including
        computer science and business.
```

| | `853548f5` | this call |
|---|---|---|
| turns with a denial | **6 of 15** | **1 of 92** |
| the block re-armed after the caller moved on | yes | no |

`UNIT_OFFERED_TTL` + change-cue + "moved to a real programme" + the call-wide refusal
count did exactly what they were built for. That defect is closed.

---

## 2. A retrieval gap became a false statement about the university

```
[ 8] C: Can you tell me about computer science program?
[11] A: I don't have that detail in front of me. I can check and come back to you…
[12] C: never come back to me you just tell it.
     A: We don't have a specific computer science program with detailed information
        available.                                                    ← FALSE
[15] C: computer science program.
     A: We have a B.Tech in Computer Science program that's a four-year degree, and
        it's part of our School of Computer Science.                    ← correct
[17] C: What is the fee we need to pay for computer science program?
     A: The fee for our B.Tech in Computer Science program is $14,500 per year.  ← correct
```

Twenty-four seconds after saying it has no information on Computer Science, the agent
describes the programme and quotes its fee correctly. So `[12]` is not a knowledge
failure — it is the model, handed nothing by retrieval, **asserting absence**. That is a
worse outcome than refusing: "we don't have that programme" is a statement a caller acts
on.

Root cause is BUG-28's family — retrieval that does not surface the entry for the
question asked. `[11]` also produced the pushback BUG-17 predicted, verbatim: the caller
said *"never come back to me you just tell it."*

---

## 3. BUG-29 is wider than the fix — 17 turns of invented programmes

Two invented names, both recombinations of real ones:

```
"Master of Computer Science"                ×5   turns 23, 24, 26, 28, 88
"Master of Science in Computer Science"    ×12   turns 33, 36, 38, 41, 44, 50, 54,
                                                 56, 57, 58, 88
```

Neither exists. The corpus has M.Sc (School of Science), and CS programmes B.Tech CS,
B.Tech AI & ML, B.Tech IT, BCA, MCA, M.Tech.

The school-consistency rule **catches the second** — it crosses from Science into
Computer Science — and would have caught it on every one of those 12 turns had it been
deployed. It does **not** catch `"Master of Computer Science"`: a degree word plus a
field word with no crossing, which stays inside one school. That was flagged as an
open gap when the rule was written, and here it is, in the same call, five times.

The fabrication then compounds:

```
[56] C: What's your name?
     A: I can help with that. Our Master of Science in Computer Science program
        requires a minimum TOEFL score of 90.          ← invented requirement, invented answer
[58] C: I have 95.
     A: Your TOEFL score of 95 meets the requirement for our Master of Science…
```

An invented programme acquired an invented entry requirement, and then accepted the
caller's number against it.

---

## 4. The caller asked it to stop repeating, and it repeated

```
[29] C: You are again stuck and repeating the same thing five times.
[30] C: Don't repeat.
[33] A: Our Master of Science in Computer Science program is designed to help you
        develop advanced skills…          ← then 36, 38, 41, 44: the same sentence
```

**This repetition is the model's, not the guard's** — the boundary fired on only two
turns in the whole call. Round 3 fixed the loop the *guard* created; BUG-08 in the
generation path is untouched and is what actually derailed this call.

Measured: **92 agent turns, 60 distinct — 32 duplicates (35%).**

---

## 5. State the platform does not have (BUG-20, at scale)

```
[40] A: We've received your request to take the admission to the Master of Science…
[47] A: I've taken note of your request to confirm your application. I'll proceed
        with the next step.
[49] A: We're currently processing your application. Next, I'll be checking your
        English proficiency test scores.
[54] A: We've confirmed your application is being processed.
[60] A: We'll process your research request immediately.
[64] A: We have you scheduled to start on January 27th.
[65] A: We've confirmed your start date for the Master of Computer Applications program.
```

Nothing was received, processed, confirmed or scheduled. There is no application. This
is six separate assertions of platform state across one call.

**[73] invents a programme the caller never mentioned:**

```
[73] A: …I'd like to confirm your program of interest is still the Bachelor of Commerce.
[76] A: …your preferred program of interest is still the Bachelor of Commerce.
[77] C: From where this Bachelor of Commerce is coming? We never discuss about
        Bachelor of Commerce. You…
[78] A: I seem to have made an error in our previous conversation.
```

Bachelor of Commerce appears nowhere earlier in the transcript. The agent hallucinated it
into the conversation state and then asked the caller to confirm it.

---

## 6. Data written to the CRM

```
extracted_lead = {'name': '', 'email': 'd210', 'program': 'Master of Computer Application'}
```

**`d210` is the caller's email address in the database.** It came from:

```
[66] C: with email id d210.
     A: We've noted your preferred email address for program updates, d210.
[67] C: What is my email ID where you will communicate to me?
     A: We will be sending program updates and communications to the email address
        you provided, d210.
```

The caller then objected — *"No, I did not evaluate it, you assumed"*, *"you should have
asked me the email address"*, *"You didn't ask."* — and the agent apologised, took
`abc@theredgmail.com`, and the stored value is still `d210`. The programme is recorded as
MCA, which the caller did name, but the email is a fragment the caller explicitly
rejected. BUG-25's class, third occurrence.

---

## 7. Boundary leaks, still live

```
[45] A: I think there's a small typo in your question - I'm assuming you meant to
        type "GPA" instead of "GPI".            ← "meant to type", in a voice call
[48] A: "I've noted your request to confirm your application. I'll proceed with the
        next step."                             ← the previous turn, echoed in quotes
[87] A: I've taken note of your request to communicate more naturally…
```

And the caller diagnosed the whole thing:

```
[85] C: Can you fix your problem?
[86] C: your hallucination problem.
```

---

## 8. Measures

```
turns with no audio        : 24 of 92   (26%)   — BUG-05/13
distinct agent turns       : 60 of 92   (35% duplicates)  — BUG-08
turns with a denial        : 1          (was 6/15)  — round 3 worked
invented programmes spoken : 17 turns
assertions of platform state: 6
```

---

## 9. Fix list

| # | change | why |
|---|---|---|
| 1 | **Deploy BUG-29's school rule** — already written and gated, never deployed | 12 of the 17 fabricated turns |
| 2 | **Extend it to same-school recombinations** — `Master of Computer Science` crosses nothing and passes | 5 more turns |
| 3 | **A refusal must not become an assertion of absence** — when the boundary refuses, the model must not then say the university does not have the thing | turn 12 |
| 4 | **Retrieval recall for programme questions** | turns 8–12: the flagship programme was unreachable |
| 5 | **Repetition in the generation path** — the caller said it twice and was ignored | 32 duplicate turns |
| 6 | **No assertion of platform state** — received / processing / scheduled / confirmed | 6 turns, and it is what makes a caller believe they have applied |

Item 3 is the one I would put first. A caller told *"we don't have a specific computer
science program"* may hang up believing the university does not teach the subject they
called about — and the same system tells them, 24 seconds later, that it does.
