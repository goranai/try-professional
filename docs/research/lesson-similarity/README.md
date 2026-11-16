# Comparing repeated lessons

Try Professional 0.7.4 received reports that lessons accumulated as slightly
different wording across runs and crowded useful notes out of briefs. A merge
keeps the first lesson's text and its embedding, records each learning event,
and preserves the existing approval or rejection. Evidence is stored separately
so dates, sources, and repetition counts do not affect semantic similarity.

On 2026-11-16, `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`,
the existing default local memory model, was measured on six paraphrases and
six related but distinct lessons, using FastEmbed 0.9.1 and the Qdrant ONNX
checkpoint at revision `faf4aa4225822f3bc6376869cb1164e8e3feedd0`. The captured
fixture also records the ONNX file's SHA-256. The first three pairs of each kind are the
calibration set; the other three pairs of each kind check the chosen threshold.
The source and captured document vectors are
[`lesson_similarity.json`](../../../packages/try-professional/tests/data/lesson_similarity.json).

| Set | Paraphrase cosine scores | Distinct lesson cosine scores |
| --- | --- | --- |
| Calibration | 0.9572–0.9727 | 0.7861–0.9382 |
| Validation | 0.9524–0.9760 | 0.6161–0.8102 |

A document-to-document cosine threshold of **0.95** separates these examples.
In particular, changing “before committing” to “after committing” scores 0.9382
and stays separate. Evidence is limited to this small English regression set.
Broader paraphrases, other languages, and other embedding
models have not been calibrated for lesson merging. Unmeasured models merge
exact text only; the recall score floor is never used as a merge threshold.

Tests replay the captured vectors without downloading a model. The model tests
also reproduce the scores and exercise the real Mnemosyne storage, old entries,
concurrent writes, evidence, counts, memory banks, and briefs. They use the same
local model as normal memory and skill recall.

To repeat the measurement, set `TRYPRO_MODEL_CACHE` to the local model cache and
run `python docs/research/lesson-similarity/measure.py`. To exercise the feature,
run `python -m pytest packages/try-professional/tests/test_lesson_merging.py -q`.

Existing near-duplicate entries are not rewritten in bulk. A new learning event
merges into its closest qualifying canonical entry within the same bank or
proposal tier and owner. The original evidence on old entries is retained on
their first merge. A repeated run projection does not count as another learning
event. Evidence and counts survive reopening storage.
