# How the README's demo was recorded

`docs/assets/demo.svg` is a real run, on 2026-11-08, through Claude Code 2.1.319, on `claude-sonnet-5-5`, on a Claude plan. `record_demo.py` ran the quickstart in a new folder, and saved each step's output and how long it took in `demo.json`. `render_demo.py` drew that as an animated terminal.

## What's real, and what isn't

- **Every line on screen is real output,** from that run.
- **Waiting is cut.** Each cut says how long it was: designing the company took 5 min 50 s, and the command 1 min 6 s.
- **Long output is shortened,** and marked "… (shortened)". A shortened screen keeps its beginning and its end.
- **Paths are replaced** with `<folder>`.
- **Claude Code ran non-interactively,** with `-p`, so it shows the command and its answer, not a live session.
- **A script answered the real review screen,** standing in for the person who approves. It ran `trypro init --review` with the terminal check switched off, and typed "a" and "approve". In real use only a person at a terminal can.
- **The command shown was the second one tried.** The first, "Write a two-sentence reply to a customer asking whether we cover vegetarian meals", gave a new company no facts. QA rightly sent its reply back three times for claiming the menu had vegetarian dishes, and it stopped to ask a person. The demo's command states the fact itself.

## Tokens

At API list prices, as Claude Code reported them. They ran on a Claude plan.

| Step | Input | Cache write | Cache read | Output |
|---|---|---|---|---|
| Design the company | 46 | 78,285 | 1,147,074 | 10,974 |
| The first command, which stopped | 134 | 75,760 | 1,198,873 | 10,903 |
| The command shown | 48 | 46,968 | 398,299 | 3,375 |

## Reproduce

```bash
python docs/research/demo/record_demo.py <new folder> demo.json
python docs/research/demo/render_demo.py demo.json demo.svg
```
