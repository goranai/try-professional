# Management playbooks

How managers and staff behave, for any business. These are the core of Try Professional, and they're ours to write and refine. GitHub skills supply staff expertise, and don't replace them.

| File | Who it's for |
|---|---|
| `ceo.md` | The root role |
| `manager.md` | Department heads |
| `staff.md` | Every role doing or reviewing a task |
| `memory-and-skills.md` | What each level retrieves, and when |

Rules for changing them:

- **Keep them general.** Nothing here may name an industry. Templates add industry knowledge through each role's own instructions, and no template or company can turn a playbook off.
- **Keep them small.** Only the section for the current step reaches the model, and a test limits it to 250 tokens with the memory policy added.
- **Raise the version** in the file's header when you change it. Every run records the versions it used.
- **Run `python scripts/generate_schemas.py`** afterwards. It updates the copy packaged with the code, and a test fails if the two differ.
