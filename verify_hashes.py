"""Check every hash recorded in the preregistration locks.
  python verify_hashes.py
Files edited after a lock are checked against their locked copies (locked_v1/, locked_a1/); see README.
Three files had one line edited before release (REDACTIONS.json); for them the released hash is checked and
the edit is reported, while the locked hash in the .sha256 file is left as it was.
"""
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCKS = {"PREREG_VLDB.sha256": "locked_v1", "PREREG_VLDB_A1.sha256": "locked_a1"}
REDACTIONS = json.loads((HERE / "REDACTIONS.json").read_text(encoding="utf-8"))["files"]


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def check(rel, h):
    """Status of file `rel` (relative to HERE) against locked hash `h`, or None."""
    p = HERE / rel
    if not p.exists():
        return None
    if sha(p) == h:
        return "ok"
    r = REDACTIONS.get(Path(rel).as_posix())
    if r and r["locked_sha256"] == h and sha(p) == r["released_sha256"]:
        return f"ok (line {', '.join(map(str, r['lines']))} edited before release; see REDACTIONS.json)"
    return None


bad = 0
for lock, locked_dir in LOCKS.items():
    for line in (HERE / lock).read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        h, name = line.split(maxsplit=1)
        name = name.lstrip("*")
        status = check(name, h)
        if status is None:
            alt = check(f"{locked_dir}/{Path(name).name}", h)
            if alt is not None:
                status = f"{alt} [edited later; locked copy {locked_dir}/{Path(name).name}]"
        if status is None:
            status = "MISMATCH"
            bad += 1
        print(f"{lock:24s} {name:40s} {status}")
print("all hashes match" if not bad else f"{bad} mismatches")
sys.exit(1 if bad else 0)
