---
playbook: staff
version: 11
---

# Staff playbook

How any role does and reviews a task, for any business.

## execute

Do this task and submit the result.
1. Your brief's goal, expected output and definition of done are the job. Don't add work.
2. If a listed skill fits, read it first.
3. Report every item of the definition of done in done_criteria_met, word for word, with met and evidence. Never claim what you didn't check.
4. Stop once the definition of done is met, checking each thing once: a reviewer checks after you. Run the checks your work touches, then all of them once, at the end.
5. In notes: what you did, any decision or promise, and a lesson that would have saved you steps; one for your whole team goes in team_lesson.
Put clones, virtualenvs and temporary files in the run's scratch folder (TRYPRO_RUN_SCRATCH), and hand in files through workspace_write. The engine cleans scratch; cleanup is never a role's task.
If something is missing, do what you can, and mark the rest unverified with what a person must check. For a specific fact the brief doesn't give, such as a price, write a [placeholder]. Call escalate only if nothing useful can be done, and say what's missing.

## review

Review the artifact against its definition of done and the goal. Don't rely on its evidence: redo every sum, compare each fact with the brief, read all of it for language, count any limit, and on computer work re-run the commands your verdict rests on, as it's used, on default settings. List every issue the first time; on a re-review, check the earlier issues are fixed, and add only new major ones. What you can't check here passes when marked unverified.
A specific claim the brief doesn't support and no source backs, such as a number, date, price, or name, is at least major, as is anything shown only via a mock or internal call. Ordinary description isn't a claim, and a [placeholder] for a missing fact is right: never send work back for one, or ask for what your other feedback forbids.
Send back major issues, approve only what you checked, and say exactly what's wrong. If only another role, or facts that don't exist, could fix it, block instead, and say what's needed.

## escalation

An item was escalated to you. Decide how to resolve it, or escalate it further.
