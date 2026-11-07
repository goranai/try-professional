# Peak memory

How much RAM Try Professional's steps need, read from the operating system, and how the numbers changed when the second copy of the embedding model was removed.

## A whole company over MCP

Each role runs its own MCP server, so before this fix each one could hold its own copy of the embedding model. Now one process per company holds it, and every role's server asks it. It only turns text into vectors, and knows nothing about roles or memory, so isolation stays in the memory rules, the skill loadouts, and each server's tool list. `measure_company_ram.py` measured a 22-role company on Windows 11 on 2026-11-07, with every server really recalling memory, in unique memory, so nothing shared is counted twice. Raw numbers are in `company_ram.json`.

| | Peak |
|---|---|
| A typical command: the operator, one role at a time, and the service | **743 MB** |
| Every role's server open at once, sharing the service (22 servers at about 97 MB, and the service at 581 MB) | **2,712 MB** |
| One server per role before the fix, measured on 3 servers (1,856 MB, or about 619 MB each) | 619 MB per server |
| Every role before the fix, computed and not measured, because it could have taken the machine down | about 13.6 GB |

Agents run one at a time, so the typical line is what an ordinary command needs. The service exits after 10 minutes idle. `TRYPRO_EMBED_SERVICE=off` turns it off, and if it can't start, a process loads its own copy.

## Reproduce

```bash
pip install -e packages/try-professional psutil
GITHUB_TOKEN=... python docs/research/memory/measure_peak_ram.py <work_dir> --discovery
```

It's free, and uses no model API. `--discovery` searches GitHub and unpacks real skills, as `trypro init` does. Each step runs in a fresh process, after waiting for enough free memory.

## Results

Windows 11, Python 3.13, 32 GB of RAM, on 2026-11-07. See `peak_ram.json`.

| Step | Before | After |
|---|---|---|
| Import the CLI, with no model | 57 MB | 57 MB |
| Load the model, one query | 625 MB | 625 MB |
| Embed 60 long skill descriptions | 811 MB | 664 MB |
| Our embedder and company memory in one process | 1,183 MB | 648 MB |
| `trypro init --template`, no discovery | not measured | 636 MB |
| `trypro init` with real GitHub discovery (20 searches, 21 skills pinned) | not measured | 713 MB |

Two changes made the difference. Mnemosyne loaded its own copy of the model, and now shares ours. Embedding uses batches of 8, where the default of 256 pads more text at once. Thread count made no difference.

No temp folders were left behind after these runs.

## With a real model

The org generator evaluation ran 16 more real generations in one process, on the cheap model. Peak RAM per generation was 585 to 772 MB, and 772 MB was the process's peak overall. Generations that also searched GitHub and matched skills peaked at 666 to 772 MB. Each one's figure is in that run's `results.json` as `peak_ram_mb`. It left no temp folders behind.

`trypro init` waits until 1,824 MB is free: 800 MB for the work (772 MB measured, rounded up), and a 1,024 MB safety margin.

The scripted `trypro run --dry-run` loads no memory bank and no model, so its 60 MB says nothing about a real command. A real command's peak comes from the 4c journey.
