# Unknown Is Easy, Absorbed Is Hard — artifact

Benchmark, raw model responses and analysis for the PVLDB Experiment, Analysis & Benchmark paper
*"Unknown Is Easy, Absorbed Is Hard: How Language Models Read SQL Queries over NULLs"*.

The benchmark asks a language model what a SQL script returns when some values are `NULL`, and scores the answer row
by row. Every row is labelled by which candidate semantics agree on it: **unknown** rows (the condition is `UNKNOWN`
in SQL), **absorbing** rows (SQL absorbs the `NULL`, e.g. `NULL OR TRUE`, while "a NULL makes the condition fail"
would flip the row) and **classical** rows. Gold answers are obtained by executing every item on PostgreSQL, SQLite
and DuckDB, which must agree with a reference evaluator.

## Layout

| Path | Content |
|---|---|
| `gen_sql_items.py` | result-prediction item generator (families `where`, `check`, `notin`, `join`, `agg`; splits S0/S1/S2) and the five reference semantics (`sql`, `wk`, `nf`, `nt`, `eq`) |
| `exec_gold.py` | runs every item on SQLite, DuckDB and PostgreSQL; keeps an item only if all three agree with the reference `sql` answer |
| `gen_write_items*.py`, `exec_write_gold.py`, `score_write.py` | query-writing task: templates, test suites (NULL suite and NULL-free twins), execution scoring |
| `sql_eval.py` | builds Message Batches requests from a spec, submits, fetches, parses and scores answers; holds the prompts (`SYSTEM`, `user_prompt`) and the prices |
| `analyze_formal.py`, `analyze_a1.py` | the preregistered analyses (H1–H5; H1f–H3f, H5f) |
| `explore_errors.py`, `pilot_summary.py` | exploratory analyses reported as such in the paper |
| `gen_trap_items.py` | the exploratory short "rule-of-thumb" families (LEFT JOIN, IN / NOT IN with a NULL) |
| `paper/paper_numbers.py` | recomputes every number and both data figures of the paper (`paper/numbers.json`, `paper/figs/`) |
| `paper/schematic_pipeline/figure/figure.js` | source of the pipeline schematic (Figure 2) |
| `pool/` | all item pools with engine gold (`*_gold.jsonl`), with SHA-256 files |
| `specs/` | run specifications (arms, cells, models, effort) |
| `runs/<run>/` | `requests.jsonl` (exact requests), `results.jsonl` (raw responses with usage), `scored*.jsonl`, `analysis.json` |
| `PREREG_VLDB.md`, `PREREG_VLDB_A1.md` | preregistration and amendment A1 (Chinese originals, binding), with `.sha256` locks and English translations (`*.en.md`) |
| `locked_v1/`, `locked_a1/` | the locked versions of files that were edited after their lock (see below) |
| `spend_ledger_vldb.json` | batch ids, request counts and cost of every run |

Runs: `formal_v1` (2,504 requests; Haiku 5.5, Sonnet 5.5, Opus 5.5, Fable 5.1), `formal_a1_fable52` (488 requests;
Fable 5.2), and the exploratory pilots `pilot_sql_v1`, `pilot_sql_p2`, `pilot_write_v1..v3` (before the
preregistration), `pilot_a2_p3`, `pilot_a2_low`, `pilot_trap1` (after it). Model ids, effort, `max_tokens` and dates
are in each run's `spec.json` and `requests.jsonl`.

## Requirements

Python 3.11+ with `duckdb` 2.0.0, `psycopg` 3.x, `pgserver` (bundles PostgreSQL 16.2), `anthropic` (only to call the
API) and `matplotlib` (figures; the figures set text with LaTeX and need a TeX installation with `libertine` and
`newtxmath`). SQLite is Python's built-in module (3.49.1 in our runs). See `requirements.txt`.

`exec_gold.py`, `exec_write_gold.py` and `score_write.py` contain the PostgreSQL data directory used on our machine
(`PGDATA = r"D:\k3pg\data"`). Change that line to any ASCII-only path (PostgreSQL's binaries do not run from
non-ASCII paths). This changes the file's hash; `verify_hashes.py` checks the files as shipped.

## Reproduce without an API key (minutes, CPU only)

```bash
python verify_hashes.py                          # every preregistration lock still matches
python analyze_formal.py formal_v1               # H1-H5 and secondary analyses -> runs/formal_v1/analysis.json
python analyze_a1.py formal_a1_fable52           # H1f-H3f, H5f
python explore_errors.py                         # exploratory error analysis -> runs/explore_errors.json
python paper/paper_numbers.py                    # every number in the paper + figures
python exec_gold.py pool/sql_pool_f.jsonl /tmp/f_gold.jsonl   # re-derive the formal gold on three engines
python sql_eval.py score formal_v1               # re-parse and re-score the raw responses
```

## Rerun the models (needs `ANTHROPIC_API_KEY`; costs money)

```bash
python sql_eval.py plan   specs/formal_v1.json   # request count and projected cost, no API call
python sql_eval.py submit specs/formal_v1.json   # refuses if the projected spend would pass CAP_USD
python sql_eval.py fetch  formal_v1
python sql_eval.py score  formal_v1
python score_write.py     formal_v1
```

Our runs cost US$23.54 (formal_v1), US$13.17 (A1) and US$47.96 in total including all pilots, at batch prices.
Model outputs are not deterministic; a rerun gives a new sample, not the same responses.

## Preregistration and locks

`PREREG_VLDB.sha256` (locked 2026-11-17T12:42:09Z, before formal_v1) and `PREREG_VLDB_A1.sha256` (locked
2026-11-18T02:08:46Z, before the amendment) record the hashes of the preregistration text, specs, pools and code.
Two files were edited after a lock, and the locked versions are kept:

- `sql_eval.py`: after the v1 lock, Fable 5.2's price was added for A1 (documented in A1; v1 version in
  `locked_v1/`); after the A1 lock, only the line `CAP_USD = ...` (the spending cap) changed for the later
  exploratory pilots (A1 version in `locked_a1/`).
- `gen_sql_items.py`: after the v1 lock, the cell sets `p3`/`a2` and the generator `gen_chain_item_steer` were added
  for the exploratory deep-chain pilot; nothing existing was changed (v1 version in `locked_v1/`).

`verify_hashes.py` checks each recorded hash against the shipped file or, for these two, against its locked copy.

Before release, one line was edited in each of three locked files to remove internal remarks: line 8 of
`PREREG_VLDB_A1.md` (a sign-off remark, replaced by the date of the decision) and line 30 of `locked_v1/sql_eval.py`
and `locked_a1/sql_eval.py` (a comment on the funding of the spending cap; the value is unchanged). No design,
code path or analysis is affected. `REDACTIONS.json` lists the locked and the released hash of each file;
`verify_hashes.py` checks the released hash and reports the edit. The `.sha256` lock files are unchanged and refer to
the unedited originals, which we will provide on request.

## License

Code: MIT. Item pools, prompts and our analysis outputs: CC BY 4.0. Model responses in `runs/` were produced with the
Anthropic API and are shared for research reproducibility.
