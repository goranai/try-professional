# Embedding models

Embeddings run on your machine, and memory text never leaves it. Two models are supported.

| | `paraphrase-multilingual-MiniLM-L12-v2` (default) | `intfloat/multilingual-e5-large` |
|---|---|---|
| Recall@5, 100 held-out queries | 93/100 | 98/100 |
| Top-1 | 71/100 | 87/100 |
| Download | 0.22 GB | 2.24 GB |
| Process memory added (one measurement) | 0.6 GB | 1.58 GB |
| Prefixes | None | `query: ` and `passage: ` |
| Memory score floor | 0.28 | None |

These come from hand-written queries over a 40-skill catalog, with no floor applied. See [`research/phase3-retrieval/`](research/phase3-retrieval/). They compare the two models on this set, and aren't a claim about general accuracy.

## Switching to e5-large

Set it in `trypro.yaml`:

```yaml
memory:
  embedding_model: intfloat/multilingual-e5-large
```

What this does and doesn't change:

- **Prefixes are handled for you.** The e5 models were trained with `query: ` and `passage: ` in front of the text. Try Professional adds them to the skill catalog, and sets `MNEMOSYNE_EMBEDDING_QUERY_PREFIX` and `MNEMOSYNE_EMBEDDING_DOC_PREFIX` for memory. Without them, retrieval quality drops.
- **There is no memory score floor.** A floor measured on one model doesn't transfer to another, and the method used for MiniLM cut e5-large's recall to 71/100. So nothing filters weak memory matches on this model yet, and every recall returns its top results.
- **Existing memory can't be reused.** Vectors from different models can't be compared, so memory and the skill catalog refuse to open with a different model. Start with a fresh `.trypro/memory`, and rebuild the catalog. Moving existing memories to a new model isn't built yet.

The combination of e5-large with the real Mnemosyne backend hasn't been run end to end. Prefix handling is covered by tests, and retrieval was measured directly with the model.
