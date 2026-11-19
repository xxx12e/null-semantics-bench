"""Every number quoted in the VLDB paper comes from here.
  python paper_numbers.py      -> numbers.json, numbers.tex (LaTeX macros), figs/*.pdf
Reads ../runs/formal_v1 (scored.jsonl, scored_write.jsonl, analysis.json), ../pool, ../spend_ledger_vldb.json.
"""
import collections
import json
import random
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
V = HERE.parent
RUN = V / "runs" / "formal_v1"
RUN_A1 = V / "runs" / "formal_a1_fable52"     # amendment A1 (Fable 5.2), same items
import sys
sys.path.insert(0, str(V))
from analyze_formal import row_records, MISSING, testable  # noqa: E402

MODELS = [("claude-haiku-5-5", "Haiku 5.5"), ("claude-sonnet-5-5", "Sonnet 5.5"),
          ("claude-opus-5-5", "Opus 5.5"), ("claude-fable-5-2", "Fable 5.2"), ("claude-fable-5-1", "Fable 5.1")]
CELLS = ["d8r12", "d32r6", "d64r6"]


def load(p):
    return [json.loads(l) for l in p.open(encoding="utf-8")]


def boot_ci(d, B=2000, seed=1):
    """item -> [err, n]; 95% percentile CI of the pooled rate, resampling items."""
    if not d:
        return None, None, None
    keys = list(d)
    rng = random.Random(seed)
    e = sum(v[0] for v in d.values())
    n = sum(v[1] for v in d.values())
    sims = []
    for _ in range(B):
        s = [d[rng.choice(keys)] for _ in keys]
        nn = sum(v[1] for v in s)
        if nn:
            sims.append(sum(v[0] for v in s) / nn)
    sims.sort()
    return e / n, sims[int(.025 * len(sims))], sims[int(.975 * len(sims)) - 1]


def by_item(rows):
    d = collections.defaultdict(lambda: [0, 0])
    for x in rows:
        d[x["item"]][0] += x["err"]
        d[x["item"]][1] += 1
    return d


def main():
    spec = json.loads((RUN / "spec.json").read_text())
    pool = {it["id"]: it for it in load(V / spec["pool"])}
    scored = load(RUN / "scored.jsonl") + load(RUN_A1 / "scored.jsonl")
    write = load(RUN / "scored_write.jsonl") + load(RUN_A1 / "scored_write.jsonl")
    an = json.loads((RUN / "analysis.json").read_text())
    rows = row_records(scored, pool)
    N = {}

    # ---- materials
    N["pool_items"] = len(pool)
    N["requests"] = len(scored) + len(write)
    N["engines"] = next(iter(pool.values()))["engines"]
    led = json.loads((V / "spend_ledger_vldb.json").read_text())["runs"]
    FORMAL = ("formal_v1", "formal_a1_fable52")
    N["usd_formal"] = led["formal_v1"]["actual_usd"]
    N["usd_a1"] = led["formal_a1_fable52"]["actual_usd"]
    N["usd_pilots"] = round(sum(v.get("actual_usd", 0) for k, v in led.items() if k not in FORMAL), 2)
    N["pilot_requests"] = sum(v["n"] for k, v in led.items() if k not in FORMAL)
    N["an_a1"] = json.loads((RUN_A1 / "analysis.json").read_text())["tests"]
    N["v0_items"] = sum(1 for _ in (V / "pool/sql_pool_v0_gold.jsonl").open(encoding="utf-8"))
    N["p2_items"] = sum(1 for _ in (V / "pool/sql_pool_p2_gold.jsonl").open(encoding="utf-8"))

    # ---- row-level rates with CIs, per model x cell x category (WHERE, plain)
    fig = {}
    for m, name in MODELS:
        for cell in CELLS:
            sel = lambda **kw: [x for x in rows if x["model"] == m and x["family"] == "where" and x["cond"] == "plain"
                                and x["cell"] == cell and all(x[k] == v for k, v in kw.items())]
            miss = [r for r in scored if r["model"] == m and r["family"] == "where" and r["cond"] == "plain"
                    and f"d{r['depth']}r{r['rows']}" == cell]
            mr = sum(r["outcome"] in MISSING for r in miss) / len(miss) if miss else None
            fig[f"{name}|{cell}"] = {
                "absorbing": boot_ci(by_item(sel(split="S2", cat="absorbing"))),
                "unknown": boot_ci(by_item(sel(split="S1", cat="unknown"))),
                "classical_S2": boot_ci(by_item(sel(split="S2", cat="classical"))),
                "S0": boot_ci(by_item(sel(split="S0"))),
                "missing": mr, "n_answers": len(miss)}
    N["fig_rows"] = fig
    # CHECK
    for m, name in MODELS[:4]:
        sel = lambda **kw: [x for x in rows if x["model"] == m and x["family"] == "check" and all(x[k] == v for k, v in kw.items())]
        N[f"check|{name}"] = {"absorbing": boot_ci(by_item(sel(split="S2", cat="absorbing"))),
                              "unknown": boot_ci(by_item(sel(split="S1", cat="unknown"))),
                              "classical_S2": boot_ci(by_item(sel(split="S2", cat="classical")))}

    # ---- item-level accuracy per split (what an item-accuracy benchmark would report)
    acc = collections.defaultdict(collections.Counter)
    for r in scored:
        fam = r["family"] if r["family"] in ("where", "check") else "simple"
        k = (r["model"], fam, r["split"], r["cond"])
        acc[k]["n"] += 1
        acc[k]["ok"] += r["outcome"] == "correct"
        acc[k]["miss"] += r["outcome"] in MISSING
        acc[k]["exact_wk"] += r["outcome"] == "wrong" and "wk" in r["matches"]
        acc[k]["wrong"] += r["outcome"] == "wrong"
    N["item_acc"] = {"|".join(k): dict(v) for k, v in acc.items()}

    # ---- tokens and missing per model
    tok = collections.defaultdict(list)
    for r in scored + write:
        if r.get("output_tokens"):
            tok[r["model"]].append(r["output_tokens"])
    N["tokens"] = {m: {"mean": round(sum(v) / len(v)), "median": sorted(v)[len(v) // 2], "max": max(v)} for m, v in tok.items()}
    oc = collections.Counter((r["model"], r["outcome"]) for r in scored)
    N["outcomes"] = {"|".join(k): v for k, v in oc.items()}

    # ---- task B
    idi = collections.Counter()
    idi_m = collections.defaultdict(collections.Counter)
    for r in write:
        s = (r.get("sql") or "").upper()
        idi_m[r["model"]]["queries"] += 1
        for t in ("NOT EXISTS", "IS DISTINCT FROM", "COALESCE", "IS NULL", "IS NOT NULL", "NOT IN", "NULLS LAST", "LEFT JOIN"):
            hit = bool(re.search(r"\b" + t.replace(" ", r"\s+") + r"\b", s))
            idi[t] += hit
            idi_m[r["model"]][t] += hit
    N["write_idioms_by_model"] = {m: dict(c) for m, c in idi_m.items()}
    N["write_n"] = len(write)
    N["write_ok_null"] = sum(bool(r.get("null_all")) for r in write)
    N["write_ok_clean"] = sum(bool(r.get("clean_all")) for r in write)
    N["write_idioms"] = dict(idi)
    N["write_items"] = sum(1 for _ in (V / "pool/write_f/items_write.jsonl").open(encoding="utf-8"))
    cost = collections.defaultdict(collections.Counter)
    for r in load(RUN / "results.jsonl") + load(RUN_A1 / "results.jsonl"):
        cost[r["model"]]["n"] += 1
        cost[r["model"]]["usd"] += r.get("usd") or 0
        cost[r["model"]]["out"] += r.get("output_tokens") or 0
        cost[r["model"]]["truncated"] += r.get("stop_reason") == "max_tokens"
        cost[r["model"]]["refusal"] += r.get("stop_reason") == "refusal"
    N["cost"] = {m: {k: (round(v, 2) if isinstance(v, float) else v) for k, v in c.items()} for m, c in cost.items()}
    N["tests"] = an["tests"]
    N["not_testable"] = an["not_testable"]

    # ---- teaser: unknown vs absorbing rows per model, pooled over the testable sizes each model ran (plain prompt)
    miss = testable(scored)
    ok = lambda x: miss.get((x["model"], x["arm"], x["family"], x["split"], x["cell"]), 0) <= 0.20
    teaser = {}
    for fam in ("where", "check"):
        for m, name in MODELS:
            sel = lambda split, cat: [x for x in rows if x["model"] == m and x["family"] == fam and x["cond"] == "plain"
                                      and x["split"] == split and x["cat"] == cat and ok(x)]
            a, u = by_item(sel("S2", "absorbing")), by_item(sel("S1", "unknown"))
            if not a:
                continue
            teaser[f"{fam}|{name}"] = {"absorbing": boot_ci(a), "unknown": boot_ci(u),
                                       "abs_counts": [sum(v[0] for v in a.values()), sum(v[1] for v in a.values())],
                                       "unk_counts": [sum(v[0] for v in u.values()), sum(v[1] for v in u.values())]}
    # counts quoted in Table 2 and Section 5; the figure must show exactly these
    expect = {"where|Haiku 5.5": ([8, 200], [0, 198]), "where|Sonnet 5.5": ([19, 240], [0, 240]),
              "where|Opus 5.5": ([18, 240], [1, 240]), "where|Fable 5.1": ([4, 100], [0, 100]),
              "where|Fable 5.2": ([3, 240], [0, 240]), "check|Haiku 5.5": ([4, 80], [0, 80]),
              "check|Sonnet 5.5": ([25, 80], [0, 80]), "check|Opus 5.5": ([12, 80], [0, 80]),
              "check|Fable 5.2": ([0, 80], [0, 80])}
    assert set(teaser) == set(expect), sorted(teaser)
    for k, (ac, uc) in expect.items():
        assert teaser[k]["abs_counts"] == ac and teaser[k]["unk_counts"] == uc, (k, teaser[k])
    N["teaser"] = teaser
    (HERE / "numbers.json").write_text(json.dumps(N, indent=1, default=str))
    make_fig(fig)
    make_teaser(teaser, pool, N)
    print("ok")


# Palette: dataviz reference categorical slots 1-3 (validated all-pairs on white: worst CVD dE 9.2, normal 24.0);
# S0 rows are a neutral context series. Every series also has its own marker shape (redundant encoding).
C_UNK, C_ABS, C_CLS, C_S0 = "#2a78d6", "#eb6834", "#1baf7a", "#6b6a66"
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"


def style(plt):
    """Text is set by LaTeX with the paper's own fonts (Linux Libertine, Type 1), so figure and body text match."""
    plt.rcParams.update({"text.usetex": True,
                         "text.latex.preamble": r"\usepackage[T1]{fontenc}\usepackage{libertine}\usepackage[libertine]{newtxmath}",
                         "font.family": "serif", "font.size": 7.5, "axes.spines.top": False, "axes.spines.right": False,
                         "axes.edgecolor": INK2, "axes.labelcolor": INK, "xtick.color": INK2, "ytick.color": INK2,
                         "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6,
                         "pdf.fonttype": 42, "ps.fonttype": 42})


def make_teaser(t, pool, N):
    """Full-width overview teaser (178 mm), page 1. Every number is computed here from the pool or from numbers.json.
    Left: boxes on what existing benchmarks miss; middle: a ring chart of the benchmark's composition; right: stacked
    statistics boxes.
    """
    import collections
    import math
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch, Wedge
    style(plt)
    SEMS = ["sql", "wk", "nf", "nt", "eq"]
    W, H = 178.0, 84.0
    f = plt.figure(figsize=(W / 25.4, H / 25.4))
    L = f.add_axes([0, 0, 1, 1])
    L.set_xlim(0, W)
    L.set_ylim(0, H)
    L.axis("off")
    RED, GREEN = "#d1453b", "#14936a"
    PANEL, EDGE, HEAD = "#f6f7f9", "#cfd5dd", "#e3e8ef"
    WARM, WARMEDGE, WARMHEAD = "#fbf6f1", "#e3cdb8", "#f6e4d3"

    def box(x, y, w, h, fc, ec, r=1.4, lw=0.6, z=1):
        L.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={r}", fc=fc, ec=ec, lw=lw,
                                   zorder=z))

    def txt(x, y, s, size=6.4, c=INK, ha="left", va="center", z=4, **kw):
        L.text(x, y, s, fontsize=size, color=c, ha=ha, va=va, zorder=z, **kw)

    def tick(x, y, s=0.9):
        L.plot([x - s, x - s * .3, x + s], [y, y - s * .7, y + s * .9], color=GREEN, lw=1.0, solid_capstyle="round",
               solid_joinstyle="round", zorder=5)

    def cross(x, y, s=0.75):
        for a, b in ((-1, 1), (1, -1)):
            L.plot([x - s, x + s], [y + a * s, y + b * s], color=RED, lw=1.0, solid_capstyle="round", zorder=5)

    def titled(x, y, w, h, title, fc=PANEL, ec=EDGE, hc=HEAD):
        box(x, y, w, h, fc, ec)
        L.add_patch(FancyBboxPatch((x, y + h - 4.6), w, 4.6, boxstyle="round,pad=0,rounding_size=1.4", fc=hc, lw=0,
                                   zorder=2))
        L.add_patch(plt.Rectangle((x, y + h - 4.6), w, 2.0, fc=hc, lw=0, zorder=2))
        txt(x + 2.0, y + h - 2.3, title, size=6.6)

    # ------------------------------------------------------------------ pool statistics (computed, not typed)
    fam_n = collections.Counter(it["family"] for it in pool.values())
    split_n = collections.Counter((it["family"], it["split"]) for it in pool.values())
    cats = collections.Counter()
    for it in pool.values():
        if it["answer_kind"] != "ids" or it["family"] not in ("where", "check"):
            continue
        ans = {m: set(it["answers"][m]) for m in SEMS}
        for i in range(1, it["rows"] + 1):
            mem = {m: i in ans[m] for m in SEMS}
            cats["classical" if len(set(mem.values())) == 1 else "absorbing" if mem["sql"] != mem["wk"] else "unknown"] += 1
    n_write = N["write_items"]
    n_pred = len(pool)
    unk_e = sum(t[k]["unk_counts"][0] for k in t)
    unk_n = sum(t[k]["unk_counts"][1] for k in t)
    abs_e = sum(t[k]["abs_counts"][0] for k in t)
    abs_n = sum(t[k]["abs_counts"][1] for k in t)
    assert (unk_e, unk_n, abs_e, abs_n) == (1, 1338, 93, 1340)
    items_all = N["v0_items"] + N["p2_items"] + n_pred + 64     # 64 = the exploratory pilot items (p3 + trap pools)
    assert items_all == 3700, items_all

    # ------------------------------------------------------------------ left column: what UNKNOWN-only tests miss
    x0, w0 = 0.4, 49.0
    box(x0, 0.4, w0, H - 0.8, WARM, WARMEDGE, lw=0.7)
    box(x0, H - 7.0, w0, 6.6, WARMHEAD, WARMEDGE, lw=0.7, z=2)
    txt(x0 + w0 / 2, H - 3.7, r"\textbf{Why} \texttt{UNKNOWN}\textbf{-only tests fall short}", size=7.0, ha="center")

    # 1. the shortcut passes them
    y1 = 52.0
    titled(x0 + 2, y1, w0 - 4, 22.6, r"\textbf{1}\enspace The shortcut passes them", fc="white", ec=WARMEDGE,
           hc=WARMHEAD)
    txt(x0 + 4, y1 + 15.0, r"\texttt{WHERE a > 5 OR b < 5}", size=6.0)
    cols = (x0 + 4.0, x0 + 17.5, x0 + 29.5, x0 + 37.0)
    for cx, h in zip(cols, ("row", "condition", "SQL", "shortcut")):
        txt(cx, y1 + 11.4, h, size=5.4, c=INK2)
    L.plot([x0 + 4, x0 + w0 - 4], [y1 + 10.0, y1 + 10.0], color=WARMEDGE, lw=0.5, zorder=4)
    for yy, row, cond, sql_, ok in ((y1 + 7.3, r"\texttt{(2,NULL,9)}", r"$\mathsf{U}\lor\mathsf{F}=\mathsf{U}$", "drop", True),
                                   (y1 + 3.0, r"\texttt{(1,NULL,3)}", r"$\mathsf{U}\lor\mathsf{T}=\mathsf{T}$", "keep", False)):
        txt(cols[0], yy, row, size=5.6)
        txt(cols[1], yy, cond, size=6.0)
        txt(cols[2], yy, sql_, size=6.0)
        txt(cols[3], yy, "drop", size=6.0)
        (tick if ok else cross)(cols[3] + 6.0, yy)
    L.add_patch(plt.Rectangle((x0 + 3.0, y1 + 5.4), 2.4, 3.8, fc=C_UNK, lw=0, zorder=3, alpha=0))

    # 2. item scores hide row errors (Sonnet 5.5, nested WHERE)
    ia = N["item_acc"]
    s1 = ia["claude-sonnet-5-5|where|S1|plain"]
    s2 = ia["claude-sonnet-5-5|where|S2|plain"]
    h1 = N["tests"]["H1_where|claude-sonnet-5-5"]
    y2 = 27.0
    titled(x0 + 2, y2, w0 - 4, 22.6, r"\textbf{2}\enspace Item scores hide row errors", fc="white", ec=WARMEDGE,
           hc=WARMHEAD)
    txt(x0 + 4, y2 + 15.0, r"Sonnet 5.5, nested \texttt{WHERE}", size=6.0)
    txt(x0 + 21.0, y2 + 11.4, "items exact", size=5.4, c=INK2)
    txt(x0 + 34.0, y2 + 11.4, "rows misread", size=5.4, c=INK2)
    L.plot([x0 + 4, x0 + w0 - 4], [y2 + 10.0, y2 + 10.0], color=WARMEDGE, lw=0.5, zorder=4)
    for yy, lab, it_, rr, col in ((y2 + 7.3, r"\So{} (\texttt{UNKNOWN})", s1, h1["unknown_S1"], C_UNK),
                                 (y2 + 3.0, r"\St{} (absorbed)", s2, h1["absorbing_S2"], C_ABS)):
        txt(x0 + 4, yy, lab.replace(r"\So{}", r"\textsf{S1}").replace(r"\St{}", r"\textsf{S2}"), size=5.8)
        txt(x0 + 21.0, yy, f"{it_['ok']}/{it_['n']}", size=6.0)
        txt(x0 + 34.0, yy, r"\textbf{" + f"{rr[1]}/{rr[2]}" + "}", size=6.0, c=col)

    # 3. NULL-free test data
    y3 = 2.4
    titled(x0 + 2, y3, w0 - 4, 22.6, r"\textbf{3}\enspace Test data lack the \texttt{NULL}s", fc="white",
           ec=WARMEDGE, hc=WARMHEAD)
    for k, line in enumerate((r"Execution-based text-to-SQL suites", r"seldom contain the \texttt{NULL} patterns that",
                              r"separate right from wrong queries; a",
                              r"right answer on them says little about",
                              r"\texttt{NULL} (Section~1).")):
        txt(x0 + 4, y3 + 14.6 - k * 3.0, line, size=5.9, c=INK)

    # ------------------------------------------------------------------ centre: composition ring (4 rings, every
    # segment labelled): task -> family -> split -> condition size. Counts come from the pool files.
    cx, cy, R = 82.0, 42.0, 31.5
    rings = (0.30, 0.49, 0.665, 0.835, 1.0)
    cell_n = collections.Counter((it["family"], it["split"], it["depth"], it["rows"]) for it in pool.values())
    fams = [("where", r"\texttt{WHERE}"), ("check", r"\texttt{CHECK}"), ("notin", r"\texttt{NOT\,IN}"),
            ("join", "join"), ("agg", "aggr.")]
    split_col = {"S0": "#9d9b96", "S1": C_UNK, "S2": C_ABS}
    split_name = {"S0": r"no \texttt{NULL}", "S1": r"\texttt{UNKNOWN}", "S2": "absorbed"}
    fam_col = {"where": "#41566e", "check": "#566c85", "notin": "#6b829b", "join": "#7f96ad", "agg": "#93a8bd",
               "trap": "#8c7a68", "control": "#a8998a"}
    cell_col = {"S0": "#c4c2bd", "S1": "#8fb6ea", "S2": "#f4a988"}
    total = n_pred + n_write
    seg = []                     # (ring, start_deg, sweep_deg, colour, label, text colour)

    def put(ring, a0, n, col, lab, tc="white"):
        sw = 360.0 * n / total
        seg.append((ring, a0 - sw, sw, col, lab, tc))
        return a0 - sw

    put(0, 90.0, n_pred, "#2c3a4a", r"\textbf{prediction}\enspace " + f"{n_pred}")
    put(0, 90.0 - 360.0 * n_pred / total, n_write, "#6d5c4b", r"\textbf{" + f"{n_write}" + "}")
    a1 = 90.0
    for fam, lab in fams:
        a2 = a1
        for sp in ("S0", "S1", "S2"):
            if not split_n[(fam, sp)]:
                continue
            nm = r"\textsf{" + sp + "}"
            big = split_n[(fam, sp)] >= 100
            put(2, a2, split_n[(fam, sp)], split_col[sp],
                nm + (r"\enspace " + split_name[sp] if big else "") + r"\enspace " + f"{split_n[(fam, sp)]}")
            a3 = a2
            for (f_, s_, d, r), n in sorted(cell_n.items(), key=lambda kv: kv[0][2]):
                if f_ == fam and s_ == sp:
                    lab3 = (f"d{d}" + r"$\times$" + f"{r}") if d else f"{r} rows"
                    a3 = put(3, a3, n, cell_col[sp], lab3, INK)
            a2 -= 360.0 * split_n[(fam, sp)] / total
        a1 = put(1, a1, fam_n[fam], fam_col[fam], lab + r"\enspace " + f"{fam_n[fam]}")
    for kind in ("trap", "control"):
        n = n_write // 2
        put(2, a1, n, "#cbbfb2", "5+5", INK)
        put(3, a1, n, "#e4dbd1", "DBs", INK)
        a1 = put(1, a1, n, fam_col[kind], f"{n}")

    # ---- labels: along the ring they are set glyph by glyph on the arc; across the ring they stay straight
    import re as _re
    renderer = f.canvas.get_renderer()
    _wcache = {}
    PT = 25.4 / 72.0                                      # mm per point

    def glyphs(lab):
        """Split the label markup into glyph tokens: (markup, is_space, space_em)."""
        out = []
        for m in _re.finditer(r"\\(texttt|textsf|textbf)\{([^}]*)\}|\$\\times\$|\\enspace\s?|\\,|.", lab):
            tok = m.group(0)
            if m.group(1):
                for part in _re.split(r"(\\,)", m.group(2)):
                    if part == r"\,":
                        out.append(("", True, 0.17))
                    else:
                        out += [("\\" + m.group(1) + "{" + c + "}", False, 0) if c != " " else ("", True, 0.3)
                                for c in part]
            elif tok.startswith(r"\enspace"):
                out.append(("", True, 0.5))
            elif tok == r"\,":
                out.append(("", True, 0.17))
            elif tok == " ":
                out.append(("", True, 0.3))
            else:
                out.append((tok, False, 0))
        return out

    def gw(tok, size):                                   # advance width of one glyph in mm
        mk, sp, em = tok
        if sp:
            return em * size * PT
        key = (mk, size)
        if key not in _wcache:
            tt = L.text(0, 0, mk, fontsize=size)
            _wcache[key] = tt.get_window_extent(renderer).width / f.dpi * 25.4
            tt.remove()
        return _wcache[key]

    def arc_text(lab, rr, mid_deg, size, color):
        toks = glyphs(lab)
        ws = [gw(t, size) for t in toks]
        total_w = sum(ws)
        top = math.sin(math.radians(mid_deg)) >= 0
        sgn = -1.0 if top else 1.0                        # top half reads clockwise, bottom half counter-clockwise
        r_base = rr - 0.30 * size * PT if top else rr + 0.30 * size * PT   # centre the x-height on the ring
        a = math.radians(mid_deg) - sgn * (total_w / 2) / r_base
        for t, w in zip(toks, ws):
            ac = a + sgn * (w / 2) / r_base
            if not t[1]:
                rot = math.degrees(ac) - 90 if top else math.degrees(ac) + 90
                L.text(cx + r_base * math.cos(ac), cy + r_base * math.sin(ac), t[0], fontsize=size, color=color,
                       ha="center", va="baseline", rotation=rot, rotation_mode="anchor", zorder=5)
            a += sgn * w / r_base
        return total_w

    for ring, a0, sw, col, lab, tc in seg:
        r_in, r_out = rings[ring] * R, rings[ring + 1] * R
        L.add_patch(Wedge((cx, cy), r_out, a0, a0 + sw, width=r_out - r_in, fc=col, ec="white", lw=0.7, zorder=3))
        if not lab:
            continue
        mid = a0 + sw / 2
        rr = (r_in + r_out) / 2
        size = (6.2, 5.4, 4.9, 4.6)[ring]
        arc = rr * math.radians(sw)
        thick = r_out - r_in
        lw_ = sum(gw(t, size) for t in glyphs(lab))
        if lw_ <= 0.90 * arc:                             # along the ring, following its curve
            arc_text(lab, rr, mid, size, tc)
        else:                                             # across the ring (narrow segments), straight and radial
            assert lw_ <= 1.10 * thick, (ring, lab, lw_, thick)
            rot = mid if math.cos(math.radians(mid)) >= 0 else mid + 180
            L.text(cx + rr * math.cos(math.radians(mid)), cy + rr * math.sin(math.radians(mid)), lab, fontsize=size,
                   color=tc, ha="center", va="center", rotation=rot, rotation_mode="anchor", zorder=5)
    # what the writing wedges are
    wa = math.radians(90.0 - 360.0 * (n_pred + n_write / 2) / total)
    ex, ey = cx + 1.02 * R * math.cos(wa), cy + 1.02 * R * math.sin(wa)
    L.plot([ex, ex + 3.0], [ey, ey + 2.2], color=INK2, lw=0.5, zorder=4)
    txt(ex + 3.4, ey + 3.6, r"query writing: " + f"{n_write // 2} traps + {n_write // 2} controls", size=5.0, c=INK2)
    txt(ex + 3.4, ey + 1.2, r"each on 5 \texttt{NULL} + 5 \texttt{NULL}-free DBs", size=5.0, c=INK2)
    # filled centre disc
    from matplotlib.patches import Circle
    r0 = rings[0] * R
    L.add_patch(Circle((cx, cy), r0, fc="#f3e4d4", ec="white", lw=0.7, zorder=3))
    txt(cx, cy + 3.6, r"\textbf{" + f"{total}" + "}", size=12, ha="center")
    txt(cx, cy - 0.6, "formal items", size=5.6, ha="center")
    txt(cx, cy - 3.6, f"{n_pred} + {n_write}", size=4.8, ha="center", c=INK2)
    # ring legend
    for k, (sp, lab) in enumerate((("S0", r"\textsf{S0} no \texttt{NULL}"), ("S1", r"\textsf{S1} \texttt{UNKNOWN}"),
                                   ("S2", r"\textsf{S2} absorbed"))):
        lx = 55.5 + k * 19.0
        L.add_patch(FancyBboxPatch((lx, 2.0), 2.0, 2.0, boxstyle="round,pad=0,rounding_size=0.4",
                                   fc=split_col[sp], lw=0, zorder=4))
        txt(lx + 2.8, 3.0, lab, size=5.8)
    txt(cx, 6.6, r"rings, inside out: task, family, split, condition depth $\times$ rows", size=5.2, ha="center",
        c=INK2)

    # ------------------------------------------------------------------ right column: statistics
    xr, wr = 115.2, W - 115.2 - 0.4
    # A. gold answers
    ya, ha = 66.0, 17.6
    titled(xr, ya, wr, ha, r"\textbf{Gold answers}\enspace executed, not annotated")
    eng = N["engines"]
    chips = [("PostgreSQL " + eng["postgres"]), ("SQLite " + eng["sqlite"]), ("DuckDB " + eng["duckdb"]),
             r"ref.\ evaluator"]
    cxs = xr + 2.0
    for k, c in enumerate(chips):
        cw = (wr - 4.0 - 3 * 1.4) / 4
        box(cxs + k * (cw + 1.4), ya + 6.4, cw, 5.0, "white", EDGE, r=1.0, lw=0.5, z=3)
        txt(cxs + k * (cw + 1.4) + cw / 2, ya + 8.9, c, size=5.2, ha="center")
    txt(xr + 2.0, ya + 3.0, r"all four agree on \textbf{3{,}700 of 3{,}700} generated items; none dropped", size=5.9)

    # B. rows by category
    yb, hb = 46.4, 18.0
    titled(xr, yb, wr, hb, r"\textbf{Every row labelled}\enspace \texttt{WHERE} + \texttt{CHECK} items")
    cw = (wr - 4.0 - 2 * 1.4) / 3
    for k, (cat, col, lab, sub) in enumerate((("unknown", C_UNK, "unknown", r"\textsf{sql} = \textsf{wk}"),
                                              ("absorbing", C_ABS, "absorbing", r"\textsf{sql} $\neq$ \textsf{wk}"),
                                              ("classical", C_CLS, "classical", "all 5 agree"))):
        bx = xr + 2.0 + k * (cw + 1.4)
        box(bx, yb + 2.0, cw, 10.6, "white", EDGE, r=1.0, lw=0.5, z=3)
        L.add_patch(plt.Rectangle((bx + 1.6, yb + 8.6), 1.6, 2.4, fc=col, lw=0, zorder=4))
        txt(bx + 4.0, yb + 9.8, lab, size=6.0)
        txt(bx + 1.6, yb + 5.8, r"\textbf{" + f"{cats[cat]:,}".replace(",", "{,}") + "}", size=8.4)
        txt(bx + cw - 1.4, yb + 3.6, sub, size=5.2, c=INK2, ha="right")

    # C. evaluation + result
    yc, hc = 0.4, 44.4
    titled(xr, yc, wr, hc, r"\textbf{Rows misread}\enspace five Claude models, " + f"{N['requests']:,}".replace(",", "{,}")
           + r" responses")
    txt(xr + 2.0, yc + 35.0, r"\textbf{" + f"{unk_e}" + r"\,/\," + f"{unk_n:,}".replace(",", "{,}") + "}", size=11,
        c=C_UNK)
    txt(xr + 2.0, yc + 31.2, "unknown rows", size=5.8, c=INK2)
    txt(xr + 31.0, yc + 35.0, r"\textbf{" + f"{abs_e}" + r"\,/\," + f"{abs_n:,}".replace(",", "{,}") + "}", size=11,
        c=C_ABS)
    txt(xr + 31.0, yc + 31.2, "absorbing rows", size=5.8, c=INK2)
    # per-model bars, WHERE + CHECK pooled
    order = ["Haiku 5.5", "Sonnet 5.5", "Opus 5.5", "Fable 5.1", "Fable 5.2"]
    bx0, bw, xmax = xr + 15.0, wr - 26.0, 0.16
    for k, name in enumerate(order):
        yy = yc + 26.4 - k * 4.7
        ue = sum(t[f"{fam}|{name}"]["unk_counts"][0] for fam in ("where", "check") if f"{fam}|{name}" in t)
        un = sum(t[f"{fam}|{name}"]["unk_counts"][1] for fam in ("where", "check") if f"{fam}|{name}" in t)
        ae = sum(t[f"{fam}|{name}"]["abs_counts"][0] for fam in ("where", "check") if f"{fam}|{name}" in t)
        an = sum(t[f"{fam}|{name}"]["abs_counts"][1] for fam in ("where", "check") if f"{fam}|{name}" in t)
        txt(xr + 2.0, yy, name, size=6.0)
        L.add_patch(plt.Rectangle((bx0, yy + 0.25), max(bw * (ae / an) / xmax, 0.35), 1.45, fc=C_ABS, lw=0, zorder=4))
        L.add_patch(plt.Rectangle((bx0, yy - 1.75), max(bw * (ue / un) / xmax, 0.35), 1.45, fc=C_UNK, lw=0, zorder=4))
        txt(bx0 + bw * (ae / an) / xmax + 1.0, yy + 0.95, f"{ae}/{an}", size=5.0, c=INK2)
        txt(bx0 + max(bw * (ue / un) / xmax, 0.35) + 1.0, yy - 1.05, f"{ue}/{un}", size=5.0, c=INK2)
    for v in (0, 0.05, 0.10, 0.15):
        xx = bx0 + bw * v / xmax
        L.plot([xx, xx], [yc + 5.0, yc + 28.6], color=GRID, lw=0.4, zorder=2)
        txt(xx, yc + 2.9, f"{int(v * 100)}\\%", size=5.2, c=INK2, ha="center")
    f.savefig(HERE / "figs" / "teaser.pdf", pad_inches=0)
    f.savefig(HERE / "figs" / "teaser_preview.png", dpi=300, pad_inches=0)
    plt.close(f)


def make_fig(fig):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter
    style(plt)
    cats = [("unknown", "unknown rows (S1 items)", C_UNK, "o"),
            ("absorbing", "absorbing rows (S2 items)", C_ABS, "D"),
            ("classical_S2", "classical rows of S2 items", C_CLS, "s"),
            ("S0", "rows of S0 items (no NULL)", C_S0, "^")]
    names = [n for _, n in MODELS[:4]]
    f, axes = plt.subplots(1, 4, figsize=(7.0, 1.9), sharey=True)
    for ax, name in zip(axes, names):
        for j, (c, lab, col, mk) in enumerate(cats):
            xs, ys, lo, hi = [], [], [], []
            for i, cell in enumerate(CELLS):
                v = fig[f"{name}|{cell}"][c]
                if v[0] is None or fig[f"{name}|{cell}"]["missing"] > 0.2:
                    continue
                xs.append(i + (j - 1.5) * 0.15)
                ys.append(v[0])
                lo.append(v[0] - v[1])
                hi.append(v[2] - v[0])
            ax.errorbar(xs, ys, yerr=[lo, hi], fmt=mk, color=col, ms=3.8, mec="white", mew=0.5, lw=1.0,
                        capsize=0, label=lab, zorder=3)
        ax.set_xticks(range(3))
        ax.set_xticklabels(["depth 8\n12 rows", "depth 32\n6 rows", "depth 64\n6 rows"])
        ax.set_xlim(-0.5, 2.5)
        ax.set_title(name, fontsize=7.5, color=INK, pad=3)
        ax.grid(axis="y", lw=0.4, color=GRID)
        ax.set_axisbelow(True)
        ax.tick_params(axis="x", length=0)
        if name == "Haiku 5.5":
            ax.text(2, 0.10, r"\textit{not testable:}" + "\n" + r"\textit{25--45\% truncated}", ha="center", fontsize=6.2, color=INK2)
    axes[0].set_ylabel("row error rate", color=INK)
    axes[0].set_ylim(-0.01, 0.5)
    axes[0].yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    h, l = axes[0].get_legend_handles_labels()
    f.legend(h, l, loc="upper center", ncol=4, frameon=False, bbox_to_anchor=(0.5, 1.12), fontsize=7,
             handletextpad=0.3, columnspacing=1.5)
    f.tight_layout(pad=0.3)
    f.savefig(HERE / "figs" / "row_errors.pdf", bbox_inches="tight", pad_inches=0.01)
    plt.close(f)


if __name__ == "__main__":
    main()
