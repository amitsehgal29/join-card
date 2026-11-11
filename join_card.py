#!/usr/bin/env python3
"""join-card: profile join cardinality and fanout between two CSVs.

Standard library only. Given a parent (dimension/lookup) file and a child
(fact) file, it reports whether the join is 1:1, 1:n, n:1 or n:n, how many
child rows hang off each parent key, and any orphan or unused keys.
"""
import argparse
import csv
import json
import statistics
import sys


def parse_columns(value):
    return [part.strip() for part in value.split(",") if part.strip()]


def read_rows(handle):
    reader = csv.DictReader(handle)
    if reader.fieldnames is None:
        return [], []
    return list(reader.fieldnames), list(reader)


def key_of(row, keys, ignore_case):
    values = []
    for name in keys:
        raw = row.get(name)
        if raw is None:
            raw = ""
        raw = raw.strip()
        if ignore_case:
            raw = raw.lower()
        values.append(raw)
    return tuple(values)


def is_null_key(key):
    return all(part == "" for part in key)


def index_keys(rows, keys, ignore_case):
    """Map key -> list of 1-based data row numbers. Null keys are counted separately."""
    index = {}
    null_rows = []
    for i, row in enumerate(rows, start=1):
        key = key_of(row, keys, ignore_case)
        if is_null_key(key):
            null_rows.append(i)
            continue
        index.setdefault(key, []).append(i)
    return index, null_rows


def key_record(keys, key):
    return dict(zip(keys, key))


def percentile(sorted_values, p):
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    index = int(round(p * (len(sorted_values) - 1)))
    index = min(max(index, 0), len(sorted_values) - 1)
    return sorted_values[index]


def cardinality_label(parent_mult_max, child_mult_max):
    parent_one = parent_mult_max <= 1
    child_one = child_mult_max <= 1
    if parent_one and child_one:
        return "1:1"
    if parent_one and not child_one:
        return "1:n"
    if not parent_one and child_one:
        return "n:1"
    return "n:n"


def profile_join(parent_rows, child_rows, parent_keys, child_keys, ignore_case=False):
    parent_index, null_parent_rows = index_keys(parent_rows, parent_keys, ignore_case)
    child_index, null_child_rows = index_keys(child_rows, child_keys, ignore_case)

    parent_mults = [len(v) for v in parent_index.values()]
    child_mults = [len(v) for v in child_index.values()]
    parent_mult_max = max(parent_mults) if parent_mults else 0
    child_mult_max = max(child_mults) if child_mults else 0

    matched = []
    unused = []
    for key, parent_lines in parent_index.items():
        child_lines = child_index.get(key, [])
        if child_lines:
            matched.append((key, parent_lines, child_lines))
        else:
            unused.append((key, parent_lines))

    orphans = [
        (key, lines) for key, lines in child_index.items() if key not in parent_index
    ]

    fanouts = [len(child_lines) for _key, _p, child_lines in matched]
    fanouts_sorted = sorted(fanouts)
    fanout = {
        "min": fanouts_sorted[0] if fanouts_sorted else None,
        "p50": percentile(fanouts_sorted, 0.50),
        "p95": percentile(fanouts_sorted, 0.95),
        "max": fanouts_sorted[-1] if fanouts_sorted else None,
        "mean": round(statistics.mean(fanouts_sorted), 4) if fanouts_sorted else None,
    }

    highest = sorted(matched, key=lambda item: (-len(item[2]), item[0]))
    return {
        "parent_rows": len(parent_rows),
        "child_rows": len(child_rows),
        "parent_keys": len(parent_index),
        "child_keys": len(child_index),
        "null_parent_rows": len(null_parent_rows),
        "null_child_rows": len(null_child_rows),
        "duplicate_parent_keys": sum(1 for v in parent_index.values() if len(v) > 1),
        "cardinality": cardinality_label(parent_mult_max, child_mult_max),
        "matched_parent_keys": len(matched),
        "matched_child_rows": sum(len(c) for _k, _p, c in matched),
        "orphan_child_keys": len(orphans),
        "orphan_child_rows": sum(len(lines) for _k, lines in orphans),
        "unused_parent_keys": len(unused),
        "fanout": fanout,
        "examples": {
            "highest_fanout": [
                {
                    "key": key_record(parent_keys, key),
                    "parent_rows": len(parent_lines),
                    "child_rows": len(child_lines),
                }
                for key, parent_lines, child_lines in highest[:5]
                if len(child_lines) > 1
            ],
            "orphans": [
                {"key": key_record(child_keys, key), "rows": lines}
                for key, lines in orphans[:10]
            ],
            "unused_parents": [
                {"key": key_record(parent_keys, key)} for key, _lines in unused[:10]
            ],
        },
    }


def missing_columns(fieldnames, required):
    return [name for name in required if name not in fieldnames]


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Profile join cardinality and fanout between a parent CSV and a child CSV."
    )
    parser.add_argument("parent", help="parent / lookup CSV")
    parser.add_argument("child", help="child / fact CSV")
    parser.add_argument(
        "--parent-key",
        required=True,
        help="parent key column(s), comma-separated",
    )
    parser.add_argument(
        "--child-key",
        help="child foreign-key column(s), default: same as --parent-key",
    )
    parser.add_argument(
        "--ignore-case",
        action="store_true",
        help="compare keys case-insensitively",
    )
    parser.add_argument(
        "--expect",
        choices=["1:1", "1:n", "n:1", "n:n"],
        help="fail when the observed cardinality does not match",
    )
    parser.add_argument(
        "--max-fanout",
        type=int,
        default=None,
        help="fail when any parent key has more than this many child rows",
    )
    parser.add_argument(
        "--max-orphans",
        type=int,
        default=0,
        help="allowed orphan child rows before exit 1 (default: 0)",
    )
    args = parser.parse_args(argv)

    parent_keys = parse_columns(args.parent_key)
    child_keys = parse_columns(args.child_key) if args.child_key else list(parent_keys)
    if not parent_keys:
        print("join-card: --parent-key is required", file=sys.stderr)
        return 2
    if len(parent_keys) != len(child_keys):
        print(
            "join-card: --parent-key and --child-key must have the same number of columns",
            file=sys.stderr,
        )
        return 2

    try:
        with open(args.parent, newline="", encoding="utf-8") as handle:
            parent_fields, parent_rows = read_rows(handle)
        with open(args.child, newline="", encoding="utf-8") as handle:
            child_fields, child_rows = read_rows(handle)
    except OSError as exc:
        print(f"join-card: {exc}", file=sys.stderr)
        return 2

    missing_parent = missing_columns(parent_fields, parent_keys)
    missing_child = missing_columns(child_fields, child_keys)
    if missing_parent or missing_child:
        parts = []
        if missing_parent:
            parts.append("parent missing column(s): " + ", ".join(missing_parent))
        if missing_child:
            parts.append("child missing column(s): " + ", ".join(missing_child))
        print("join-card: " + "; ".join(parts), file=sys.stderr)
        return 2

    report = profile_join(
        parent_rows, child_rows, parent_keys, child_keys, ignore_case=args.ignore_case
    )

    reasons = []
    if report["orphan_child_rows"] > args.max_orphans:
        reasons.append(
            f"orphan_child_rows {report['orphan_child_rows']} > max-orphans {args.max_orphans}"
        )
    if args.max_fanout is not None:
        observed = report["fanout"]["max"] or 0
        if observed > args.max_fanout:
            reasons.append(f"fanout max {observed} > max-fanout {args.max_fanout}")
    if args.expect and report["cardinality"] != args.expect:
        reasons.append(f"cardinality {report['cardinality']} != expected {args.expect}")

    report["ok"] = not reasons
    if reasons:
        report["failed_checks"] = reasons

    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
