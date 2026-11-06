# Industry templates

A template is where `trypro init "<business>"` starts. The Org Architect picks the closest one and adapts it. **Adding an industry is a good first contribution.**

## Add one

Create `templates/<your-id>/template.yaml`. Copy `youtube-shorts-studio` and change it. Then run:

```bash
python scripts/generate_schemas.py    # copies it into the package
pytest packages/try-professional/tests/test_generator.py -k "Templates"
```

A test compiles every template, checks it against the spec and company policy, and runs a whole company through it with no model.

## What goes in it

| Field | What it's for |
|---|---|
| `id`, `name`, `description` | What it is |
| `examples` | Businesses it suits. Used to choose a template locally, so add a few, including in other languages |
| `departments` | The departments doing the work, each with one role of `kind: head` |

**Leave out Executive, Legal, and QA.** Every company gets them. You also can't set tools, gates, budgets, escalation, or model settings: a role's `kind` decides its tools, and a template can't express the rest. That keeps templates safe to accept from anyone.

## Writing good roles

- **Keep it small.** Every role adds tokens on every run. Prefer 10 to 15 roles of your own.
- **`skill_needs` find skills.** Use short, concrete, English phrases, such as `write a video hook`. Vague ones find nothing.
- **Say what a role can't do.** The company produces files in v0.1. An editor writes edit notes, and doesn't edit video. It has no live data unless a person provides a file.
- **Outputs:** `PublicContent`, `ResearchReport`, `TaskReport`, or `CodeChange`. Roles that output `ResearchReport` follow the rule on current claims, and can use web search if it's on.
