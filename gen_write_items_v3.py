"""Task B v3: like v2 (no hints about missing values), but each NULL hazard sits inside a compound condition.
v2 (single-hazard idioms) also put every model at ceiling; v3 is the last pilot of task B.

Pilot W (v1) put every model at ceiling: each v1 trap question spelled out how missing values should be treated
("Employees without a recorded bonus should be included."). v2 drops those hints. Every question is still
unambiguous in plain English (an employee who has no manager is not managed by employee 2), and each trap is paired
with a control of the same shape where the naive query is already right.

Same schema, instance generator and test-suite scoring as v1 (gen_write_items.py, exec_write_gold.py).
  python gen_write_items_v2.py <out_dir>    -> <out_dir>/items_write_raw.jsonl
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

from gen_write_items import gen_instance, to_sql

# (name, kind, question, gold, naive, ordered)
TEMPLATES = [
    # negation over a compound condition with two nullable columns
    ("not_both", "trap",
     "List the ids of employees who are not both in department {d} and paid a bonus above {k}.",
     "SELECT id FROM employees WHERE dept_id IS DISTINCT FROM {d} OR bonus IS NULL OR bonus <= {k}",
     "SELECT id FROM employees WHERE NOT (dept_id = {d} AND bonus > {k})", False),
    ("either", "control",
     "List the ids of employees who are in department {d} or are paid a bonus above {k}.",
     "SELECT id FROM employees WHERE COALESCE(dept_id = {d}, FALSE) OR COALESCE(bonus > {k}, FALSE)",
     "SELECT id FROM employees WHERE dept_id = {d} OR bonus > {k}", False),
    ("neither", "trap",
     "List the ids of employees who neither work in department {d} nor are managed by employee {m}.",
     "SELECT id FROM employees WHERE dept_id IS DISTINCT FROM {d} AND manager_id IS DISTINCT FROM {m}",
     "SELECT id FROM employees WHERE NOT (dept_id = {d} OR manager_id = {m})", False),
    ("dept_or_mgr", "control",
     "List the ids of employees who work in department {d} or are managed by employee {m}.",
     "SELECT id FROM employees WHERE COALESCE(dept_id = {d}, FALSE) OR COALESCE(manager_id = {m}, FALSE)",
     "SELECT id FROM employees WHERE dept_id = {d} OR manager_id = {m}", False),
    # NOT IN whose subquery column is nullable although the filter is on another column
    ("no_high_bonus_dept", "trap",
     "List the ids of departments in which no employee has a bonus above {k}.",
     "SELECT d.id FROM departments d WHERE NOT EXISTS (SELECT 1 FROM employees e WHERE e.dept_id = d.id AND e.bonus > {k})",
     "SELECT id FROM departments WHERE id NOT IN (SELECT dept_id FROM employees WHERE bonus > {k})", False),
    ("high_bonus_dept", "control",
     "List the ids of departments in which some employee has a bonus above {k}.",
     "SELECT d.id FROM departments d WHERE EXISTS (SELECT 1 FROM employees e WHERE e.dept_id = d.id AND e.bonus > {k})",
     "SELECT id FROM departments WHERE id IN (SELECT dept_id FROM employees WHERE bonus > {k})", False),
    # NULL on the left of NOT IN
    ("not_mgd_from_dept", "trap",
     "List the ids of employees who are not managed by anyone who works in department {d}.",
     "SELECT e.id FROM employees e WHERE NOT EXISTS (SELECT 1 FROM employees m WHERE m.id = e.manager_id AND m.dept_id = {d})",
     "SELECT id FROM employees WHERE manager_id NOT IN (SELECT id FROM employees WHERE dept_id = {d})", False),
    ("mgd_from_dept", "control",
     "List the ids of employees who are managed by someone who works in department {d}.",
     "SELECT e.id FROM employees e WHERE EXISTS (SELECT 1 FROM employees m WHERE m.id = e.manager_id AND m.dept_id = {d})",
     "SELECT id FROM employees WHERE manager_id IN (SELECT id FROM employees WHERE dept_id = {d})", False),
]


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(int(sys.argv[2]) if len(sys.argv) > 2 else 20261118)
    items = []
    for name, kind, q, gold, naive, ordered in TEMPLATES:
        for v in range(3):
            params = {"k": rng.choice([100, 200, 300]), "m": rng.choice([1, 2, 3]), "d": rng.choice([1, 2, 3])}
            fmt = lambda s: s.format(**params)
            suite = []
            for j in range(120):
                seed = rng.randrange(10**9)
                suite.append({"seed": seed, "null": to_sql(*gen_instance(random.Random(seed), True)),
                              "clean": to_sql(*gen_instance(random.Random(seed), False))})
            items.append({"id": f"{name}-v{v}", "template": name, "kind": kind, "question": fmt(q),
                          "gold_sql": fmt(gold), "naive_sql": fmt(naive), "ordered": ordered,
                          "params": params, "candidates": suite})
    with (out / "items_write_raw.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for it in items:
            f.write(json.dumps(it) + "\n")
    print(len(items), "items")


if __name__ == "__main__":
    main()
