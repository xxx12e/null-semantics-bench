"""Score task B (query writing) results by execution on each item's test suites (needs D:/k3pg/venv python).
  D:/k3pg/venv/Scripts/python score_write.py <run_name>
"""
import collections
import json
import re
import sys
from pathlib import Path

from exec_write_gold import connect, run_suite

HERE = Path(__file__).resolve().parent
SQL_BLOCK = re.compile(r"```sql\s*(.*?)```", re.S | re.I)


def main(name, pool="pool/write/items_write.jsonl"):
    items = {json.loads(l)["id"]: json.loads(l) for l in (HERE / pool).open(encoding="utf-8")}
    conn = connect()
    rows = []
    for l in (HERE / "runs" / name / "results.jsonl").open(encoding="utf-8"):
        r = json.loads(l)
        if r.get("task") != "write":
            continue
        it = items[r["item"]]
        out = {k: r[k] for k in ("custom_id", "model", "effort", "cond", "item", "family", "split", "output_tokens") if k in r}
        if r["type"] != "succeeded" or r.get("stop_reason") in ("refusal", "max_tokens"):
            out["outcome"] = r.get("stop_reason") or r["type"]
        else:
            m = SQL_BLOCK.findall(r.get("text") or "")
            if not m:
                out["outcome"] = "unparsed"
            else:
                sql = m[-1].strip().rstrip(";")
                out["sql"] = sql
                res = run_suite(conn, it, sql)
                out.update(null_all=res["null"]["all"], clean_all=res["clean"]["all"],
                           null_frac=res["null"]["frac"], clean_frac=res["clean"]["frac"],
                           outcome="scored")
        rows.append(out)
    with (HERE / "runs" / name / "scored_write.jsonl").open("w", encoding="utf-8") as f:
        for o in rows:
            f.write(json.dumps(o) + "\n")
    agg = collections.defaultdict(collections.Counter)
    for o in rows:
        k = (o["model"][7:], o["effort"], o["cond"], o["split"])
        agg[k]["n"] += 1
        agg[k]["null_ok"] += bool(o.get("null_all"))
        agg[k]["clean_ok"] += bool(o.get("clean_all"))
        if o["outcome"] != "scored":
            agg[k][o["outcome"]] += 1
    for k in sorted(agg):
        print(k, dict(agg[k]))


if __name__ == "__main__":
    main(*sys.argv[1:3])
