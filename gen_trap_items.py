"""A2 exploratory item families: short, realistic queries where a well-known NULL rule of thumb fails because the
NULL is absorbed. Same five semantics and the same S1/S2 row categories as gen_sql_items.py.

  lj  SELECT t.id FROM t LEFT JOIN r ON t.k = r.k WHERE <formula over t.y, r.z, r.w> ORDER BY t.id;
      Unmatched rows carry a NULL that is not in the data. Rule of thumb: "a WHERE condition on the right table
      turns the LEFT JOIN into an inner join" (= weak Kleene on those rows); it fails when an OR with a TRUE
      left-table condition (or a NOT over an AND with a FALSE one) absorbs the NULL.
  ni  SELECT id FROM t WHERE <formula over x [NOT] IN (SELECT v FROM s), y> ORDER BY id;   s holds a NULL.
      Rule of thumb: "if the subquery returns a NULL, [NOT] IN is UNKNOWN" (= weak Kleene); it fails for rows whose
      x matches a non-NULL v: IN is TRUE, NOT IN is FALSE.

S1 items: 2-4 rows are "unknown" rows (sql == wk, another semantics differs), the rest classical.
S2 items: 2-4 rows are "absorbing" rows (sql != wk), the rest classical. Classical rows are mixed (kept and not).

  python gen_trap_items.py <out.jsonl> --per-cell N --seed S [--families lj,ni] [--splits S1,S2]
"""
import argparse
import hashlib
import json
import random
from pathlib import Path

from gen_sql_items import F, U, T, k_and, k_or, k_not, cmp, eq_vals

OPS = ("<", ">", "=", "<>", "<=", ">=")
MODES = ("sql", "wk", "nf", "nt", "eq")
N_ROWS = 8


# ------------------------------------------------------------------ formulas
def rand_formula(rng, atoms_pool, n_atoms):
    """Random binary tree over n_atoms leaves; NOT on a node with prob 0.25 (never NOT NOT)."""
    if n_atoms == 1:
        node = ("atom", rng.choice(atoms_pool)())
    else:
        left = rng.randint(1, n_atoms - 1)
        node = (rng.choice(("and", "or")), rand_formula(rng, atoms_pool, left),
                rand_formula(rng, atoms_pool, n_atoms - left))
    if rng.random() < 0.25:
        node = ("not", node)
    return node


def f_eval(node, row, mode, atom_val):
    k = node[0]
    if k == "atom":
        return atom_val(node[1], row, mode)
    if k == "not":
        return k_not(f_eval(node[1], row, mode, atom_val))
    a, b = f_eval(node[1], row, mode, atom_val), f_eval(node[2], row, mode, atom_val)
    weak = mode == "wk"
    return k_and(a, b, weak) if k == "and" else k_or(a, b, weak)


def f_sql(node, atom_sql, top=True):
    k = node[0]
    if k == "atom":
        return atom_sql(node[1])
    if k == "not":
        return f"NOT ({f_sql(node[1], atom_sql, True)})"
    parts = []
    for c in node[1:]:
        s = f_sql(c, atom_sql, False)
        parts.append(s if c[0] in ("atom", "not") else f"({s})")
    return f" {k.upper()} ".join(parts)


def f_atoms(node):
    if node[0] == "atom":
        return [node[1]]
    return [a for c in node[1:] for a in f_atoms(c)]


def lit(v):
    return "NULL" if v is None else str(v)


# ------------------------------------------------------------------ lj
def lj_atom_val(atom, row, mode):
    _, col, op, k = atom
    return cmp(op, row[col], k, mode)


def lj_atom_sql(atom):
    return f"{atom[1]} {atom[2]} {atom[3]}"


def gen_lj(rng):
    keys = rng.sample(range(1, 7), rng.randint(3, 4))
    rtab = [{"k": k, "z": rng.randint(0, 9), "w": rng.randint(0, 9)} for k in sorted(keys)]
    rmap = {x["k"]: x for x in rtab}
    trows = [{"id": i + 1, "k": rng.randint(1, 6), "y": rng.randint(0, 9)} for i in range(N_ROWS)]
    rows = []
    for t in trows:
        m = rmap.get(t["k"])
        rows.append({"t.y": t["y"], "r.z": m["z"] if m else None, "r.w": m["w"] if m else None})
    pool = [lambda: ("cmp", "t.y", rng.choice(OPS), rng.randint(1, 8)),
            lambda: ("cmp", rng.choice(("r.z", "r.w")), rng.choice(OPS), rng.randint(1, 8))]
    f = rand_formula(rng, pool, rng.randint(2, 4))
    cols = {a[1] for a in f_atoms(f)}
    if "t.y" not in cols or not cols & {"r.z", "r.w"}:
        return None
    ddl = ["CREATE TABLE t (id INTEGER PRIMARY KEY, k INTEGER NOT NULL, y INTEGER NOT NULL);",
           "CREATE TABLE r (k INTEGER PRIMARY KEY, z INTEGER NOT NULL, w INTEGER NOT NULL);"]
    ins = ([f"INSERT INTO t VALUES ({t['id']}, {t['k']}, {t['y']});" for t in trows]
           + [f"INSERT INTO r VALUES ({x['k']}, {x['z']}, {x['w']});" for x in rtab])
    query = f"SELECT t.id FROM t LEFT JOIN r ON t.k = r.k WHERE {f_sql(f, lj_atom_sql)} ORDER BY t.id;"
    return rows, f, lj_atom_val, ddl, ins, query


# ------------------------------------------------------------------ ni
def gen_ni(rng):
    s = [rng.randint(0, 6) for _ in range(rng.randint(2, 4))]
    s.insert(rng.randrange(len(s) + 1), None)
    trows = [{"id": i + 1, "x": rng.randint(0, 6), "y": rng.randint(0, 9)} for i in range(N_ROWS)]

    def atom_val(atom, row, mode):
        if atom[0] == "cmp":
            return cmp(atom[2], row["y"], atom[3], mode)
        v = F
        for e in s:
            v = k_or(v, eq_vals(row["x"], e, mode), mode == "wk")
        return v if atom[0] == "in" else k_not(v)

    def atom_sql(atom):
        if atom[0] == "cmp":
            return f"y {atom[2]} {atom[3]}"
        return f"x {'IN' if atom[0] == 'in' else 'NOT IN'} (SELECT v FROM s)"

    pool = [lambda: ("cmp", "y", rng.choice(OPS), rng.randint(1, 8)),
            lambda: (rng.choice(("in", "notin")),)]
    f = rand_formula(rng, pool, rng.randint(2, 4))
    kinds = {a[0] for a in f_atoms(f)}
    if "cmp" not in kinds or not kinds & {"in", "notin"}:
        return None
    ddl = ["CREATE TABLE t (id INTEGER PRIMARY KEY, x INTEGER NOT NULL, y INTEGER NOT NULL);",
           "CREATE TABLE s (v INTEGER);"]
    ins = ([f"INSERT INTO t VALUES ({t['id']}, {t['x']}, {t['y']});" for t in trows]
           + [f"INSERT INTO s VALUES ({lit(v)});" for v in s])
    query = f"SELECT id FROM t WHERE {f_sql(f, atom_sql)} ORDER BY id;"
    return trows, f, atom_val, ddl, ins, query


# ------------------------------------------------------------------ items
def category(mem):
    if len(set(mem.values())) == 1:
        return "classical"
    return "absorbing" if mem["sql"] != mem["wk"] else "unknown"


def gen_item(rng, fam, split):
    target = {"S1": "unknown", "S2": "absorbing"}.get(split)
    for _ in range(20000):
        g = (gen_lj if fam == "lj" else gen_ni)(rng)
        if g is None:
            continue
        rows, f, aval, ddl, ins, query = g
        mems = [{m: f_eval(f, r, m, aval) == T for m in MODES} for r in rows]
        cats = [category(m) for m in mems]
        spec = [c for c in cats if c != "classical"]
        if split == "SX":
            # mixed split: 2-4 unknown and 2-4 absorbing rows in the same item; SQL keeps 1..7 rows
            if not (2 <= cats.count("unknown") <= 4 and 2 <= cats.count("absorbing") <= 4):
                continue
            n_keep = sum(m["sql"] for m in mems)
            if n_keep == 0 or n_keep == len(rows):
                continue
        else:
            if not (2 <= len(spec) <= 4) or any(c != target for c in spec):
                continue
            cls = [m["sql"] for m, c in zip(mems, cats) if c == "classical"]
            if all(cls) or not any(cls):
                continue
        answers = {m: [i + 1 for i, x in enumerate(mems) if x[m]] for m in MODES}
        return dict(ddl=ddl, inserts=ins, query=query, answer_kind="ids", answers=answers,
                    n_special=len(spec), cats=cats, n_atoms=len(f_atoms(f)))
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--per-cell", type=int, default=8)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--families", default="lj,ni")
    ap.add_argument("--splits", default="S1,S2")
    a = ap.parse_args()
    rng = random.Random(a.seed)
    out = []
    for fam in a.families.split(","):
        for split in a.splits.split(","):
            seen, made = set(), 0
            while made < a.per_cell:
                it = gen_item(rng, fam, split)
                if it is None:
                    raise SystemExit(f"cannot fill {fam} {split}")
                key = (tuple(it["inserts"]), it["query"])
                if key in seen:
                    continue
                seen.add(key)
                it.update(id=f"{fam}-{split}-d00-r{N_ROWS:02d}-{made:03d}", family=fam, split=split, depth=0,
                          rows=N_ROWS)
                out.append(it)
                made += 1
            print(fam, split, made, flush=True)
    p = Path(a.out)
    with p.open("w", encoding="utf-8", newline="\n") as fh:
        for it in out:
            fh.write(json.dumps(it, ensure_ascii=False) + "\n")
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    p.with_suffix(".sha256").write_text(f"{h}  {p.name}\n")
    print(len(out), "items", h)


if __name__ == "__main__":
    main()
