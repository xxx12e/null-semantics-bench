# Preregistration amendment A1: Fable 5.2 (English translation)

> **Translation note.** English translation of `PREREG_VLDB_A1.md`, made on 2026-11-18 for readers of the artifact, after the run. It is *not* covered by `PREREG_VLDB_A1.sha256`. The Chinese original is binding; its hash was locked 2026-11-18T02:08:46Z before the amendment's first request.

Written on 2026-11-17. At this point formal_v1 had been run and analysed, and Fable 5.2 had not been run on any item. The hashes of this file and of the files below are recorded in `PREREG_VLDB_A1.sha256` and locked before submission. The original preregistration `PREREG_VLDB.md` and its hashes are unchanged.

## Why
formal_v1 included Fable 5.1 only as a secondary model on a small sample. Fable 5.2 is the strongest Claude model available to us; it was added on 2026-11-17. The decision was made after the formal_v1 results had been seen, and the paper says so.

## Design
- Spec: `specs/formal_a1_fable52.json`, 488 requests.
- Items: exactly the items of the formal_v1 main models, for direct comparison. Task A pool `pool/sql_pool_f_gold.jsonl` (same hash as in the original preregistration); writing pool `pool/write_f/items_write.jsonl`.
- Arms: A (WHERE, S0/S1/S2 × d8r12, d32r6, 40 items per cell); A64 (WHERE, S0/S1/S2 × d64r6, 20 per cell); C (CHECK, S1/S2 × d32r6, 40 per cell); W (query writing, 54 items × plain/bare).
- Arm E (NOT IN, JOIN, aggregate) is not run: the three main models were 720/720 correct, so it carries little information. Arm R (rules stated) is not run: H4 failed for all three main models.
- Parameters as in formal_v1: effort medium, max_tokens 64,000, Message Batches, wrong answers not retried, no fallback models, refusals recorded as refusals.

## Code changes (relative to the version locked by the original preregistration)
- `sql_eval.py` only gains Fable 5.2's price ($8 / $40 per million tokens) and its default output-length estimate of 1,500. The original locked file is kept in `locked_v1/sql_eval.py`; its hash matches `PREREG_VLDB.sha256`.
- New analysis script `analyze_a1.py`, reusing the functions of `analyze_formal.py`. Row categories, the 20% missing-answer threshold and the bootstrap are unchanged.

## Hypotheses (Fable 5.2 only)
- **H1f:** in WHERE, the error rate on S2 absorbing rows exceeds that on S1 unknown rows (arms A and A64 pooled).
- **H2f:** in S2 WHERE items, the error rate on absorbing rows exceeds that on classical rows of the same items (paired by item).
- **H3f:** in CHECK, the error rate on S2 absorbing rows exceeds that on S1 unknown rows.

All three are one-sided item-cluster bootstrap tests, 10,000 resamples, seed 20261117, Holm-corrected together; they hold if the adjusted p < 0.05.

- **H5f:** in query writing, the share of trap items correct on every NULL instance is at least 0.90; decided on the point estimate, with the Wilson lower bound reported.

Hypotheses that fail are reported as they are. Per-cell results are secondary analyses without multiplicity correction.

## Budget
Built-in conservative estimate $15.52; from Fable 5.1's measured unit cost scaled to 5.2's prices, about $14. Spend on this benchmark before submission $27.00; hard cap $45 unchanged. Submit-guard margin 1.1: $15.52 × 1.1 + $27.00 = $44.1 < $45. If the actual cost exceeds $18, this is recorded in the results file.
