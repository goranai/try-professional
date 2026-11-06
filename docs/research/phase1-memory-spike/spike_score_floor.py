"""Throwaway calibration check. usage: spike_floor.py <out.json>
With the lexical gate off, does Mnemosyne's dense_score separate correct matches from nonsense?
Reuses the labelled set from spike_40_skills without running its main body.
"""
import ast
import json
import os
import statistics as st
import sys
import tempfile
from pathlib import Path

here = Path(__file__).parent
tree = ast.parse((here / "spike_40_skills.py").read_text(encoding="utf-8"))
ns = {}
for node in tree.body:
    if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") in {"TARGETS", "DISTRACTORS", "QUERIES", "NONSENSE", "LANGS"}:
        exec(compile(ast.Module([node], []), "set", "exec"), ns)
ALL = {**ns["TARGETS"], **ns["DISTRACTORS"]}

data_dir = Path(tempfile.mkdtemp(prefix="mnemo-spike-"))
os.environ.update(
    MNEMOSYNE_DATA_DIR=str(data_dir),
    MNEMOSYNE_EMBEDDING_MODEL="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    MNEMOSYNE_EMBEDDING_DIM="384",
    MNEMOSYNE_FASTEMBED_CACHE_DIR=str(here / "fastembed-cache"),
    MNEMOSYNE_LEXICAL_GATE_MIN="0",
    HF_HUB_DISABLE_SYMLINKS_WARNING="1",
)
from mnemosyne.core.banks import BankManager  # noqa: E402
from mnemosyne.core.memory import Mnemosyne  # noqa: E402

BankManager(data_dir=data_dir).create_bank("skills")
m = Mnemosyne(session_id="spike", bank="skills")
for sid, desc in ALL.items():
    m.remember(f"{sid}: {desc}", source="skill-index", scope="global", importance=0.5)


def sid(h):
    return h.get("content", "").split(":", 1)[0]


correct, wrong_top, by_lang = [], [], {}
for target, qs in ns["QUERIES"].items():
    for lang, q in qs.items():
        hits = m.recall(q, top_k=5)
        if not hits:
            continue
        for h in hits:
            d = float(h.get("dense_score") or 0)
            if sid(h) == target:
                correct.append(d)
                by_lang.setdefault(lang, []).append(d)
        if sid(hits[0]) != target:
            wrong_top.append(float(hits[0].get("dense_score") or 0))
nonsense = {q: [round(float(h.get("dense_score") or 0), 3) for h in m.recall(q, top_k=5)] for q in ns["NONSENSE"]}
nonsense_top = [max(v) for v in nonsense.values() if v]


def pct(xs, p):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(p * len(xs)))], 3)


out = {
    "correct_hits": len(correct),
    "correct_dense": {"min": round(min(correct), 3), "p10": pct(correct, 0.10), "median": round(st.median(correct), 3), "max": round(max(correct), 3)},
    "by_language_min_median": {k: [round(min(v), 3), round(st.median(v), 3)] for k, v in by_lang.items()},
    "nonsense_top5_dense": nonsense,
    "nonsense_top_max": max(nonsense_top),
    "floors": {},
}
for f in (0.3, 0.4, 0.5, 0.6, 0.62, 0.7, 0.8):
    out["floors"][str(f)] = {
        "correct_kept": sum(d >= f for d in correct),
        "of": len(correct),
        "nonsense_queries_with_any_hit": sum(any(d >= f for d in v) for v in nonsense.values()),
    }
Path(sys.argv[1]).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, indent=1))
