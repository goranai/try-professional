---
description: Give the company a command, and run it to a result.
argument-hint: <what you want done>
---

Run this through the company: $ARGUMENTS

You coordinate, and you aren't a role in the company. Your trypro tools can't do a role's work, and they don't show you what a role was asked. Use them like this:

1. If the command is "continue" and a run_id, don't start anything: call run_next_action with that run_id. Otherwise, call run_start with the command as written. It returns a run_id, and the first actions.
2. For each action, start the subagent named trypro-<department>-<role> for its role (a role of marketing/seo is trypro-marketing-seo). Give it only the run_id. When there are actions for several roles, start all of them at once, in one message, so they work at the same time, one subagent per role. Never add what another role did. An action that says runs_in works in another agent: don't start a subagent for it. Call run_step with its run_id and invocation_id, at the same time as the subagents, and while it says working, call it again.
3. Call run_next_action, and repeat until its status isn't "working".
4. If its status is "blocked", show me each question under needs_a_person, word for word, with its options, and ask me which. Never choose for me. When I've answered in my own words, record it with person_answer, then carry on from step 3.
5. If a role's subagent won't start, or replies NO_ROLE_TOOLS, stop, and tell me: Claude Code leaves out each role's own tools until this exact folder is trusted, so open Claude Code here once and accept its prompt, and then run it again. That holds for `claude -p` too. Never do a role's work yourself.
6. If its status is "done", tell me what came back, including everything under known_limits, and give me the path under deliverables. Then go through what it left for me, one kind at a time, and ask me about each item, in my language: company_changes (add this role? record with person_decide_company_change), and company_memory (keep this for the whole company? record with person_decide_memory). Only record what I said.
