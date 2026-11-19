"""Exploratory pilot summary: row errors by (family, split, cell, category), plus outcomes and output tokens.
  python pilot_summary.py <run>
"""
import collections
import json
import statistics
import sys
from pathlib import Path

from analyze_formal import row_records

HERE = Path(__file__).resolve().parent
run = HERE / "runs" / sys.argv[1]
spec = json.loads((run / "spec.json").read_text())
pool = {json.loads(l)["id"]: json.loads(l) for l in (HERE / spec["pool"]).open(encoding="utf-8")}
scored = [json.loads(l) for l in (run / "scored.jsonl").open(encoding="utf-8")]
print("outcomes:", collections.Counter(r["outcome"] for r in scored))
c = collections.defaultdict(lambda: [0, 0, 0])
for x in row_records(scored, pool):
    k = (x["model"], x["family"], x["split"], x["cell"], x["cat"])
    c[k][0] += x["err"]
    c[k][1] += 1
    c[k][2] += x["err"] and x["as_wk"]
for k in sorted(c):
    e, n, w = c[k]
    print(" ".join(map(str, k)), f"err {e}/{n}", f"(wk-direction {w})")
items = collections.defaultdict(list)
for r in scored:
    items[(r["model"], r["family"], r["split"], f"d{r['depth']}r{r['rows']}")].append(r)
for k in sorted(items):
    rs = items[k]
    toks = [r.get("output_tokens") or r.get("usage", {}).get("output_tokens") for r in rs]
    toks = [t for t in toks if t]
    print(" ".join(k), "items right", sum(r["outcome"] == "correct" for r in rs), "/", len(rs),
          "median out tok", statistics.median(toks) if toks else "?")
