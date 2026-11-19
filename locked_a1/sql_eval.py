"""SQL NULL benchmark: run engine-gold items through the Claude Message Batches API with a hard spend cap.

  python sql_eval.py plan   <spec.json>      # count requests + projected cost (no API call)
  python sql_eval.py submit <spec.json>      # refuses if ledger + margin x projection would pass the cap
  python sql_eval.py fetch  <run_name>       # poll until ended, save raw results, book actual cost
  python sql_eval.py score  <run_name>       # parse answers, compare with gold and with the candidate semantics
  python sql_eval.py ledger

Key from ANTHROPIC_API_KEY. Spend is booked from each response's usage at batch prices (50% of list).
No refusal fallbacks: a fallback would answer with a different model. Refusals are their own outcome.
The cap covers this benchmark only; the earlier K3 runs are in ../spend_ledger.json and are added to the
printed credit total.
"""
from __future__ import annotations

import collections
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs"
POOL = HERE / "pool" / "sql_pool_v0_gold.jsonl"
LEDGER = HERE / "spend_ledger_vldb.json"
K3_LEDGER = HERE.parent / "spend_ledger.json"
CAP_USD = 45.0                     # hard spending cap for this benchmark, ~$30 expected

PRICE = {  # list $/MTok (input, output)
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-fable-5-1": (10.00, 50.00),
    "claude-fable-5-2": (8.00, 40.00),       # added for amendment A1 (2026-11-17)
}
BATCH_DISCOUNT = 0.5
MAX_TOKENS = 64000                 # generous on purpose: truncation must not be the bottleneck again

# ---------------------------------------------------------------- prompts (frozen)
SYSTEM = {
    "plain": "You are an expert in SQL.",
    "rules": (
        "You are an expert in SQL. Recall how SQL treats NULL:\n"
        "- A comparison with NULL (=, <>, <, >, <=, >=) yields UNKNOWN.\n"
        "- NOT UNKNOWN is UNKNOWN. FALSE AND x is FALSE and TRUE OR x is TRUE for any x; otherwise AND/OR with an "
        "UNKNOWN operand yield UNKNOWN.\n"
        "- WHERE and JOIN ... ON keep a row only when the condition is TRUE.\n"
        "- A CHECK constraint rejects a row only when the condition is FALSE.\n"
        "- x NOT IN (subquery) is UNKNOWN when x matches no value of the subquery and the subquery returns a NULL.\n"
        "- SUM, MAX and other aggregates except COUNT(*) ignore NULL inputs and return NULL when every input is NULL."
    ),
}
ANSWER_FMT = {
    "ids": ("End your reply with a final line of the form 'Answer: [1, 4]' that lists the returned id values in "
            "order, or 'Answer: []' if no rows are returned."),
    "join": ("End your reply with a final line of the form 'Answer: [(1, 11), (2, 13)]' that lists the returned "
             "(r.id, s.sid) rows in order, or 'Answer: []' if no rows are returned."),
    "agg": ("End your reply with a final line of the form 'Answer: [(1, 5), (2, 7)]' that lists the returned "
            "(grp, value) rows in order, writing NULL for a null value."),
}


def user_prompt(it) -> str:
    stmts = "\n".join(it["ddl"] + it["inserts"])
    fmt = ANSWER_FMT["ids" if it["answer_kind"] == "ids" else ("join" if it["family"] == "join" else "agg")]
    return ("The following statements are run, in order, on an empty PostgreSQL database. If a statement fails "
            "with an error, it has no effect and the remaining statements still run.\n\n"
            f"{stmts}\n\nThen this query is run:\n\n{it['query']}\n\nWhat does the query return?\n\n{fmt}")


# ---------------------------------------------------------------- parsing
ANS_LINE = re.compile(r"answer\s*[:：]\s*\**\s*(\[[^\]\n]*\])", re.I)


def parse_answer(text, kind):
    m = ANS_LINE.findall(text or "")
    if not m:
        return None
    s = m[-1]
    tok = lambda x: None if x.strip().upper() in ("NULL", "NONE") else int(x)
    try:
        if kind == "ids":
            inner = s.strip("[] ")
            return [tok(x) for x in inner.split(",")] if inner else []
        tups = re.findall(r"\(([^)]*)\)", s)
        if not tups and s.strip("[] ") == "":
            return []
        return [[tok(x) for x in t.split(",")] for t in tups]
    except ValueError:
        return None


# ---------------------------------------------------------------- spec -> requests
def load_pool(path=None):
    p = HERE / path if path else POOL
    return [json.loads(l) for l in p.open(encoding="utf-8")]


def build_requests(spec):
    items = load_pool(spec.get("pool"))
    cells = collections.defaultdict(list)
    for it in items:
        cells[(it["family"], it["split"], it["depth"], it.get("rows", 6))].append(it)
    reqs = []
    for arm in spec["arms"]:
        if arm.get("task") == "write":
            reqs += build_write_requests(arm)
            continue
        for (fam, split, depth, _rows), lst in sorted(cells.items()):
            if fam not in arm["families"] or split not in arm["splits"]:
                continue
            if "depths" in arm and depth not in arm["depths"]:
                continue
            if "cells" in arm and [depth, _rows] not in arm["cells"]:
                continue
            for it in lst[arm["offset"]: arm["offset"] + arm["n"]]:
                for model in arm["models"]:
                  for eff in (arm["efforts"] if "efforts" in arm else [arm["effort"]]):
                    for cond in arm.get("conditions", ["plain"]):
                        cid = re.sub(r"[^a-zA-Z0-9_-]", "_", f"{arm['name']}|{model[7:]}|{eff[0]}|{cond[0]}|{it['id']}")[:64]
                        reqs.append({
                            "custom_id": cid,
                            "meta": {"arm": arm["name"], "model": model, "effort": eff, "cond": cond,
                                     "item": it["id"], "family": fam, "split": split, "depth": depth,
                                     "rows": it.get("rows", 6)},
                            "params": {"model": model, "max_tokens": MAX_TOKENS,
                                       "output_config": {"effort": eff},
                                       "system": SYSTEM[cond],
                                       "messages": [{"role": "user", "content": user_prompt(it)}]},
                        })
    ids = [r["custom_id"] for r in reqs]
    assert len(ids) == len(set(ids)), "custom_id collision"
    return reqs


WRITE_POOL = HERE / "pool" / "write" / "items_write.jsonl"


SYSTEM["bare"] = SYSTEM["plain"]   # task B: same system prompt, schema shown without its NULL comments


def write_prompt(it, cond="plain"):
    from gen_write_items import SCHEMA
    schema = SCHEMA if cond != "bare" else re.sub(r"[ \t]*--[^\n]*", "", SCHEMA)
    return ("Here is a PostgreSQL schema:\n\n" + schema +"\n\nWrite one PostgreSQL query that answers the question "
            "below. The query will be run on databases with this schema, whatever rows they contain.\n\n"
            f"Question: {it['question']}\n\nEnd your reply with the query alone in a ```sql code block.")


def build_write_requests(arm):
    reqs = []
    pool = HERE / arm["write_pool"] if arm.get("write_pool") else WRITE_POOL
    items = [json.loads(l) for l in pool.open(encoding="utf-8")]
    for it in items:
        if arm.get("variants") is not None and int(it["id"].rsplit("-v", 1)[1]) not in arm["variants"]:
            continue
        for model in arm["models"]:
            for eff in arm["efforts"]:
                for cond in arm.get("conditions", ["plain"]):
                    cid = re.sub(r"[^a-zA-Z0-9_-]", "_", f"{arm['name']}|{model[7:]}|{eff[0]}|{cond[0]}|{it['id']}")[:64]
                    reqs.append({"custom_id": cid,
                                 "meta": {"arm": arm["name"], "task": "write", "model": model, "effort": eff, "cond": cond,
                                          "item": it["id"], "family": it["template"], "split": it["kind"], "depth": 0},
                                 "params": {"model": model, "max_tokens": MAX_TOKENS, "output_config": {"effort": eff},
                                            "system": SYSTEM[cond],
                                            "messages": [{"role": "user", "content": write_prompt(it, cond)}]}})
    return reqs


# rough output-token guesses until the pilot gives measured ones (K3 run, comparable workload)
GUESS_OUT = {"claude-haiku-5-5": 20000, "claude-sonnet-5-5": 2500, "claude-opus-5-5": 1500, "claude-fable-5-1": 3000,
             "claude-fable-5-2": 1500}  # A1: Fable 5.1 measured mean 1,403 on formal_v1


def project_cost(reqs, profile):
    total = 0.0
    for r in reqs:
        m = r["meta"]["model"]
        key = f"{m}|{r['meta']['family']}|{r['meta']['depth']}"
        out = min(profile.get(key) or profile.get(m) or GUESS_OUT[m], MAX_TOKENS)
        pi, po = PRICE[m]
        total += BATCH_DISCOUNT * (450 * pi + out * po) / 1e6
    return total


def ledger():
    return json.loads(LEDGER.read_text()) if LEDGER.exists() else {"runs": {}, "total_usd": 0.0}


def save_ledger(L):
    L["total_usd"] = round(sum(v.get("actual_usd", 0) for v in L["runs"].values()), 4)
    LEDGER.write_text(json.dumps(L, indent=2))


def credit_used():
    k3 = json.loads(K3_LEDGER.read_text())["total_usd"] if K3_LEDGER.exists() else 0.0
    return k3 + ledger()["total_usd"]


def make_client():
    import anthropic
    # explicit host and key, so that proxy variables in the environment are ignored
    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], auth_token=None,
                               base_url="https://api.anthropic.com")


# ---------------------------------------------------------------- commands
def cmd_plan(spec_path):
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    reqs = build_requests(spec)
    prof = HERE / "token_profile_sql.json"
    proj = project_cost(reqs, json.loads(prof.read_text()) if prof.exists() else {})
    by = collections.Counter(r["meta"]["model"] for r in reqs)
    print(f"{spec['name']}: {len(reqs)} requests {dict(by)}, projected ${proj:.2f}; "
          f"benchmark ledger ${ledger()['total_usd']:.2f} / cap ${CAP_USD:.2f}; gift credit used so far ${credit_used():.2f}")
    return spec, reqs, proj


def cmd_submit(spec_path):
    spec, reqs, proj = cmd_plan(spec_path)
    L = ledger()
    if spec["name"] in L["runs"]:
        sys.exit(f"run {spec['name']} already submitted")
    margin = spec.get("projection_margin", 1.3)
    if L["total_usd"] + proj * margin > CAP_USD:
        sys.exit(f"REFUSED: ledger {L['total_usd']:.2f} + projection {proj:.2f} x {margin} > cap {CAP_USD}")
    client = make_client()
    run_dir = RUNS / spec["name"]
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "spec.json").write_text(json.dumps(spec, indent=2))
    with (run_dir / "requests.jsonl").open("w", encoding="utf-8") as fh:
        for r in reqs:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    b = client.messages.batches.create(requests=[{"custom_id": r["custom_id"], "params": r["params"]} for r in reqs])
    L["runs"][spec["name"]] = {"batch_ids": [b.id], "n": len(reqs), "projected_usd": round(proj, 4),
                               "submitted": datetime.now(timezone.utc).isoformat()}
    save_ledger(L)
    print("submitted", b.id)


def cmd_fetch(name, poll=60):
    L = ledger()
    run = L["runs"][name]
    client = make_client()
    while True:
        bs = [client.messages.batches.retrieve(b) for b in run["batch_ids"]]
        if all(b.processing_status == "ended" for b in bs):
            break
        c = [b.request_counts for b in bs]
        print(time.strftime("%H:%M:%S"), "processing", sum(x.processing for x in c),
              "done", sum(x.succeeded + x.errored for x in c), flush=True)
        time.sleep(int(poll))
    meta = {json.loads(l)["custom_id"]: json.loads(l)["meta"] for l in (RUNS / name / "requests.jsonl").open(encoding="utf-8")}
    usd = 0.0
    with (RUNS / name / "results.jsonl").open("w", encoding="utf-8") as fh:
        for b in run["batch_ids"]:
            for res in client.messages.batches.results(b):
                rec = {"custom_id": res.custom_id, **meta[res.custom_id], "type": res.result.type}
                if res.result.type == "succeeded":
                    msg = res.result.message
                    u = msg.usage
                    pi, po = PRICE[rec["model"]]
                    cost = BATCH_DISCOUNT * (u.input_tokens * pi + u.output_tokens * po) / 1e6
                    usd += cost
                    sd = getattr(msg, "stop_details", None)
                    rec.update(stop_reason=msg.stop_reason,
                               refusal_category=getattr(sd, "category", None) if sd else None,
                               text="".join(blk.text for blk in msg.content if blk.type == "text"),
                               input_tokens=u.input_tokens, output_tokens=u.output_tokens, usd=cost)
                else:
                    rec["error"] = str(getattr(res.result, "error", ""))
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    L = ledger()          # re-read: another fetch may have written the ledger while this one was polling
    run = L["runs"][name]
    run["actual_usd"] = round(usd, 4)
    run["fetched"] = datetime.now(timezone.utc).isoformat()
    save_ledger(L)
    print(f"{name}: actual ${usd:.2f} (projected ${run['projected_usd']:.2f}); benchmark ledger ${ledger()['total_usd']:.2f}; "
          f"gift credit used ${credit_used():.2f}")


def cmd_score(name):
    spec = json.loads((RUNS / name / "spec.json").read_text())
    items = {it["id"]: it for it in load_pool(spec.get("pool"))}
    rows = []
    for l in (RUNS / name / "results.jsonl").open(encoding="utf-8"):
        r = json.loads(l)
        if r.get("task") == "write":       # task B is scored by execution in score_write.py
            continue
        it = items[r["item"]]
        kind = it["answer_kind"]
        if r["type"] != "succeeded":
            outcome = "error"
            ans = None
        elif r["stop_reason"] == "refusal":
            outcome, ans = "refusal", None
        elif r["stop_reason"] == "max_tokens":
            outcome, ans = "truncated", None
        else:
            ans = parse_answer(r["text"], kind)
            outcome = "unparsed" if ans is None else ("correct" if ans == it["gold"] else "wrong")
        r.update(outcome=outcome, parsed=ans, gold=it["gold"],
                 matches=[m for m in ("sql", "wk", "nf", "nt", "eq") if ans is not None and ans == it["answers"][m]])
        rows.append(r)
    with (RUNS / name / "scored.jsonl").open("w", encoding="utf-8") as fh:
        for r in rows:
            r = {k: v for k, v in r.items() if k != "text"}
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    agg = collections.defaultdict(collections.Counter)
    for r in rows:
        k = (r["model"][7:], r["effort"], r["cond"], r["family"], r["split"], r["depth"], r.get("rows", 6))
        agg[k][r["outcome"]] += 1
        agg[k]["n"] += 1
        if r["outcome"] == "wrong":
            agg[k]["wrong_as_" + ("+".join(r["matches"]) or "other")] += 1
    for k in sorted(agg):
        print(k, dict(agg[k]))
    toks = collections.defaultdict(list)
    for r in rows:
        if r.get("output_tokens"):
            toks[r["model"]].append(r["output_tokens"])
    for m, v in toks.items():
        v = sorted(v)
        print(f"{m}: n={len(v)} mean={sum(v)/len(v):.0f} median={v[len(v)//2]} max={v[-1]}")


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    {"plan": cmd_plan, "submit": cmd_submit, "fetch": cmd_fetch, "score": cmd_score,
     "ledger": lambda: print(json.dumps(ledger(), indent=2), "\ncredit used", credit_used())}[cmd](*args)
