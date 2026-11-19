"""Generate the SQL NULL item pool: real queries over small tables, gold = executed result.

Five query families, each with matched splits that carry over the design of the K3 paper:
  S0  no NULL takes part                                  -> every semantics agrees
  S1  a NULL makes the outcome UNKNOWN in SQL             -> SQL differs from at least one 2VL collapse
  S2  a NULL takes part but is absorbed (outcome definite)-> SQL agrees with the collapses; "NULL poisons
                                                             everything" (weak Kleene) differs
Families
  where  SELECT id FROM t WHERE <chain predicate of depth d over nullable columns>   (depth axis)
  notin  SELECT id FROM r WHERE x NOT IN (SELECT y FROM s [WHERE ...])
  join   SELECT r.id, s.sid FROM r JOIN s ON r.k = s.k [OR r.g = s.g]
  agg    SELECT grp, SUM(v) | MAX(v) FROM t GROUP BY grp
  check  CREATE TABLE t (..., CHECK (<predicate>)); INSERT ...; which rows are stored?
           (CHECK rejects only FALSE, so S1 rows are accepted and S2 = NULL absorbed to FALSE -> rejected)

For every item we compute the answer under five semantics with our own evaluator:
  sql (strong Kleene + the context rule), wk (weak Kleene: any NULL operand -> UNKNOWN),
  nf (NULL comparisons are FALSE), nt (NULL comparisons are TRUE), eq (NULL = NULL is TRUE, other NULL
  comparisons FALSE; only differs in join/notin).
The sql answer must equal the result of executing the item in SQLite, DuckDB and PostgreSQL (exec_gold.py).

  python gen_sql_items.py <out.jsonl> [--per-cell N] [--seed S]
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import random
from pathlib import Path

F, U, T = 0, 1, 2
NAME = {F: "F", U: "U", T: "T"}


# ------------------------------------------------------------------ three-valued core
def k_not(a):
    return 2 - a


def k_and(a, b, weak=False):
    if weak and U in (a, b):
        return U
    return min(a, b)


def k_or(a, b, weak=False):
    if weak and U in (a, b):
        return U
    return max(a, b)


def cmp(op, x, k, mode):
    """Value of `x op k` (k is a non-null int). mode: sql/wk -> U on NULL; nf -> F; nt -> T; eq -> F."""
    if x is None:
        return {"sql": U, "wk": U, "nf": F, "nt": T, "eq": F}[mode]
    r = {"<": x < k, ">": x > k, "=": x == k, "<>": x != k, "<=": x <= k, ">=": x >= k}[op]
    return T if r else F


def eq_vals(x, y, mode):
    """Value of `x = y` where either may be NULL."""
    if x is None or y is None:
        if mode in ("sql", "wk"):
            return U
        if mode == "nf":
            return F
        if mode == "nt":
            return T
        if mode == "eq":
            return T if (x is None and y is None) else F
    return T if x == y else F


# ------------------------------------------------------------------ chain predicates (where / check)
COLS = ("a", "b", "c")
OPS = ("<", ">", "=", "<>", "<=", ">=")


def atom_val(atom, row, mode):
    kind = atom[0]
    if kind == "isnull":
        return T if row[atom[1]] is None else F
    if kind == "notnull":
        return F if row[atom[1]] is None else T
    _, col, op, k = atom
    return cmp(op, row[col], k, mode)


def atom_sql(atom):
    if atom[0] == "isnull":
        return f"{atom[1]} IS NULL"
    if atom[0] == "notnull":
        return f"{atom[1]} IS NOT NULL"
    return f"{atom[1]} {atom[2]} {atom[3]}"


def rand_atom(rng, cols):
    if rng.random() < 0.08:
        return ("isnull" if rng.random() < 0.5 else "notnull", rng.choice(cols))
    return ("cmp", rng.choice(cols), rng.choice(OPS), rng.randint(1, 8))


def make_chain(rng, depth, cols):
    """Linear chain: bottom atom, then `depth` operators. Each op: ("not",) or (op, atom, side)."""
    bottom = rand_atom(rng, cols)
    ops = []
    for _ in range(depth):
        if rng.random() < 0.2 and (not ops or ops[-1][0] != "not"):
            ops.append(("not",))
        else:
            ops.append((rng.choice(("and", "or")), rand_atom(rng, cols), rng.random() < 0.5))
    if all(o[0] == "not" for o in ops):
        return make_chain(rng, depth, cols)
    return bottom, ops


def chain_sql(chain):
    bottom, ops = chain
    s = atom_sql(bottom)
    for o in ops:
        if o[0] == "not":
            s = f"NOT ({s})"
        else:
            leaf = atom_sql(o[1])
            s = f"({s}) {o[0].upper()} {leaf}" if o[2] else f"{leaf} {o[0].upper()} ({s})"
    return s


def chain_eval(chain, row, mode):
    bottom, ops = chain
    weak = mode == "wk"
    v = atom_val(bottom, row, mode)
    for o in ops:
        if o[0] == "not":
            v = k_not(v)
        else:
            leaf = atom_val(o[1], row, mode)
            v = k_and(v, leaf, weak) if o[0] == "and" else k_or(v, leaf, weak)
    return v


def chain_no_shortcircuit(chain, row):
    """Strong Kleene: no leaf ever fixes the chain value (image of the bottom keeps >= 2 values)."""
    _, ops = chain
    img = {F, U, T}
    for o in ops:
        if o[0] == "not":
            img = {k_not(v) for v in img}
        else:
            leaf = atom_val(o[1], row, "sql")
            img = {k_and(v, leaf) if o[0] == "and" else k_or(v, leaf) for v in img}
        if len(img) < 2:
            return False
    return True


def chain_atoms(chain):
    return [chain[0]] + [o[1] for o in chain[1] if o[0] != "not"]


def has_u_atom(chain, row):
    return any(atom_val(a, row, "sql") == U for a in chain_atoms(chain))


def rand_row(rng, cols, p_null):
    return {c: (None if rng.random() < p_null else rng.randint(0, 9)) for c in cols}


def row_kind(chain, row, ctx):
    """Classify one row. ctx 'where' keeps TRUE rows; ctx 'check' rejects FALSE rows."""
    sk = chain_eval(chain, row, "sql")
    if not has_u_atom(chain, row):
        return "C", sk
    nf, nt = chain_eval(chain, row, "nf"), chain_eval(chain, row, "nt")
    if sk == U:
        return ("U" if nf != nt else "Uagree"), sk
    if ctx == "where":
        return ("A" if sk == T else "Afalse"), sk
    return ("A" if sk == F else "Atrue"), sk


def make_chain_for_rows(rng, depth, cols, rows, tries=40):
    """Bottom-up chain built for a fixed set of rows: every leaf keeps each row's image of the bottom atom at
    >= 2 values (no leaf short-circuits the chain on any row)."""
    bottom = ("cmp", rng.choice(cols), rng.choice(OPS), rng.randint(1, 8))
    imgs = [{F, U, T} for _ in rows]
    ops = []
    for _ in range(depth):
        if rng.random() < 0.2 and (not ops or ops[-1][0] != "not"):
            ops.append(("not",))
            imgs = [{k_not(v) for v in im} for im in imgs]
            continue
        placed = False
        for op in rng.sample(("and", "or"), 2):
            f = k_and if op == "and" else k_or
            for _ in range(tries):
                atom = rand_atom(rng, cols)
                new = [{f(v, atom_val(atom, r, "sql")) for v in im} for im, r in zip(imgs, rows)]
                if all(len(n) >= 2 for n in new):
                    ops.append((op, atom, rng.random() < 0.5))
                    imgs = new
                    placed = True
                    break
            if placed:
                break
        if not placed:
            return None
    if all(o[0] == "not" for o in ops):
        return None
    return bottom, ops


def keep(v, ctx):
    return v == T if ctx == "where" else v != F


def gen_chain_item(rng, family, split, depth, n_rows=6):
    n_spec = max(2, n_rows // 4)
    ctx = "where" if family == "where" else "check"
    cols = COLS if family == "where" else ("a", "b")
    want_special = {"S0": 0, "S1": n_spec, "S2": n_spec}[split]
    special_kind = {"S1": "U", "S2": "A"}.get(split)
    for _ in range(4000):
        # rows first, then a chain whose every leaf leaves each row's bottom image with >= 2 values
        rows = [rand_row(rng, cols, 0.0) for _ in range(n_rows - want_special)]
        for _ in range(want_special):
            r = rand_row(rng, cols, 0.0)
            for c in rng.sample(cols, rng.randint(1, len(cols) - 1)):
                r[c] = None
            rows.append(r)
        chain = make_chain_for_rows(rng, depth, cols, rows)
        if chain is None:
            continue
        kinds = [row_kind(chain, r, ctx)[0] for r in rows]
        if any(k != "C" for k in kinds[:n_rows - want_special]) or any(k != special_kind for k in kinds[n_rows - want_special:]):
            continue
        specials = want_special
        # classical rows must not all agree: at least one kept and one not kept among them
        cls = [keep(chain_eval(chain, r, "sql"), ctx) for r in rows if row_kind(chain, r, ctx)[0] == "C"]
        if cls and (all(cls) or not any(cls)):
            continue
        rng.shuffle(rows)
        for i, r in enumerate(rows, 1):
            r["id"] = i
        pred = chain_sql(chain)
        answers = {m: [r["id"] for r in rows if keep(chain_eval(chain, r, m), ctx)] for m in ("sql", "wk", "nf", "nt")}
        answers["eq"] = answers["nf"]
        colsdef = ", ".join(f"{c} INTEGER" for c in cols)
        if family == "where":
            ddl = [f"CREATE TABLE t (id INTEGER PRIMARY KEY, {colsdef});"]
            ins = [f"INSERT INTO t VALUES ({r['id']}, " + ", ".join(lit(r[c]) for c in cols) + ");" for r in rows]
            query = f"SELECT id FROM t WHERE {pred} ORDER BY id;"
            return dict(ddl=ddl, inserts=ins, query=query, answer_kind="ids", answers=answers,
                        n_special=specials, kinds=[row_kind(chain, r, ctx)[0] for r in rows])
        ddl = [f"CREATE TABLE t (id INTEGER PRIMARY KEY, {colsdef}, CHECK ({pred}));"]
        ins = [f"INSERT INTO t VALUES ({r['id']}, " + ", ".join(lit(r[c]) for c in cols) + ");" for r in rows]
        return dict(ddl=ddl, inserts=ins, query="SELECT id FROM t ORDER BY id;", answer_kind="ids",
                    answers=answers, n_special=specials, kinds=[row_kind(chain, r, ctx)[0] for r in rows],
                    inserts_may_fail=True)
    return None


def lit(v):
    return "NULL" if v is None else str(v)


# ------------------------------------------------------------------ NOT IN
def gen_notin_item(rng, split):
    """S0: s has no NULL. S1: a NULL reaches the NOT IN list -> no row qualifies.
    S2: s holds a NULL that the subquery's own WHERE removes (NULL > k is UNKNOWN), so NOT IN is classical."""
    for _ in range(1000):
        n_r, n_s = 5, rng.randint(3, 4)
        r = [{"id": i + 1, "x": rng.randint(0, 6)} for i in range(n_r)]
        s = [rng.randint(0, 6) for _ in range(n_s)]
        k = rng.randint(0, 2)
        sub_where = None
        if split == "S0":
            pass
        elif split == "S1":
            s[rng.randrange(n_s)] = None
            if rng.random() < 0.5:
                sub_where = ("ge_or_null", k)      # WHERE y >= k OR y IS NULL  -> NULL survives
        else:
            s[rng.randrange(n_s)] = None
            sub_where = ("gt", k)                  # WHERE y > k                -> NULL filtered out
        if sub_where is None and split != "S1" and rng.random() < 0.5:
            sub_where = ("gt", k)

        def sub_vals(mode):
            out = []
            for y in s:
                if sub_where is None:
                    ok = T
                elif sub_where[0] == "gt":
                    ok = cmp(">", y, sub_where[1], mode if mode != "eq" else "sql")
                else:
                    ok = k_or(cmp(">=", y, sub_where[1], mode if mode != "eq" else "sql"), T if y is None else F,
                              mode == "wk")
                if ok == T:
                    out.append(y)
            return out

        def notin(x, mode):
            vals = sub_vals(mode)
            v = F  # x IN vals, computed as OR of x = y
            for y in vals:
                v = k_or(v, eq_vals(x, y, mode), mode == "wk")
            return k_not(v)

        answers = {m: [row["id"] for row in r if notin(row["x"], m) == T] for m in ("sql", "wk", "nf", "nt", "eq")}
        # WK on S2: model that treats the stored NULL as poisoning NOT IN -> empty answer
        if split == "S2":
            answers["wk"] = []
        sql = answers["sql"]
        if split == "S1" and sql != []:
            continue
        if split in ("S0", "S2") and (not sql or len(sql) == n_r):
            continue
        if split == "S1" and answers["nf"] == []:
            continue
        where = ""
        if sub_where:
            where = f" WHERE y > {sub_where[1]}" if sub_where[0] == "gt" else f" WHERE y >= {sub_where[1]} OR y IS NULL"
        return dict(ddl=["CREATE TABLE r (id INTEGER PRIMARY KEY, x INTEGER);", "CREATE TABLE s (y INTEGER);"],
                    inserts=[f"INSERT INTO r VALUES ({row['id']}, {row['x']});" for row in r]
                    + [f"INSERT INTO s VALUES ({lit(y)});" for y in s],
                    query=f"SELECT id FROM r WHERE x NOT IN (SELECT y FROM s{where}) ORDER BY id;",
                    answer_kind="ids", answers=answers)
    return None


# ------------------------------------------------------------------ JOIN
def gen_join_item(rng, split):
    """S0: no NULL keys. S1: NULL keys on both sides, plain equi-join (NULL = NULL is UNKNOWN).
    S2: NULL keys, join condition `r.k = s.k OR r.g = s.g` where g matches for a NULL-key pair (absorbed)."""
    for _ in range(2000):
        r = [{"id": i + 1, "k": rng.randint(1, 4), "g": rng.randint(1, 3)} for i in range(3)]
        s = [{"sid": 10 + i + 1, "k": rng.randint(1, 4), "g": rng.randint(1, 3)} for i in range(3)]
        cond_or = split == "S2" or (split == "S0" and rng.random() < 0.5)
        if split in ("S1", "S2"):
            r[rng.randrange(3)]["k"] = None
            s[rng.randrange(3)]["k"] = None

        def on(a, b, mode):
            v = eq_vals(a["k"], b["k"], mode)
            if cond_or:
                v = k_or(v, eq_vals(a["g"], b["g"], mode), mode == "wk")
            return v

        answers = {m: [[a["id"], b["sid"]] for a in r for b in s if on(a, b, m) == T] for m in ("sql", "wk", "nf", "nt", "eq")}
        if not answers["sql"]:
            continue
        null_pairs = [(a, b) for a in r for b in s if a["k"] is None or b["k"] is None]
        if split == "S1" and answers["eq"] == answers["sql"]:
            continue
        if split == "S2":
            if not any(on(a, b, "sql") == T for a, b in null_pairs):   # some NULL-key pair joins via g
                continue
        cond = "r.k = s.k" + (" OR r.g = s.g" if cond_or else "")
        cols = "k INTEGER, g INTEGER"
        return dict(ddl=[f"CREATE TABLE r (id INTEGER PRIMARY KEY, {cols});", f"CREATE TABLE s (sid INTEGER PRIMARY KEY, {cols});"],
                    inserts=[f"INSERT INTO r VALUES ({a['id']}, {lit(a['k'])}, {a['g']});" for a in r]
                    + [f"INSERT INTO s VALUES ({b['sid']}, {lit(b['k'])}, {b['g']});" for b in s],
                    query=f"SELECT r.id, s.sid FROM r JOIN s ON {cond} ORDER BY r.id, s.sid;",
                    answer_kind="pairs", answers=answers)
    return None


# ------------------------------------------------------------------ aggregates
def gen_agg_item(rng, split):
    """GROUP BY grp with SUM or MAX over a nullable v.
    S1: one group has only NULL v -> its aggregate is NULL (unknown gold; NULL-as-zero says 0).
    S2: groups contain NULLs among numbers -> NULLs are ignored (absorbed; "NULL poisons" says NULL)."""
    for _ in range(1000):
        fn = rng.choice(("SUM", "MAX"))
        groups = [1, 2, 3]
        rows = []
        for g in groups:
            for _ in range(rng.randint(2, 3)):
                rows.append({"grp": g, "v": rng.randint(1, 9)})
        if split == "S1":
            g0 = rng.choice(groups)
            for x in rows:
                if x["grp"] == g0:
                    x["v"] = None
        elif split == "S2":
            for g in rng.sample(groups, 2):
                idx = [i for i, x in enumerate(rows) if x["grp"] == g]
                rows[rng.choice(idx)]["v"] = None
        rng.shuffle(rows)
        for i, x in enumerate(rows, 1):
            x["id"] = i

        def agg(vals, mode):
            if mode == "wk" and any(v is None for v in vals):
                return None
            if mode in ("nf", "nt", "eq"):            # NULL treated as 0
                vals = [0 if v is None else v for v in vals]
            vals = [v for v in vals if v is not None]
            if not vals:
                return None
            return sum(vals) if fn == "SUM" else max(vals)

        answers = {m: [[g, agg([x["v"] for x in rows if x["grp"] == g], m)] for g in groups] for m in ("sql", "wk", "nf", "nt", "eq")}
        if split != "S0" and answers["sql"] == answers["nf"] and answers["sql"] == answers["wk"]:
            continue
        return dict(ddl=["CREATE TABLE t (id INTEGER PRIMARY KEY, grp INTEGER, v INTEGER);"],
                    inserts=[f"INSERT INTO t VALUES ({x['id']}, {x['grp']}, {lit(x['v'])});" for x in rows],
                    query=f"SELECT grp, {fn}(v) FROM t GROUP BY grp ORDER BY grp;",
                    answer_kind="pairs", answers=answers)
    return None


# ------------------------------------------------------------------ pool
CELLS_P2 = ([("where", s, d, 6) for s in ("S0", "S1", "S2") for d in (16, 32, 64)]
            + [("where", s, 8, r) for s in ("S0", "S1", "S2") for r in (12, 24)]
            + [("check", s, d, 6) for s in ("S1", "S2") for d in (16, 32)])
# formal pool (chosen from pilot 2, see PILOT_NOTE.md)
CELLS_F = ([("where", s, d, r) for s in ("S0", "S1", "S2") for d, r in ((8, 12), (32, 6), (64, 6))]
           + [("check", s, 32, 6) for s in ("S1", "S2")]
           + [(f, s, 0, 6) for f in ("notin", "join", "agg") for s in ("S1", "S2")])
CELLS = ([("where", s, d) for s in ("S0", "S1", "S2") for d in (2, 4, 8)]
         + [("check", s, d) for s in ("S0", "S1", "S2") for d in (2, 4)]
         + [(f, s, 0) for f in ("notin", "join", "agg") for s in ("S0", "S1", "S2")])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--per-cell", type=int, default=120)
    ap.add_argument("--seed", type=int, default=20261117)
    ap.add_argument("--cells", default="v0")
    a = ap.parse_args()
    rng = random.Random(a.seed)
    out, n_fail = [], 0
    cells = ([(f, s, d, 6) for f, s, d in CELLS] if a.cells == "v0"
             else CELLS_F if a.cells == "f" else CELLS_P2)
    for fam, split, depth, n_rows in cells:
        seen = set()
        made = 0
        while made < a.per_cell:
            if fam in ("where", "check"):
                it = gen_chain_item(rng, fam, split, depth, n_rows)
            elif fam == "notin":
                it = gen_notin_item(rng, split)
            elif fam == "join":
                it = gen_join_item(rng, split)
            else:
                it = gen_agg_item(rng, split)
            if it is None:
                n_fail += 1
                if n_fail > 200:
                    raise SystemExit(f"cannot fill {fam} {split} {depth}")
                continue
            key = (tuple(it["inserts"]), it["query"])
            if key in seen:
                continue
            seen.add(key)
            rows_tag = "" if a.cells == "v0" else f"-r{n_rows:02d}"
            it.update(id=f"{fam}-{split}-d{depth:02d}{rows_tag}-{made:03d}", family=fam, split=split, depth=depth, rows=n_rows)
            out.append(it)
            made += 1
        print(f"{fam:6s} {split} d{depth:<2d} r{n_rows:<2d} {made}", flush=True)
    p = Path(a.out)
    with p.open("w", encoding="utf-8", newline="\n") as f:
        for it in out:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    p.with_suffix(".sha256").write_text(f"{h}  {p.name}\n")
    print(len(out), "items", h)


if __name__ == "__main__":
    main()
