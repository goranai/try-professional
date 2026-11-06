# Phase 1 memory spike

Throwaway feasibility test behind `docs/architecture.md` §8 and §15. It is not project code and not a benchmark: hand-written queries without native review, a single run, one model.

## Reproduce

```bash
python -m venv .venv
.venv/Scripts/python -m pip install "mnemosyne-memory[embeddings]==4.0.0"   # .venv/bin/python on Linux or macOS
.venv/Scripts/python spike_40_skills.py "$PWD/result_default.json"
.venv/Scripts/python spike_40_skills.py "$PWD/result_gate0.json" MNEMOSYNE_LEXICAL_GATE_MIN=0
```

The first run downloads the embedding model into `fastembed-cache/` next to the output file. Move scripts and results out of a synced folder first if you don't want that cache here.

## Files

| File | Contents |
|---|---|
| `spike_small_set.py`, `result_small_set.json` | First pass: 8 skills, bank isolation check |
| `spike_40_skills.py` | Second pass: 40 skills, recall@5, nonsense queries |
| `result_default.json` | Mnemosyne defaults |
| `result_gate0*.json` | Lexical gate off, with further setting variations |
| `spike_score_floor.py`, `result_score_floor.json` | Gate off: dense score of correct hits vs nonsense queries, at candidate floors |
