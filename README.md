# join-card

Profile a planned join between two CSV files before you run it. It reports whether the relationship is **1:1**, **1:n**, **n:1** or **n:n**, the fanout (how many child rows hang off each parent key), orphan foreign keys, and unused parent keys. Python 3 standard library only.

Use it to catch a fanout explosion before joining a fact table to a dimension, or as a pipeline gate when a lookup should stay 1:1. It complements `fk-check` (orphans only) and `skew-check` (one column's frequency).

## Usage

```bash
python3 join_card.py samples/customers.csv samples/orders.csv --parent-key customer_id
```

Options:

- `--child-key cust_id` when the foreign-key column has a different name. Composite keys work on both sides (`--parent-key store,sku --child-key loc,item`).
- `--ignore-case` compares keys case-insensitively.
- `--expect 1:1` (or `1:n`, `n:1`, `n:n`) fails when the observed cardinality does not match.
- `--max-fanout 10` fails when any parent key has more than 10 child rows.
- `--max-orphans 0` (default) is the allowed number of orphan child rows.

Exit codes: `0` all checks passed, `1` a check failed, `2` bad input (missing columns or a bad argument).

Empty foreign keys are counted as `null_child_rows`, not as orphans.

## Example output (real run on the samples)

```json
{
  "parent_rows": 4,
  "child_rows": 7,
  "parent_keys": 4,
  "child_keys": 4,
  "null_parent_rows": 0,
  "null_child_rows": 1,
  "duplicate_parent_keys": 0,
  "cardinality": "1:n",
  "matched_parent_keys": 3,
  "matched_child_rows": 5,
  "orphan_child_keys": 1,
  "orphan_child_rows": 1,
  "unused_parent_keys": 1,
  "fanout": {
    "min": 1,
    "p50": 1,
    "p95": 3,
    "max": 3,
    "mean": 1.6667
  },
  "examples": {
    "highest_fanout": [
      {"key": {"customer_id": "C1"}, "parent_rows": 1, "child_rows": 3}
    ],
    "orphans": [
      {"key": {"customer_id": "C9"}, "rows": [6]}
    ],
    "unused_parents": [
      {"key": {"customer_id": "C4"}}
    ]
  },
  "ok": false,
  "failed_checks": [
    "orphan_child_rows 1 > max-orphans 0"
  ]
}
```

Customer C1 has three orders, C4 has none, C9 is an orphan, and one order has a blank customer_id. The join is 1:n. Exit code was 1 because of the orphan. Adding `--max-orphans 1 --expect 1:n` makes the same run exit 0.

## Tests

```bash
python3 -m unittest -v
```
