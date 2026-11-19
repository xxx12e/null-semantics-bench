"""Task B gold: run gold and naive queries on candidate instances in PostgreSQL, keep 5 instances per item that
meet the template's condition, store gold results. Also provides run_suite() for scoring model-written SQL.
  D:/k3pg/venv/Scripts/python exec_write_gold.py <items_write_raw.jsonl> <items_write.jsonl>
"""
import json
import sys
from decimal import Decimal
from pathlib import Path

import pgserver
import psycopg

from gen_write_items import SCHEMA

PGDATA = r"D:\k3pg\data"
N_KEEP = 5


def connect():
    srv = pgserver.get_server(PGDATA, cleanup_mode=None)
    return psycopg.connect(srv.get_uri(), autocommit=True)


def canon(rows, ordered):
    def v(x):
        if isinstance(x, Decimal) or isinstance(x, float):
            return round(float(x), 4)
        return x
    rows = [tuple(v(x) for x in r) for r in rows]
    return rows if ordered else sorted(rows, key=lambda r: tuple((x is None, x if x is not None else 0) for x in r))


_READY = set()


def run_queries(conn, inserts, sqls, ordered):
    """Load one instance inside a transaction, run every query (each under a savepoint), roll back.
    Returns canonical rows or ('ERROR', message) per query."""
    if id(conn) not in _READY:            # schema created once per connection
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS w CASCADE; CREATE SCHEMA w; SET search_path TO w")
            cur.execute(SCHEMA)
        _READY.add(id(conn))
    out = []
    with conn.cursor() as cur:
        cur.execute("BEGIN")
        cur.execute("\n".join(inserts))
        cur.execute("SET LOCAL statement_timeout = 5000")
        for sql in sqls:
            cur.execute("SAVEPOINT q")
            try:
                cur.execute(sql)
                out.append(canon(cur.fetchall(), ordered))
                cur.execute("RELEASE SAVEPOINT q")
            except psycopg.Error as e:
                cur.execute("ROLLBACK TO SAVEPOINT q")
                out.append(("ERROR", str(e).splitlines()[0][:200]))
        cur.execute("ROLLBACK")
    return out


def run_query(conn, inserts, sql, ordered):
    return run_queries(conn, inserts, [sql], ordered)[0]


def run_suite(conn, item, sql):
    """Per suite: fraction of instances where `sql` reproduces the gold result, plus all-correct flags."""
    out = {}
    for suite in ("null", "clean"):
        ok = [run_query(conn, inst[suite], sql, item["ordered"]) == [tuple(r) for r in inst["gold_" + suite]]
              for inst in item["suite"]]
        out[suite] = {"all": all(ok), "frac": sum(ok) / len(ok)}
    return out


def main():
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    conn = connect()
    kept_items = []
    for l in src.open(encoding="utf-8"):
        it = json.loads(l)
        keep = []
        for c in it["candidates"]:
            gn, nn = run_queries(conn, c["null"], [it["gold_sql"], it["naive_sql"]], it["ordered"])
            gc, nc = run_queries(conn, c["clean"], [it["gold_sql"], it["naive_sql"]], it["ordered"])
            assert gc == nc, (it["id"], "naive and gold must agree without NULLs")
            ok = (gn != nn) if it["kind"] == "trap" else (gn == nn)
            if ok and gn and gn[0] != "ERROR" and len(gn) > 0:
                keep.append({"seed": c["seed"], "null": c["null"], "clean": c["clean"], "gold_null": gn, "gold_clean": gc})
            if len(keep) == N_KEEP:
                break
        if len(keep) < N_KEEP:
            print("NOT ENOUGH", it["id"], len(keep))
            continue
        it.pop("candidates")
        it["suite"] = keep
        # sanity: gold scores 1.0 on both suites; naive fails the null suite for traps only
        g, n = run_suite(conn, it, it["gold_sql"]), run_suite(conn, it, it["naive_sql"])
        assert g["null"]["all"] and g["clean"]["all"]
        assert n["clean"]["all"] and (n["null"]["all"] == (it["kind"] == "control")), (it["id"], n)
        kept_items.append(it)
    # json can't hold tuples; lists are fine for comparison after reload because canon() is re-applied on load
    with dst.open("w", encoding="utf-8", newline="\n") as f:
        for it in kept_items:
            f.write(json.dumps(it, default=str) + "\n")
    print(len(kept_items), "items kept")


if __name__ == "__main__":
    main()
