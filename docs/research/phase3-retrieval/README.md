# Phase 3 retrieval measurements

Skill retrieval across English, Indonesian, Spanish, Chinese and Arabic: 40 skills, 200 queries, 20 nonsense queries. The floor comes from the development split only, and the held-out split is scored once with it.

These are hand-written queries, and only English needs no translation review, so treat the numbers as evidence for this design, not as general accuracy.

## Reproduce

```bash
pip install -e packages/try-professional psutil
python docs/research/phase3-retrieval/run_retrieval.py results.json
python docs/research/phase3-retrieval/compare_e5.py compare_e5.json <model cache dir>
```

No LLM key is needed. `compare_e5.py` downloads about 2.2 GB.

## Files

| File | Contents |
|---|---|
| `run_retrieval.py`, `results.json` | Catalog against a Mnemosyne bank, with the default model |
| `compare_e5.py`, `compare_e5.json` | The default model against `intfloat/multilingual-e5-large` |
| `results_no_floor.json` | The catalog after removing its floor: held-out, plus 20 fresh English queries |

The same labelled data ships in `try_professional/bench/retrieval/`, and `tests/test_cross_lingual.py` checks it on every run of `pytest -m model`.
