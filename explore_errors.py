"""Exploratory (not preregistered) error analysis on formal_v1 + formal_a1_fable52, plain prompt, testable cells.
  python explore_errors.py   -> runs/explore_errors.json and a printout
1. absorbing-row error rate by the number of NULL comparisons the row meets in the condition
2. output length of wrong vs right answers on S2 items
3. concentration: do absorbing-row errors of different models fall on the same items?
"""
import collections
import json
import re
from pathlib import Path

from analyze_formal import row_records, testable, MISSING

HERE = Path(__file__).resolve().parent
RUNS = [HERE / "runs" / "formal_v1", HERE / "runs" / "formal_a1_fable52"]
MODELS = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-2"]
ATOM = re.compile(r"\b([a-c])\s*(<=|>=|<>|<|>|=)\s*\d+")


def load(p):
    return [json.loads(l) for l in p.open(encoding="utf-8")]


def row_values(it):
    cols = re.search(r"CREATE TABLE t \(id INTEGER PRIMARY KEY, (.*?)(?:, CHECK|\);)", it["ddl"][0]).group(1)
    names = [c.split()[0] for c in cols.split(", ")]
    out = {}
    for s in it["inserts"]:
        vals = [v.strip() for v in re.search(r"VALUES \((.*)\);", s).group(1).split(",")]
        out[int(vals[0])] = dict(zip(names, [None if v == "NULL" else int(v) for v in vals[1:]]))
    return out


def null_cmps(it, rid):
    pred = it["query"] if it["family"] == "where" else it["ddl"][0]
    row = row_values(it)[rid]
    return sum(row.get(c) is None for c, _ in ATOM.findall(pred))


def main():
    pool = {it["id"]: it for it in load(HERE / "pool/sql_pool_f_gold.jsonl")}
    scored = [r for run in RUNS for r in load(run / "scored.jsonl")]
    scored = [r for r in scored if r["model"] in MODELS and r["cond"] == "plain" and r["family"] in ("where", "check")]
    miss = testable(scored)
    ok = lambda x: miss.get((x["model"], x["arm"], x["family"], x["split"], x["cell"]), 0) <= 0.20
    rows = [x for x in row_records(scored, pool) if ok(x)]
    # attach row id: row_records emits rows 1..n in order per answer
    rid = collections.defaultdict(int)
    for x in rows:
        k = (x["model"], x["arm"], x["item"])
        rid[k] += 1
        x["rid"] = rid[k]
    out = {}

    # 1. NULL comparisons met by absorbing rows (and unknown rows for contrast)
    for fam in ("where", "check"):
        for cat, split in (("absorbing", "S2"), ("unknown", "S1")):
            b = collections.defaultdict(lambda: [0, 0])
            for x in rows:
                if x["family"] == fam and x["split"] == split and x["cat"] == cat:
                    n = null_cmps(pool[x["item"]], x["rid"])
                    key = "1" if n <= 1 else "2-3" if n <= 3 else "4-7" if n <= 7 else "8+"
                    b[key][0] += x["err"]
                    b[key][1] += 1
            out[f"nullcmp|{fam}|{cat}"] = {k: b[k] for k in ("1", "2-3", "4-7", "8+") if k in b}
        # the same, within each size (NULL comparisons grow with depth, so the pooled gradient is confounded)
        for cell in sorted({x["cell"] for x in rows if x["family"] == fam}):
            b = collections.defaultdict(lambda: [0, 0])
            for x in rows:
                if x["family"] == fam and x["cell"] == cell and x["split"] == "S2" and x["cat"] == "absorbing":
                    n = null_cmps(pool[x["item"]], x["rid"])
                    key = "1" if n <= 1 else "2-3" if n <= 3 else "4-7" if n <= 7 else "8+"
                    b[key][0] += x["err"]
                    b[key][1] += 1
            out[f"nullcmp_by_size|{fam}|{cell}|absorbing"] = {k: b[k] for k in ("1", "2-3", "4-7", "8+") if k in b}

    # 2. output length of wrong vs right answers, S2 WHERE/CHECK items, per model (median)
    for m in MODELS:
        for fam in ("where", "check"):
            rs = [r for r in scored if r["model"] == m and r["family"] == fam and r["split"] == "S2"
                  and r["outcome"] not in MISSING and r.get("output_tokens")]
            w = sorted(r["output_tokens"] for r in rs if r["outcome"] == "wrong")
            c = sorted(r["output_tokens"] for r in rs if r["outcome"] == "correct")
            out[f"tokens|{m}|{fam}"] = {"wrong_n": len(w), "wrong_median": w[len(w) // 2] if w else None,
                                        "right_n": len(c), "right_median": c[len(c) // 2] if c else None}

    # 3. concentration of absorbing-row errors over items, and overlap across models
    for fam in ("where", "check"):
        per = collections.defaultdict(set)
        nerr = collections.Counter()
        items = set()
        for x in rows:
            if x["family"] == fam and x["split"] == "S2" and x["cat"] == "absorbing":
                items.add(x["item"])
                if x["err"]:
                    per[x["item"]].add(x["model"])
                    nerr[x["item"]] += 1
        multi = sum(len(v) >= 2 for v in per.values())
        out[f"concentration|{fam}"] = {"S2_items": len(items), "items_with_abs_error": len(per),
                                       "items_wrong_for_2plus_models": multi, "abs_errors": sum(nerr.values()),
                                       "top5_items_share": round(sum(v for _, v in nerr.most_common(5)) / max(1, sum(nerr.values())), 2)}
    (HERE / "runs" / "explore_errors.json").write_text(json.dumps(out, indent=1))
    for k, v in out.items():
        print(k, v)


if __name__ == "__main__":
    main()
