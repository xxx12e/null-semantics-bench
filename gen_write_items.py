"""Task B (query writing): questions over a small schema with nullable columns; scored by execution.

Each template has a natural-language question, a gold query and the common "naive" query a writer would
produce if NULLs did not exist.
  trap     on data with NULLs the naive query returns a different result from the gold query
  control  NULLs are present, but the naive query is still correct (SQL's NULL handling already does the
           right thing, or the NULL-bearing column is not involved)
A model's query is run on a test suite of database instances (Zhong et al. 2020 style):
  null suite   K instances with NULLs in the nullable columns (for traps, every instance separates naive and gold)
  clean suite  the same instances with every NULL replaced by a value (NULL-free twins)
Correct on a suite = the result equals the gold result on every instance (as a multiset, or as a list when the
question asks for an order).

  python gen_write_items.py <out_dir>    -> items_write.jsonl + instances/<template>_<i>.sql
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

SCHEMA = """CREATE TABLE departments (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  budget INTEGER            -- NULL when no budget has been set
);
CREATE TABLE employees (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  dept_id INTEGER REFERENCES departments(id),   -- NULL for employees not assigned to a department
  manager_id INTEGER REFERENCES employees(id),  -- NULL for employees without a manager
  salary INTEGER NOT NULL,
  bonus INTEGER                                 -- NULL when no bonus is recorded
);
CREATE TABLE projects (
  id INTEGER PRIMARY KEY,
  dept_id INTEGER NOT NULL REFERENCES departments(id),
  lead_id INTEGER REFERENCES employees(id)      -- NULL for projects without a lead
);"""

# (name, kind, question, gold, naive, ordered)
TEMPLATES = [
    ("antijoin_dept", "trap",
     "List the ids of departments that have no employees.",
     "SELECT d.id FROM departments d WHERE NOT EXISTS (SELECT 1 FROM employees e WHERE e.dept_id = d.id)",
     "SELECT id FROM departments WHERE id NOT IN (SELECT dept_id FROM employees)", False),
    ("antijoin_lead", "trap",
     "List the ids of employees who do not lead any project.",
     "SELECT e.id FROM employees e WHERE NOT EXISTS (SELECT 1 FROM projects p WHERE p.lead_id = e.id)",
     "SELECT id FROM employees WHERE id NOT IN (SELECT lead_id FROM projects)", False),
    ("antijoin_proj", "control",
     "List the ids of departments that have no projects.",
     "SELECT d.id FROM departments d WHERE NOT EXISTS (SELECT 1 FROM projects p WHERE p.dept_id = d.id)",
     "SELECT id FROM departments WHERE id NOT IN (SELECT dept_id FROM projects)", False),
    ("avg_zero", "trap",
     "What is the average bonus over all employees, counting an employee without a recorded bonus as having a bonus of 0? Return a single value.",
     "SELECT AVG(COALESCE(bonus, 0)) FROM employees",
     "SELECT AVG(bonus) FROM employees", False),
    ("avg_have", "control",
     "What is the average bonus among employees who have a recorded bonus? Return a single value.",
     "SELECT AVG(bonus) FROM employees WHERE bonus IS NOT NULL",
     "SELECT AVG(bonus) FROM employees", False),
    ("neq_incl", "trap",
     "List the ids of employees whose bonus is not {k}. Employees without a recorded bonus should be included.",
     "SELECT id FROM employees WHERE bonus IS DISTINCT FROM {k}",
     "SELECT id FROM employees WHERE bonus <> {k}", False),
    ("neq_have", "control",
     "List the ids of employees who have a recorded bonus that is not {k}.",
     "SELECT id FROM employees WHERE bonus IS NOT NULL AND bonus <> {k}",
     "SELECT id FROM employees WHERE bonus <> {k}", False),
    ("total_zero", "trap",
     "For every employee, return the id and the total pay, which is salary plus bonus, counting a missing bonus as 0.",
     "SELECT id, salary + COALESCE(bonus, 0) FROM employees",
     "SELECT id, salary + bonus FROM employees", False),
    ("total_null", "control",
     "For every employee, return the id and salary plus bonus; the result should be NULL when no bonus is recorded.",
     "SELECT id, CASE WHEN bonus IS NULL THEN NULL ELSE salary + bonus END FROM employees",
     "SELECT id, salary + bonus FROM employees", False),
    ("notmgr_incl", "trap",
     "List the ids of employees who are not managed by employee {m}. Employees without a manager should be included.",
     "SELECT id FROM employees WHERE manager_id IS DISTINCT FROM {m}",
     "SELECT id FROM employees WHERE manager_id <> {m}", False),
    ("same_mgr", "control",
     "List all pairs (a, b) of employee ids with a < b such that both employees have the same manager. Employees without a manager do not share a manager with anyone.",
     "SELECT e1.id, e2.id FROM employees e1 JOIN employees e2 ON e1.manager_id = e2.manager_id AND e1.id < e2.id WHERE e1.manager_id IS NOT NULL",
     "SELECT e1.id, e2.id FROM employees e1 JOIN employees e2 ON e1.manager_id = e2.manager_id AND e1.id < e2.id", False),
    ("budget_low_incl", "trap",
     "List the ids of departments whose budget is not above {b}. Departments without a budget should be included.",
     "SELECT id FROM departments WHERE budget IS NULL OR budget <= {b}",
     "SELECT id FROM departments WHERE NOT (budget > {b})", False),
]


def gen_instance(rng, with_nulls):
    nd, ne, np_ = 4, 9, 5
    deps = [{"id": i, "name": f"D{i}", "budget": rng.choice([100, 200, 300, 400])} for i in range(1, nd + 1)]
    emps = []
    for i in range(1, ne + 1):
        emps.append({"id": i, "name": f"E{i}", "dept_id": rng.randint(1, nd - 1),    # dept nd stays empty
                     "manager_id": rng.choice([1, 2, 3]) if i > 3 else None,
                     "salary": rng.choice([1000, 1200, 1500, 2000]), "bonus": rng.choice([100, 200, 300, 500])})
    projs = [{"id": i, "dept_id": rng.randint(1, nd - 1), "lead_id": rng.randint(1, ne)} for i in range(1, np_ + 1)]
    for e in emps[:3]:                  # top managers: give them a manager in the clean twin only
        e["manager_id"] = None
    if with_nulls:
        for e in emps:
            if rng.random() < 0.3:
                e["dept_id"] = None
            if rng.random() < 0.35:
                e["bonus"] = None
        for d in deps:
            if rng.random() < 0.35:
                d["budget"] = None
        for p in projs:
            if rng.random() < 0.3:
                p["lead_id"] = None
    else:
        for e in emps[:3]:
            e["manager_id"] = rng.choice([x for x in (1, 2, 3) if x != e["id"]])
    return deps, emps, projs


def to_sql(deps, emps, projs):
    lit = lambda v: "NULL" if v is None else (f"'{v}'" if isinstance(v, str) else str(v))
    out = [f"INSERT INTO departments VALUES ({d['id']}, {lit(d['name'])}, {lit(d['budget'])});" for d in deps]
    # employees reference managers: insert managers first (ids 1-3 have no or a low manager)
    out += [f"INSERT INTO employees VALUES ({e['id']}, {lit(e['name'])}, {lit(e['dept_id'])}, NULL, {e['salary']}, {lit(e['bonus'])});" for e in emps]
    out += [f"UPDATE employees SET manager_id = {e['manager_id']} WHERE id = {e['id']};" for e in emps if e["manager_id"] is not None]
    out += [f"INSERT INTO projects VALUES ({p['id']}, {p['dept_id']}, {lit(p['lead_id'])});" for p in projs]
    return out


def main():
    out = Path(sys.argv[1])
    (out / "instances").mkdir(parents=True, exist_ok=True)
    rng = random.Random(20261117)
    items = []
    for name, kind, q, gold, naive, ordered in TEMPLATES:
        for v in range(3):           # three parameter variants per template
            params = {"k": rng.choice([100, 200, 300]), "m": rng.choice([1, 2, 3]), "b": rng.choice([150, 250, 350])}
            fmt = lambda s: s.format(**params)
            suite = []
            for j in range(40):       # candidates; exec_write_gold keeps 5 that meet the template's condition
                seed = rng.randrange(10**9)
                null_inst = to_sql(*gen_instance(random.Random(seed), True))
                clean_inst = to_sql(*gen_instance(random.Random(seed), False))
                suite.append({"seed": seed, "null": null_inst, "clean": clean_inst})
            items.append({"id": f"{name}-v{v}", "template": name, "kind": kind, "question": fmt(q),
                          "gold_sql": fmt(gold), "naive_sql": fmt(naive), "ordered": ordered,
                          "params": params, "candidates": suite})
    with (out / "items_write_raw.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for it in items:
            f.write(json.dumps(it) + "\n")
    print(len(items), "items")


if __name__ == "__main__":
    main()
