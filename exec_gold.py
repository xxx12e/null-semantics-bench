"""Execute every item in SQLite, DuckDB and PostgreSQL; check that all three agree with each other and with the
generator's own strong-Kleene answer. Writes the engine result into each item as `gold`.
Needs the venv at D:\\k3pg\\venv (pgserver, psycopg, duckdb): PostgreSQL's binaries cannot live under a non-ASCII path.
  D:/k3pg/venv/Scripts/python exec_gold.py <items.jsonl> <out.jsonl>
"""
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import duckdb
import pgserver
import psycopg

PGDATA = r"D:\k3pg\data"


def norm(rows, kind):
    if kind == "ids":
        return [r[0] for r in rows]
    return [[None if v is None else int(v) for v in r] for r in rows]


def run_sqlite(it):
    con = sqlite3.connect(":memory:")
    for s in it["ddl"]:
        con.execute(s)
    for s in it["inserts"]:
        try:
            con.execute(s)
        except sqlite3.IntegrityError:
            pass
    return norm(con.execute(it["query"]).fetchall(), it["answer_kind"])


def run_duck(it):
    con = duckdb.connect(":memory:")
    for s in it["ddl"]:
        con.execute(s)
    for s in it["inserts"]:
        try:
            con.execute(s)
        except duckdb.ConstraintException:
            pass
    return norm(con.execute(it["query"]).fetchall(), it["answer_kind"])


def run_pg(conn, it):
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS t, r, s CASCADE")
        for s in it["ddl"]:
            cur.execute(s)
        for s in it["inserts"]:
            try:
                cur.execute(s)
            except psycopg.errors.CheckViolation:
                pass
        cur.execute(it["query"])
        return norm(cur.fetchall(), it["answer_kind"])


def main():
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    srv = pgserver.get_server(PGDATA, cleanup_mode=None)
    conn = psycopg.connect(srv.get_uri(), autocommit=True)
    items = [json.loads(l) for l in src.open(encoding="utf-8")]
    bad = Counter()
    with dst.open("w", encoding="utf-8", newline="\n") as out:
        for it in items:
            res = {"sqlite": run_sqlite(it), "duckdb": run_duck(it), "postgres": run_pg(conn, it)}
            vals = list(res.values())
            agree = all(v == vals[0] for v in vals)
            if not agree:
                bad["engines_disagree"] += 1
                print("DISAGREE", it["id"], res)
                continue
            if vals[0] != it["answers"]["sql"]:
                bad["reference_mismatch"] += 1
                print("REF", it["id"], vals[0], it["answers"]["sql"])
                continue
            it["gold"] = vals[0]
            it["engines"] = {"sqlite": sqlite3.sqlite_version, "duckdb": duckdb.__version__, "postgres": "16.2"}
            out.write(json.dumps(it, ensure_ascii=False) + "\n")
    print(f"{len(items)} items; dropped {dict(bad)}")


if __name__ == "__main__":
    main()
