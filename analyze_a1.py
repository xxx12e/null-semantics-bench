"""Preregistered analysis for amendment A1 (Fable 5.2), PREREG_VLDB_A1.md. Written and hash-locked before submission.

  python analyze_a1.py formal_a1_fable52 [--boot 10000]
Same row categories, missing-answer rule (>20% missing -> cell not testable) and bootstrap as analyze_formal.py.
H1f-H3f are one-sided item-bootstrap tests, Holm-corrected over the three; H5f is decided on the point estimate.
"""
import argparse
import json
import random
from pathlib import Path

from analyze_formal import row_records, testable, by_item, rate, boot_diff, holm, wilson_lo

HERE = Path(__file__).resolve().parent
M = "claude-fable-5-2"


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

    def sel(**kw):
        return by_item([x for x in rows if ok(x) and x["model"] == M and
                        all(x[k] in v if isinstance(v, tuple) else x[k] == v for k, v in kw.items())])

    T, ps = {}, {}
    contrasts = {
        "H1f_where": (sel(family="where", split="S2", cat="absorbing", arm=("A", "A64")),
                      sel(family="where", split="S1", cat="unknown", arm=("A", "A64")), False),
        "H2f_within": (sel(family="where", split="S2", cat="absorbing", arm=("A", "A64")),
                       sel(family="where", split="S2", cat="classical", arm=("A", "A64")), True),
        "H3f_check": (sel(family="check", split="S2", cat="absorbing", arm="C"),
                      sel(family="check", split="S1", cat="unknown", arm="C"), False),
    }
    for name, (x, y, paired) in contrasts.items():
        if not x or not y:
            T[name] = "not testable"
            continue
        d, lo, p = boot_diff(x, y, a.boot, rng, paired=paired)
        T[name] = {"a": rate(x), "b": rate(y), "diff": d, "lo95": lo, "p": p}
        ps[name] = p
    for name, q in holm(ps).items():
        T[name].update(p_holm=q, **{"pass": q < 0.05})
    for cell in ("d8r12", "d32r6", "d64r6"):
        T[f"S_cell|{cell}"] = {"absorbing_S2": rate(sel(family="where", split="S2", cat="absorbing", cell=cell)),
                               "unknown_S1": rate(sel(family="where", split="S1", cat="unknown", cell=cell)),
                               "S0_rows": rate(sel(family="where", split="S0", cell=cell))}
    wp = run / "scored_write.jsonl"
    if wp.exists():
        w = [json.loads(l) for l in wp.open(encoding="utf-8") if json.loads(l)["model"] == M]
        t = [x for x in w if x["split"] == "trap"]
        k = sum(bool(x.get("null_all")) for x in t)
        T["H5f_write"] = {"trap_null_ok": k, "n": len(t), "wilson_lo": wilson_lo(k, len(t)),
                          "pass": bool(t) and k / len(t) >= 0.90}
        T["S_write_all"] = {"null_ok": sum(bool(x.get("null_all")) for x in w),
                            "clean_ok": sum(bool(x.get("clean_all")) for x in w), "n": len(w)}
    out = {"tests": T, "not_testable": {"|".join(k): v for k, v in miss.items() if k in bad},
           "outcomes": {o: sum(r["outcome"] == o for r in scored) for o in {r["outcome"] for r in scored}}}
    (run / "analysis.json").write_text(json.dumps(out, indent=1, default=str))
    for k, v in T.items():
        print(k, v)
    print("not testable:", out["not_testable"], "outcomes:", out["outcomes"])


if __name__ == "__main__":
    main()
