#!/usr/bin/env python3
"""Evaluate the three-arm comparison from rows.json.

Degraded runs - a hybrid arm that fell back to lexical - are reported and
then excluded from the arm's figures, never merged in.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

ARMS = ("none", "lexical", "hybrid")

#: what is reported, and whether more is better, worse, or neither
METRICS = [
    ("drafts", "test drafts", "neutral"),
    ("questions", "open questions", "neutral"),
    ("unsupported", "unsupported values", "lower"),
    ("no_criterion", "expectations without a criterion", "lower"),
    ("conflicts", "possible value conflicts", "lower"),
    ("findings_for_review", "findings a reviewer must work through", "lower"),
    ("context_sent", "context items sent", "neutral"),
    ("context_task_search", "  of those found by the task text", "neutral"),
    ("context_supported", "context values actually used", "higher"),
    ("model_calls", "model calls", "lower"),
    ("repairs", "repairs", "lower"),
    ("seconds", "seconds", "lower"),
]


def mean(values):
    return sum(values) / len(values) if values else 0.0


def stdev(values):
    if len(values) < 2:
        return 0.0
    m = mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rows")
    parser.add_argument("--out")
    args = parser.parse_args()

    rows = json.loads(Path(args.rows).read_text(encoding="utf-8"))
    lines: list[str] = []

    def say(text=""):
        print(text)
        lines.append(text)

    degraded = [r for r in rows if r.get("degraded")]
    raised = [r for r in rows if r.get("status") == "raised"]
    usable = [r for r in rows if not r.get("degraded") and r.get("status") != "raised"]

    prompts = sorted({r.get("prompt_version") for r in usable if r.get("prompt_version")})
    models = sorted({r.get("model") for r in usable if r.get("model")})
    say(f"{len(rows)} run(s) recorded, {len(usable)} usable")
    say(f"prompt version(s): {', '.join(prompts) or 'none'}")
    say(f"model(s): {', '.join(models) or 'none'}")
    if len(prompts) > 1 or len(models) > 1:
        say("  WARNING: the arms do not share one baseline; the table below is void")
    say()

    if degraded:
        say(f"{len(degraded)} run(s) were DEGRADED and are excluded:")
        for row in degraded[:10]:
            say(
                f"  {row['arm']} -> {row['effective_retriever']} "
                f"{row.get('requirement')}"
            )
        say()
    else:
        say("no degraded run: every arm used the retriever it asked for")
        say()

    if raised:
        say(f"{len(raised)} run(s) raised before finishing:")
        for row in raised[:10]:
            say(f"  {row['arm']} {row.get('requirement')}: {row.get('error')}")
        say()

    by_arm = {arm: [r for r in usable if r["arm"] == arm] for arm in ARMS}
    present = [arm for arm in ARMS if by_arm[arm]]

    say("completion")
    say(f"  {'arm':<10}{'runs':>6}{'completed':>11}{'other':>8}{'not started':>13}")
    for arm in present:
        group = by_arm[arm]
        done = sum(1 for r in group if r["status"] == "completed")
        starved = sum(r.get("units_not_started", 0) for r in group)
        say(
            f"  {arm:<10}{len(group):>6}{done:>11}{len(group) - done:>8}{starved:>13}"
        )
    say()

    width = max(len(label) for _k, label, _d in METRICS) + 2
    say("mean per run (standard deviation in brackets)")
    say("  " + "metric".ljust(width) + "".join(f"{arm:>22}" for arm in present))
    for key, label, direction in METRICS:
        cells = ""
        values = {}
        for arm in present:
            vals = [r.get(key, 0) or 0 for r in by_arm[arm]]
            values[arm] = mean(vals)
            cells += f"{mean(vals):>14.2f} ({stdev(vals):>4.2f})"
        marker = ""
        if direction != "neutral" and len(present) > 1:
            best = (min if direction == "lower" else max)(values, key=values.get)
            worst = (max if direction == "lower" else min)(values, key=values.get)
            if abs(values[best] - values[worst]) > 1e-9:
                marker = f"   best: {best}"
        say("  " + label.ljust(width) + cells + marker)
    say()

    say("context: found is not sent, and sent is not used")
    say(f"  {'arm':<10}{'sent/run':>10}{'by task':>10}{'used':>8}{'used/sent':>12}")
    for arm in present:
        group = by_arm[arm]
        sent = sum(r.get("context_sent", 0) for r in group)
        task = sum(r.get("context_task_search", 0) for r in group)
        used = sum(r.get("context_supported", 0) for r in group)
        ratio = f"{used / sent:.2f}" if sent else "-"
        say(
            f"  {arm:<10}{sent / len(group):>10.2f}{task / len(group):>10.2f}"
            f"{used:>8}{ratio:>12}"
        )
    say()

    say("per requirement: open questions, mean of the repetitions")
    ids = sorted({r["requirement"] for r in usable})
    say("  " + "requirement".ljust(20) + "".join(f"{arm:>10}" for arm in present))
    for external_id in ids:
        cells = ""
        for arm in present:
            vals = [
                r["questions"] for r in by_arm[arm] if r["requirement"] == external_id
            ]
            cells += f"{mean(vals):>10.1f}" if vals else f"{'-':>10}"
        say("  " + external_id.ljust(20) + cells)
    say()

    say("A reminder about what these numbers can and cannot say:")
    say("  Fewer open questions is not a win on its own. A model that stops")
    say("  asking because context made it confident is worse, not better. Read")
    say("  'questions' together with 'unsupported values' and with the review")
    say("  list; the drafts themselves still need a tester's judgement.")

    if args.out:
        Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\nwritten to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
