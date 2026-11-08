# Cheap models, through the company's own engine

Real commands on `step-5-preview`, through the API runner, on 2026-11-08, using the gateway in the owner's `.env`. `measure.py` runs 5 jobs in a new default company each time, with the shell and the web on: a customer reply, an Instagram plan, a menu fix, a job that must use `gh` to find a GitHub repo, and a customer reply in Indonesian. `results.json` has every run.

## What went wrong at first, and what changed

| What broke | How often | The fix |
|---|---|---|
| **Every hand-in refused over field names.** The model wrote `text` for `claim`, `issue` and `fix` for `description`, and added `status` or `verified` | Most runs | Known alternative names are mapped, unknown fields in nested items are dropped, and a claim with no source is marked unverified, which is what a person is told. The strict check still runs after |
| **The gateway refused a long system message beside some user text,** with a 400 and no reason. The same words in one message went through, every time | Every Indonesian job | On a vague 400, the client retries once, then folds the instructions into the first user message for the rest of the run |
| **The gateway was briefly down,** with 502s | Often | 5 retries, with waits up to 30 seconds, instead of 2 quick ones |
| **Token limits** of 60,000 for a small command. A reasoning model's thinking counts as output, and one review wrote 12,700 tokens | 2 of 5 jobs | 120,000 for a small command and 300,000 for a large one, on the API runner. The stuck check still stops a loop |
| **Long shell output** re-sent on every call | The GitHub job | A command's output is cut to its last 6,000 characters |

## Results

| Job | Before | After |
|---|---|---|
| Customer reply | done | done, 5 calls |
| Instagram plan | stopped at its limit | stopped at 123,466 tokens; a second run finished in 6 calls |
| Menu fix | done | done, 3 calls |
| GitHub, with `gh` in the shell | stuck | **done**, 15 calls |
| Indonesian reply | refused by the gateway | **done**, 6 calls |
| **Finished** | **2 of 5** | **4 of 5**, and the fifth finished once in two |

## What it doesn't show

- **One run of each job,** so another run can differ, as the Instagram plan did.
- **One model, through one gateway.** Kimi K3 and DeepSeek finished 3 of 5 each before these fixes, and weren't measured after. GLM 5.3 Flash failed every job at the gateway before them.
- **Reasoning models spend on thinking.** Their output tokens are much higher than a non-reasoning model's for the same work.
