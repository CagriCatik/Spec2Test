"""Evaluate the comparison: reliability, quality, retrieval, effort."""
import json, re, sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

OUT = Path(sys.argv[1])
rows = json.loads((OUT / "rows.json").read_text())
runs = {}
for path in (OUT / "runs").glob("*.json"):
    data = json.loads(path.read_text())
    runs[data["run"]["run_id"]] = data["run"]

TRANSPORT = ("Timeout", "RateLimit", "Connection", "Authentication", "MissingApiKey",
             "DeadlineExceeded", "TransientServer", "Redirect")
ANSWER = {"E_INVALID_JSON", "E_TRUNCATED_ANSWER", "E_SCHEMA", "E_SCHEMA_VERSION",
          "E_NO_TEST_CASES", "E_INVALID_TYPE", "E_EMPTY_RESPONSE",
          "E_UNKNOWN_REQUIREMENT_REF", "E_STEP_WITHOUT_EXPECTATION", "E_NO_STEPS",
          "E_EMPTY_FIELD", "E_EMPTY_ACTION", "E_STEP_ORDER", "E_NO_REQUIREMENT_REFS"}
INPUT_ = {"E_INPUT_TOO_LARGE"}


def classify(run):
    """transport / answer / retrieval-or-input, from what the run recorded."""
    errors = [a.get("error") or "" for u in run["units"] for a in u["attempts"]]
    if any(any(t in e for t in TRANSPORT) for e in errors):
        return "transport"
    codes = {i["code"] for u in run["units"] for i in u["issues"]}
    if codes & INPUT_:
        return "input budget"
    if codes & ANSWER:
        return "answer"
    return "other"


def used_context(run):
    """Does a draft mention something only the context supplied?"""
    items = [i for u in run["units"] for i in u["context"]]
    if not items:
        return None
    drafts = " ".join(
        json.dumps(c) for u in run["units"] for c in u["test_cases"]
    ).lower()
    hits = 0
    for item in items:
        tokens = set()
        if item.get("external_id"):
            tokens.add(item["external_id"].lower())
        tokens |= {
            t.lower() for t in re.findall(r"\b[A-Z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b",
                                          item.get("text", ""))
        }
        if any(token in drafts for token in tokens if len(token) > 4):
            hits += 1
    return hits, len(items)


groups = defaultdict(list)
for row in rows:
    groups[row["retrieval"]].append(row)

print(f"{len(rows)} runs over {len({r['requirement'] for r in rows})} requirements\n")
for retrieval, label in ((False, "without retrieval"), (True, "with retrieval")):
    data = groups[retrieval]
    if not data:
        continue
    status = Counter(r["status"] for r in data)
    per_req = defaultdict(list)
    for r in data:
        per_req[r["requirement"]].append(r)
    used = [used_context(runs[r["run_id"]]) for r in data if r["run_id"] in runs]
    used = [u for u in used if u]
    print(f"== {label} ({len(data)} runs) ==")
    print(f"  completed/partial/failed: {status['completed']}/"
          f"{status.get('partial',0)}/{status.get('failed',0)}"
          f"   success rate {status['completed']/len(data):.0%}")
    print(f"  drafts            {mean(r['drafts'] for r in data):5.2f} per run")
    print(f"  open questions    {mean(r['questions'] for r in data):5.2f} per run")
    print(f"  unsupported       {mean(r['unsupported'] for r in data):5.2f} per run")
    print(f"  value from context{mean(r['from_context'] for r in data):5.2f} per run")
    print(f"  no criterion      {mean(r['no_criterion'] for r in data):5.2f} per run")
    print(f"  possible conflict {mean(r['conflicts'] for r in data):5.2f} per run")
    print(f"  context items     {mean(r['context_items'] for r in data):5.2f} per run")
    if used:
        print(f"  context referenced in the draft: "
              f"{sum(h for h,_ in used)}/{sum(t for _,t in used)} items")
    print(f"  model calls       {mean(r['model_calls'] for r in data):5.2f} per run")
    print(f"  repairs           {mean(r['repairs'] for r in data):5.2f} per run")
    print(f"  seconds           {mean(r['seconds'] for r in data):5.1f} per run")
    print()

print("== failures ==")
for row in rows:
    if row["status"] != "completed":
        run = runs.get(row["run_id"])
        kind = classify(run) if run else "unknown"
        codes = sorted({i["code"] for u in run["units"] for i in u["issues"]}) if run else []
        print(f"  {row['requirement']:20} retrieval={str(row['retrieval']):5} "
              f"rep{row['repetition']} -> {row['status']}, {kind}")
        print(f"      codes: {codes}")

print("\n== per requirement (questions / unsupported, mean of 2 runs) ==")
per = defaultdict(dict)
for row in rows:
    key = "with" if row["retrieval"] else "without"
    per[row["requirement"]].setdefault(key, []).append(row)
print(f"  {'requirement':22} {'q no-ret':>9} {'q ret':>7} {'uns no-ret':>11} {'uns ret':>8}")
for requirement in sorted(per):
    a, b = per[requirement].get("without", []), per[requirement].get("with", [])
    if not a or not b:
        continue
    print(f"  {requirement:22} {mean(r['questions'] for r in a):9.1f} "
          f"{mean(r['questions'] for r in b):7.1f} "
          f"{mean(r['unsupported'] for r in a):11.1f} "
          f"{mean(r['unsupported'] for r in b):8.1f}")
