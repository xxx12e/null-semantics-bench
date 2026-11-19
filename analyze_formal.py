"""Preregistered analysis for the formal run (PREREG_VLDB.md). Written and hash-locked before any formal request was sent.

  python analyze_formal.py <run_name> [--boot 10000]
reads runs/<run>/scored.jsonl (task A, from `sql_eval.py score`) and runs/<run>/scored_write.jsonl (task B, from
score_write.py), prints every preregistered test and writes runs/<run>/analysis.json.

Row categories (WHERE and CHECK items; a row "is in" the answer when the query returns its id):
  classical   all five semantics (sql, wk, nf, nt, eq) agree on the row
  absorbing   sql and wk disagree on the row (SQL absorbs the NULL; NULL-poisoning would flip the row)
  unknown     sql and wk agree but some other collapse rule disagrees (the row's condition is UNKNOWN in SQL)
Row error: the model's membership for the row differs from the gold (= sql) membership.
Answers that are truncated, refused, errored or unparsed contribute no rows; they are counted per cell instead, and a
model-cell with more than 20% such answers is "not testable" and left out of the tests that use it.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEMS = ("sql", "wk", "nf", "nt", "eq")
MAIN_MODELS = ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5")
MISSING = ("truncated", "refusal", "error", "unparsed")


def row_records(scored, pool):
    """One record per (answer, row) for ids-type items, with the row's category and whether the model erred."""
    out = []
    for r in scored:
        it = pool[r["item"]]
        if it["answer_kind"] != "ids" or r["outcome"] in MISSING:
            continue
        ans = {m: set(it["answers"][m]) for m in SEMS}
        got = set(x for x in r["parsed"] if x is not None)
        for i in range(1, it["rows"] + 1):
            mem = {m: i in ans[m] for m in SEMS}
            cat = ("classical" if len(set(mem.values())) == 1
                   else "absorbing" if mem["sql"] != mem["wk"] else "unknown")
            out.append({"model": r["model"], "cond": r["cond"], "arm": r["arm"], "family": r["family"],
                        "split": r["split"], "cell": f"d{r['depth']}r{r['rows']}", "item": r["item"],
                        "cat": cat, "err": (i in got) != mem["sql"], "as_wk": (i in got) == mem["wk"]})
    return out


def testable(scored):
    """(model, arm, family, split, cell) -> share of missing answers; cells above 20% are not testable."""
    c = collections.defaultdict(collections.Counter)
    for r in scored:
        k = (r["model"], r["arm"], r["family"], r["split"], f"d{r['depth']}r{r['rows']}")
        c[k]["n"] += 1
        c[k]["miss"] += r["outcome"] in MISSING
    return {k: v["miss"] / v["n"] for k, v in c.items()}


def by_item(rows):
    """item -> [errors, rows]"""
    d = collections.defaultdict(lambda: [0, 0])
    for x in rows:
        d[x["item"]][0] += x["err"]
        d[x["item"]][1] += 1
    return d


def rate(d):
    e = sum(v[0] for v in d.values())
    n = sum(v[1] for v in d.values())
    return e / n if n else float("nan"), e, n


def boot_diff(a, b, B, rng, paired=False):
    """One-sided bootstrap for rate(a) - rate(b) > 0, resampling items (clusters).
    Unpaired: a and b resampled independently. Paired: a and b share items; one resample of items is used for both.
    Returns (diff, lower 95% one-sided bound, p)."""
    diff = rate(a)[0] - rate(b)[0]
    ka, kb = list(a), list(b)
    if paired:
        keys = sorted(set(ka) & set(kb))
    sims = []
    for _ in range(B):
        if paired:
            s = [rng.choice(keys) for _ in keys]
            ra, rb = rate({j: a[k] for j, k in enumerate(s)})[0], rate({j: b[k] for j, k in enumerate(s)})[0]
        else:
            ra = rate({j: a[k] for j, k in enumerate(rng.choice(ka) for _ in ka)})[0]
            rb = rate({j: b[k] for j, k in enumerate(rng.choice(kb) for _ in kb)})[0]
        sims.append(ra - rb)
    sims = [x for x in sims if not math.isnan(x)]
    sims.sort()
    lo = sims[int(0.05 * len(sims))]
    p = (1 + sum(x <= 0 for x in sims)) / (len(sims) + 1)
    return diff, lo, p


def holm(ps):
    """Holm step-down; ps: dict name -> p. Returns name -> adjusted p."""
    order = sorted(ps, key=ps.get)
    adj, run = {}, 0.0
    for i, k in enumerate(order):
        run = max(run, min(1.0, (len(order) - i) * ps[k]))
        adj[k] = run
    return adj


def wilson_lo(k, n, z=1.96):
    if n == 0:
        return float("nan")
    p = k / n
    return (p + z * z / (2 * n) - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / (1 + z * z / n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--boot", type=int, default=10000)
    ap.add_argument("--seed", type=int, default=20261117)
    a = ap.parse_args()
    run = HERE / "runs" / a.run
    spec = json.loads((run / "spec.json").read_text())
    pool = {json.loads(l)["id"]: json.loads(l) for l in (HERE / spec["pool"]).open(encoding="utf-8")}
    scored = [json.loads(l) for l in (run / "scored.jsonl").open(encoding="utf-8")]
    rows = row_records(scored, pool)
    miss = testable(scored)
    bad = {k for k, v in miss.items() if v > 0.20}
    ok = lambda x: (x["model"], x["arm"], x["family"], x["split"], x["cell"]) not in bad
    rng = random.Random(a.seed)
    R = {"not_testable": {"|".join(k): round(v, 3) for k, v in miss.items() if k in bad}, "tests": {}}

    def sel(**kw):
        return [x for x in rows if ok(x) and all(x[k] == v if not isinstance(v, tuple) else x[k] in v
                                                 for k, v in kw.items())]

    def family_test(tag, fam, arms, models):
        ps = {}
        for m in models:
            s2 = by_item(sel(model=m, family=fam, split="S2", cat="absorbing", cond="plain", arm=arms))
            s1 = by_item(sel(model=m, family=fam, split="S1", cat="unknown", cond="plain", arm=arms))
            if not s2 or not s1:
                R["tests"][f"{tag}|{m}"] = "not testable"
                continue
            d, lo, p = boot_diff(s2, s1, a.boot, rng)
            R["tests"][f"{tag}|{m}"] = {"absorbing_S2": rate(s2), "unknown_S1": rate(s1), "diff": d, "lo95": lo, "p": p}
            ps[m] = p
        for m, q in holm(ps).items():
            R["tests"][f"{tag}|{m}"]["p_holm"] = q
            R["tests"][f"{tag}|{m}"]["pass"] = q < 0.05

    # H1: WHERE, absorbing rows in S2 items vs unknown rows in S1 items (arms A and A64, all three cells pooled)
    family_test("H1_where", "where", ("A", "A64"), MAIN_MODELS)
    # H3: CHECK, same contrast
    family_test("H3_check", "check", ("C",), MAIN_MODELS)

    # H2: within S2 WHERE items, absorbing rows vs classical rows of the same items (paired by item)
    ps = {}
    for m in MAIN_MODELS:
        ab = by_item(sel(model=m, family="where", split="S2", cat="absorbing", cond="plain", arm=("A", "A64")))
        cl = by_item(sel(model=m, family="where", split="S2", cat="classical", cond="plain", arm=("A", "A64")))
        d, lo, p = boot_diff(ab, cl, a.boot, rng, paired=True)
        R["tests"][f"H2_within|{m}"] = {"absorbing": rate(ab), "classical": rate(cl), "diff": d, "lo95": lo, "p": p}
        ps[m] = p
    for m, q in holm(ps).items():
        R["tests"][f"H2_within|{m}"].update(p_holm=q, **{"pass": q < 0.05})

    # H4: stating the rules lowers the absorbing-row error, same S2 items under plain (arm A) and rules (arm R)
    ps = {}
    for m in MAIN_MODELS:
        rl = by_item(sel(model=m, family="where", split="S2", cat="absorbing", cond="rules", arm="R"))
        pl = by_item(sel(model=m, family="where", split="S2", cat="absorbing", cond="plain", arm="A"))
        pl = {k: v for k, v in pl.items() if k in rl}
        if not rl or not pl:
            R["tests"][f"H4_rules|{m}"] = "not testable"
            continue
        d, lo, p = boot_diff(pl, rl, a.boot, rng, paired=True)
        R["tests"][f"H4_rules|{m}"] = {"plain": rate(pl), "rules": rate(rl), "diff": d, "lo95": lo, "p": p}
        ps[m] = p
    for m, q in holm(ps).items():
        R["tests"][f"H4_rules|{m}"].update(p_holm=q, **{"pass": q < 0.05})

    # secondary (no multiplicity correction): Fable on H1; per-cell H1; item accuracy of the simple families
    fam_f = ("claude-fable-5-1",)
    s2 = by_item(sel(model=fam_f[0], family="where", split="S2", cat="absorbing", arm="F"))
    s1 = by_item(sel(model=fam_f[0], family="where", split="S1", cat="unknown", arm="F"))
    if s2 and s1:
        d, lo, p = boot_diff(s2, s1, a.boot, rng)
        R["tests"]["S_fable_H1"] = {"absorbing_S2": rate(s2), "unknown_S1": rate(s1), "diff": d, "lo95": lo, "p": p}
    else:
        R["tests"]["S_fable_H1"] = "not testable"
    for m in MAIN_MODELS:
        for cell in ("d8r12", "d32r6", "d64r6"):
            s2 = by_item(sel(model=m, family="where", split="S2", cat="absorbing", cond="plain", cell=cell))
            s1 = by_item(sel(model=m, family="where", split="S1", cat="unknown", cond="plain", cell=cell))
            cl0 = by_item(sel(model=m, family="where", split="S0", cond="plain", cell=cell))
            R["tests"][f"S_cell|{m}|{cell}"] = {"absorbing_S2": rate(s2), "unknown_S1": rate(s1), "S0_rows": rate(cl0)}
    acc = collections.defaultdict(collections.Counter)
    for r in scored:
        if r["arm"] == "E":
            k = f"S_simple|{r['model']}|{r['family']}|{r['split']}"
            acc[k]["n"] += 1
            acc[k][r["outcome"]] += 1
    R["tests"].update({k: dict(v) for k, v in acc.items()})
    miss_c = collections.Counter()
    for r in scored:
        miss_c[(r["model"], r["outcome"])] += 1
    R["outcomes"] = {"|".join(k): v for k, v in miss_c.items()}

    # H5: query writing, trap items, null suite, all-instances-correct rate >= 0.90 per model (plain and bare pooled)
    wp = run / "scored_write.jsonl"
    if wp.exists():
        w = [json.loads(l) for l in wp.open(encoding="utf-8")]
        for m in MAIN_MODELS:
            t = [x for x in w if x["model"] == m and x["split"] == "trap"]
            k = sum(bool(x.get("null_all")) for x in t)
            R["tests"][f"H5_write|{m}"] = {"trap_null_ok": k, "n": len(t), "rate": k / len(t) if t else None,
                                           "wilson_lo": wilson_lo(k, len(t)), "pass": bool(t) and k / len(t) >= 0.90}
            for split in ("trap", "control"):
                for cond in ("plain", "bare"):
                    s = [x for x in w if x["model"] == m and x["split"] == split and x["cond"] == cond]
                    R["tests"][f"S_write|{m}|{split}|{cond}"] = {
                        "null_ok": sum(bool(x.get("null_all")) for x in s),
                        "clean_ok": sum(bool(x.get("clean_all")) for x in s), "n": len(s)}

    (run / "analysis.json").write_text(json.dumps(R, indent=1, default=str))
    for k, v in R["tests"].items():
        print(k, v)
    print("not testable:", R["not_testable"])


if __name__ == "__main__":
    main()
