"""Task B v2: questions that never mention missing values. The NULL hazard is implicit in the intent.

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
    # ORDER BY ... DESC puts NULLs first in PostgreSQL; ASC puts them last
    ("top_bonus", "trap",
     "List the ids of the three employees with the highest bonuses, highest first. Break ties by smaller id.",
     "SELECT id FROM employees WHERE bonus IS NOT NULL ORDER BY bonus DESC, id LIMIT 3",
     "SELECT id FROM employees ORDER BY bonus DESC, id LIMIT 3", True),
    ("low_bonus", "control",
     "List the ids of the three employees with the lowest bonuses, lowest first. Break ties by smaller id.",
     "SELECT id FROM employees WHERE bonus IS NOT NULL ORDER BY bonus, id LIMIT 3",
     "SELECT id FROM employees ORDER BY bonus, id LIMIT 3", True),
    # NOT IN over a nullable column returns nothing; IN is unaffected
    ("not_manager", "trap",
     "List the ids of employees who do not manage anyone.",
     "SELECT e.id FROM employees e WHERE NOT EXISTS (SELECT 1 FROM employees s WHERE s.manager_id = e.id)",
     "SELECT id FROM employees WHERE id NOT IN (SELECT manager_id FROM employees)", False),
    ("is_manager", "control",
     "List the ids of employees who manage at least one employee.",
     "SELECT e.id FROM employees e WHERE EXISTS (SELECT 1 FROM employees s WHERE s.manager_id = e.id)",
     "SELECT id FROM employees WHERE id IN (SELECT manager_id FROM employees)", False),
    # universal condition: NOT EXISTS (... <= k) lets a NULL bonus through; the existential twin is unaffected
    ("all_above", "trap",
     "List the ids of departments that have at least one employee and in which every employee has a bonus above {k}.",
     "SELECT d.id FROM departments d WHERE EXISTS (SELECT 1 FROM employees e WHERE e.dept_id = d.id) "
     "AND NOT EXISTS (SELECT 1 FROM employees e WHERE e.dept_id = d.id AND (e.bonus IS NULL OR e.bonus <= {k}))",
     "SELECT d.id FROM departments d WHERE EXISTS (SELECT 1 FROM employees e WHERE e.dept_id = d.id) "
     "AND NOT EXISTS (SELECT 1 FROM employees e WHERE e.dept_id = d.id AND e.bonus <= {k})", False),
    ("some_above", "control",
     "List the ids of departments in which at least one employee has a bonus above {k}.",
     "SELECT d.id FROM departments d WHERE EXISTS (SELECT 1 FROM employees e WHERE e.dept_id = d.id AND e.bonus > {k})",
     "SELECT id FROM departments WHERE id IN (SELECT dept_id FROM employees WHERE bonus > {k})", False),
    # <> drops NULL rows: "not managed by m" includes employees with no manager, "managed by someone other than m" does not
    ("not_managed_by", "trap",
     "List the ids of employees who are not managed by employee {m}.",
     "SELECT id FROM employees WHERE manager_id IS DISTINCT FROM {m}",
     "SELECT id FROM employees WHERE manager_id <> {m}", False),
    ("managed_by_other", "control",
     "List the ids of employees who are managed by someone other than employee {m}.",
     "SELECT id FROM employees WHERE manager_id IS NOT NULL AND manager_id <> {m}",
     "SELECT id FROM employees WHERE manager_id <> {m}", False),
    ("not_in_dept", "trap",
     "List the ids of employees who do not work in department {d}.",
     "SELECT id FROM employees WHERE dept_id IS DISTINCT FROM {d}",
     "SELECT id FROM employees WHERE dept_id <> {d}", False),
    ("in_other_dept", "control",
     "List the ids of employees who work in a department other than department {d}.",
     "SELECT id FROM employees WHERE dept_id IS NOT NULL AND dept_id <> {d}",
     "SELECT id FROM employees WHERE dept_id <> {d}", False),
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
