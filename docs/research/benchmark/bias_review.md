# The bias review

Written by a separate Claude instance (`claude-opus-5-5`, through `claude -p`, with no tools and no project context), on 2026-11-08. It was given only the goals, rubrics, and rules, with the setups called A, B, and C, and asked to check them for bias. Its answer is below, unedited. What was done with each note is at the end of [the design](DESIGN.md).

---

## Problems

### 1. Closing section: produced differently for C (High)
- **Item:** Rule "The closing section"; H item on every goal.
- **Favors / hurts:** C, in either direction. A and B are also disadvantaged on input parity.
- **Why:** A and B get an extra instruction, so their input isn't identical to C's. They write the section for this test. C's section is a translation of a report built for another purpose. "Open issues" are not the same as unmet goal parts: they can include process notes, which H penalizes. They can also be QA-vetted, which H rewards. H is reported on its own, so this measures the mapping, not the setups.
- **Fairer:** "Every setup gets the same closing instruction, appended word for word after the goal text, and writes `deliverables/CLOSING.md`. C's native report is logged but not graded."

### 2. Stuck / Retry / Cancel only reachable by C (High)
- **Item:** Scripted person: the "stuck" row and the classification rule.
- **Favors / hurts:** C on both sides. Only C can get a free restart, and only C can be cancelled into "unfinished". A and B can only be classified as asking about budget or about something else.
- **Why:** The question type comes from C's native types, while A and B are classified by budget keywords. "Unfinished" feeds the headline pass rate.
- **Fairer:** "The harness, not the setup, detects a stall: no file change or board/message activity for N turns. The same rule applies to all setups. On the first stall, the harness resumes once. On the second, the run ends unfinished. All questions are classified by one keyword rule on the question text. C's native type is logged only."

### 3. "Finished" is undefined per setup, and unfinished work isn't graded (Medium)
- **Item:** Headline pass rate.
- **Favors / hurts:** Hurts C, and B to a lesser degree. C's QA gate and B's consensus can both run into the cap. A just stops.
- **Why:** The gate is the variable under study. But if work that exists at the cap gets zero, the pass rate mixes up "never converged" with "made bad work".
- **Fairer:** "Finished = the setup's own completion signal before the cap. A: final answer. B: all roles agree. C: QA approves, and Legal too where triggered. Work present at the cap is graded for every run. Report the pass rate, the must-item rate on all runs, and the finish rate separately."

### 4. The scripted answer is an honesty hint (Medium)
- **Item:** The "anything else" answer: "Use only the facts given, and state anything else as unknown."
- **Favors:** Whichever setup asks most. That is likely C, which stops for a person by design, then B, which has several askers.
- **Why:** The answer coaches exactly what M "no invention" items and H grade.
- **Fairer:** Put that sentence in a shared preamble given to all setups. The scripted answer becomes just "Not available."

### 5. The 5-answer cap counts budget questions (Medium)
- **Item:** "After 5 answers in one run…"
- **Hurts:** C, if it checks in on budget routinely. It can burn its 5 answers, then be told "Finish with what you have."
- **Why:** That instruction ends the run early for reasons unrelated to the work.
- **Fairer:** "Budget answers don't count toward the cap. The cap applies to other questions only. The budget cap alone stops the run."

### 6. Blinding covers only the closing section (Medium)
- **Item:** Grading: "random code"; the normalization script.
- **Favors / hurts:** Reveals B and C. Review notes, "Approved by QA" lines, role headers, or several draft files can sit inside the deliverables. Graders may also judge leftover process text.
- **Fairer:** "For each setup, 'the work' is only the final files in `deliverables/`. The same script strips lines naming a role, reviewer, approval, or run from all deliverables before grading. Stripped lines are logged and counted as a separate metric."

### 7. The must items mostly test trap avoidance, and there's no cost metric (Medium)
- **Item:** Rubric balance across G1–G6; the headline metric.
- **Favors:** C, and partly B. About 15 of 26 must items are "never claim X" or "match fact X", which is what review gates are for. With a shared cap, a setup that spends 5× more counts the same.
- **Fairer:** Keep the traps. Also report tokens, wall-clock, and cost per passing run. Add one must per large goal on internal consistency across pieces, for example: "the script, sheet, and FAQ state the same fee and hours."

### 8. Prompt maturity is uneven (Medium)
- **Item:** The setup definitions. C runs "as it does in real use" with native reporting. A and B are built for the harness.
- **Hurts:** A and B, if their prompts get less engineering. That makes them a strawman.
- **Fairer:** "Each setup's prompts get equal tuning effort on non-benchmark tasks, by someone aiming to make that setup win. All prompts are published with the results."

### 9. "No invented facts" is weighted unevenly across goals (Medium)
- **Item:** G2 M3 and G5 M5 (must); G4 S3 (should); G1, G3, G6 (only via H).
- **Favors:** Review-gated setups, but only on some goals. The pass rate then swings with goal choice.
- **Fairer:** Add the same must to every goal: "Claims no fact about price, hours, area, service, credentials, or outcome that the facts don't support."

### 10. G5 "favourite" conflicts with H (Low–Medium)
- **Item:** G5 seeded flaws vs. the H item.
- **Hurts / favors:** Cautious setups (C's Legal, B's compliance role). "Austin's favourite groomers!" is an unsupported claim, which H says to list, but the rubric says to keep it. Re-spelling it as "favorite" is also unaddressed.
- **Fairer:** "A 'claim' is a checkable statement of fact. Puffery isn't one. Listing it, keeping it, or re-spelling it is neither credited nor penalized."

### 11. G2 M3 "extra service" is ambiguous (Low)
- **Hurts:** Setups that brainstorm more (B). Suggesting a Frome florist, or that a relative collect at 8am, could be read as an invented service.
- **Fairer:** "Offers no delivery to Frome and no service the facts don't list. Suggesting something the customer could do themselves is allowed."

### 12. Word limits and the holistic score vs. the closing section (Low)
- **Item:** G2 S3, G6 S3, holistic score.
- **Why:** C's closing may arrive attached to its work, while A and B write a separate file.
- **Fairer:** "Word limits and the holistic score apply to the deliverable only, never to the closing section."

### 13. B's answers arrive via the board on a later turn (Low)
- **Hurts:** B. Other roles can reach consensus before the asking role reads its answer.
- **Fairer:** "B can't finish while a question is unanswered. The asking role's next turn comes immediately after the answer is posted."

### 14. Question counts aren't comparable (Low)
- **Item:** "Number of questions per setup."
- **Why:** C's routine stop-for-person checkpoints inflate its count.
- **Fairer:** Report counts by kind (budget, stall, other) and per run.

## Other notes
- **Sample size:** 3 runs × 6 goals is too few for pass-rate gaps of under about 30 points to mean much. Report per-goal results and the must-item rate, with intervals.
- **G4:** Graders need fluent Indonesian. G4 S1 is subjective, so use two graders and report agreement. Do the same for G2 M4 (tone).
- **G3:** The year isn't stated, and 3 March is a Tuesday only in some years. State the year, or accept any consistent dating.
- **Legal triggers:** Log which goals triggered C's Legal reviewer. G5 and G6 probably did, which explains any per-goal gap.

## Fair, and should stay
- Same model, skills, budget, and no web. Identical goal text, with all facts inside the goal.
- Trap facts in every goal, and a mix of small, large, fix-a-draft, and non-English goals.
- Blind grading under random codes, and a must/should split with a holistic owner test.
- Scripted answers that never waive a review, approve work, or add facts. Every Q&A is logged.
- H reported on its own, and the normalization removes role and reviewer wording.
- G5's explicit seeded-flaw list, with "favourite" called a style choice (with the clarification in #10).
- Length and tone items in G2 and G6. They reflect real channel needs, and over-cautious legal wording paying a cost there is a fair result.