# Loadout accuracy

How well matching fits skills to roles. Scores are for what matching suggests, before any person decides.

| File | Contents |
|---|---|
| `real_skills.yaml` | Real public skills at pinned commits, with hand-assigned roles and categories |
| `run_real.py` | Measures them. Reads public GitHub, and needs no LLM key |
| `results_real_tune.json` | The sweep used to choose the threshold |
| `results_real_heldout.json` | A repository not used for tuning, labelled before scoring, and scored once |
| `run_rerank.py` | Measures the shortlist and the model's re-rank |
| `results.json` | The hand-written fixture in `try_professional/bench/retrieval/loadout_fixture.yaml` |
| `run_loadouts.py` | Sweeps the threshold on that fixture |

## What happened

1. The threshold was first chosen on the hand-written fixture alone: 0.68, with 26/26 on its held-out half. The same author wrote its skills and the roles' `skill_needs`, so they share vocabulary.
2. On real skills, 0.68 suggested nothing at all.
3. With wording alone, the tuning set reached about 45% precision. Code review matched Compliance and Sales.
4. Adding a person-assigned category, a penalty for one that doesn't fit, and a limit of 4 roles per skill reached 86% precision and 67% recall on the tuning set.
5. The threshold was fixed at 0.44. A separate real repository was then scored once: 10 of 11 suggestions were right, and 10 of 23 expected matches were found.

## Re-rank

Recall of 43% isn't enough for the project's goal, so embeddings now only shortlist, and the cheap model judges fit. `run_rerank.py` measures it, with categories hidden, because a fresh install won't have a person's.

Measured on 2026-11-06 on `deepseek-v4.1-flash-1017`. **It missed the target set in advance: recall of at least 60% on the fresh set.**

| Set | Embeddings, with a person's categories | Re-rank, with no categories |
|---|---|---|
| Fresh: 30 skills, scored once | 16/23 precise (70%), 16/39 found (41%) | 20/29 precise (69%), 20/39 found (51%) |
| Held out: 14 skills, labels seen before | 10/11 (91%), 10/23 (43%) | 15/20 (75%), 15/23 (65%) |
| Tuning: 12 skills, prompt tuned on it | 86%, 67% | 27/34 (79%), 27/27 (100%) |

Trust the fresh row. The tuning row shows how far a small set can flatter a prompt: three versions were tried on it, and 100% became 51%. On the fresh set, 16 of the 19 misses were the judge's and 3 were the shortlist's. `engineering/backend` chose no skills at all, and rejected all 5 of its labelled matches, including `stripe-integration-expert`, ranked second. That could be a bad answer, or a real judgment, because the first version didn't record it. The code now retries a misshapen answer instead of reading it as "no", and reports roles that chose nothing. All of this cost 187,811 tokens, including 54,623 for one 22-role pass over 30 skills.

A shortlist limits recall whatever the judge does. On the two sets combined (29 skills), a size of 12 kept 38 of 50 labelled matches, and 20 kept 48, so the size is 20. That choice looked at the held-out labels, so they can't give a clean result any more. The `fresh` split comes from a third repository, was labelled before anything scored it, and is scored once.

## Limits

One person labelled every set and assigned the categories, and the real sets are small. Roles' categories were also extended while labelling the tuning set, so that set is favored. Tools were set aside to measure matching alone.

## Reproduce

```bash
python docs/research/loadouts/run_real.py tune.json tune
python docs/research/loadouts/run_real.py heldout.json heldout 0.44
python docs/research/loadouts/run_loadouts.py fixture.json
python docs/research/loadouts/run_rerank.py shortlist all       # free
TRYPRO_RERANK_APPROVED=yes python docs/research/loadouts/run_rerank.py judge tune tune.json   # paid
```
