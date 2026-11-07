# Peak memory

How much RAM Try Professional's steps need, read from the operating system, and how the numbers changed when the second copy of the embedding model was removed.

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
