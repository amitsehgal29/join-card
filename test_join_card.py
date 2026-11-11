#!/usr/bin/env python3
import io
import json
import os
import tempfile
import unittest

import join_card as jc


SAMPLES = os.path.join(os.path.dirname(__file__), "samples")
CUSTOMERS = os.path.join(SAMPLES, "customers.csv")
ORDERS = os.path.join(SAMPLES, "orders.csv")


def write_csv(path, text):
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def run_main(argv):
    stdout = io.StringIO()
    stderr = io.StringIO()
    old_out, old_err = jc.sys.stdout, jc.sys.stderr
    jc.sys.stdout, jc.sys.stderr = stdout, stderr
    try:
        code = jc.main(argv)
    finally:
        jc.sys.stdout, jc.sys.stderr = old_out, old_err
    out = stdout.getvalue()
    payload = json.loads(out) if out.strip().startswith("{") else None
    return code, payload, stderr.getvalue()


class ProfileJoinTests(unittest.TestCase):
    def test_sample_is_one_to_n(self):
        with open(CUSTOMERS, newline="", encoding="utf-8") as handle:
            _fields, parents = jc.read_rows(handle)
        with open(ORDERS, newline="", encoding="utf-8") as handle:
            _fields, children = jc.read_rows(handle)
        report = jc.profile_join(parents, children, ["customer_id"], ["customer_id"])
        self.assertEqual(report["parent_rows"], 4)
        self.assertEqual(report["child_rows"], 7)
        self.assertEqual(report["cardinality"], "1:n")
        self.assertEqual(report["matched_parent_keys"], 3)
        self.assertEqual(report["matched_child_rows"], 5)
        self.assertEqual(report["orphan_child_keys"], 1)
        self.assertEqual(report["orphan_child_rows"], 1)
        self.assertEqual(report["unused_parent_keys"], 1)
        self.assertEqual(report["null_child_rows"], 1)
        self.assertEqual(report["fanout"]["min"], 1)
        self.assertEqual(report["fanout"]["max"], 3)
        self.assertEqual(report["examples"]["highest_fanout"][0]["key"], {"customer_id": "C1"})
        self.assertEqual(report["examples"]["highest_fanout"][0]["child_rows"], 3)
        self.assertEqual(report["examples"]["orphans"][0]["key"], {"customer_id": "C9"})
        self.assertEqual(report["examples"]["unused_parents"][0]["key"], {"customer_id": "C4"})

    def test_one_to_one(self):
        parents = [{"id": "A"}, {"id": "B"}]
        children = [{"id": "A"}, {"id": "B"}]
        report = jc.profile_join(parents, children, ["id"], ["id"])
        self.assertEqual(report["cardinality"], "1:1")
        self.assertEqual(report["fanout"]["max"], 1)
        self.assertEqual(report["orphan_child_rows"], 0)
        self.assertEqual(report["examples"]["highest_fanout"], [])

    def test_many_to_one(self):
        parents = [{"id": "A", "ver": "1"}, {"id": "A", "ver": "2"}]
        children = [{"id": "A"}]
        report = jc.profile_join(parents, children, ["id"], ["id"])
        self.assertEqual(report["cardinality"], "n:1")
        self.assertEqual(report["duplicate_parent_keys"], 1)
        self.assertEqual(report["fanout"]["max"], 1)

    def test_many_to_many(self):
        parents = [{"id": "A"}, {"id": "A"}]
        children = [{"id": "A"}, {"id": "A"}]
        report = jc.profile_join(parents, children, ["id"], ["id"])
        self.assertEqual(report["cardinality"], "n:n")
        self.assertEqual(report["fanout"]["max"], 2)

    def test_composite_keys(self):
        parents = [
            {"store": "DXB", "sku": "A1"},
            {"store": "AUH", "sku": "A1"},
        ]
        children = [
            {"loc": "DXB", "item": "A1"},
            {"loc": "DXB", "item": "A1"},
            {"loc": "SHJ", "item": "B2"},
        ]
        report = jc.profile_join(
            parents, children, ["store", "sku"], ["loc", "item"]
        )
        self.assertEqual(report["cardinality"], "1:n")
        self.assertEqual(report["matched_parent_keys"], 1)
        self.assertEqual(report["orphan_child_keys"], 1)
        self.assertEqual(report["unused_parent_keys"], 1)
        self.assertEqual(
            report["examples"]["highest_fanout"][0]["key"],
            {"store": "DXB", "sku": "A1"},
        )

    def test_ignore_case(self):
        parents = [{"id": "Ab"}]
        children = [{"id": "ab"}, {"id": "AB"}]
        raw = jc.profile_join(parents, children, ["id"], ["id"])
        self.assertEqual(raw["orphan_child_rows"], 2)
        folded = jc.profile_join(parents, children, ["id"], ["id"], ignore_case=True)
        self.assertEqual(folded["matched_child_rows"], 2)
        self.assertEqual(folded["orphan_child_rows"], 0)
        self.assertEqual(folded["cardinality"], "1:n")


class CliTests(unittest.TestCase):
    def test_sample_run_fails_on_orphan(self):
        code, payload, _err = run_main(
            [CUSTOMERS, ORDERS, "--parent-key", "customer_id"]
        )
        self.assertEqual(code, 1)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["cardinality"], "1:n")
        self.assertTrue(any("orphan_child_rows" in r for r in payload["failed_checks"]))

    def test_max_orphans_allows_sample(self):
        code, payload, _err = run_main(
            [CUSTOMERS, ORDERS, "--parent-key", "customer_id", "--max-orphans", "1"]
        )
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])

    def test_expect_one_to_one_fails(self):
        code, payload, _err = run_main(
            [
                CUSTOMERS,
                ORDERS,
                "--parent-key",
                "customer_id",
                "--max-orphans",
                "1",
                "--expect",
                "1:1",
            ]
        )
        self.assertEqual(code, 1)
        self.assertTrue(any("cardinality" in r for r in payload["failed_checks"]))

    def test_max_fanout_fails(self):
        code, payload, _err = run_main(
            [
                CUSTOMERS,
                ORDERS,
                "--parent-key",
                "customer_id",
                "--max-orphans",
                "1",
                "--max-fanout",
                "2",
            ]
        )
        self.assertEqual(code, 1)
        self.assertTrue(any("fanout" in r for r in payload["failed_checks"]))

    def test_missing_column_exits_two(self):
        code, payload, err = run_main(
            [CUSTOMERS, ORDERS, "--parent-key", "nope"]
        )
        self.assertEqual(code, 2)
        self.assertIsNone(payload)
        self.assertIn("missing column", err)

    def test_child_key_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            child = os.path.join(tmp, "facts.csv")
            write_csv(
                child,
                "order_id,cust_id\n1,C2\n2,C2\n",
            )
            code, payload, _err = run_main(
                [
                    CUSTOMERS,
                    child,
                    "--parent-key",
                    "customer_id",
                    "--child-key",
                    "cust_id",
                ]
            )
            self.assertEqual(code, 0)
            self.assertEqual(payload["cardinality"], "1:n")
            self.assertEqual(payload["matched_child_rows"], 2)
            self.assertEqual(payload["orphan_child_rows"], 0)


if __name__ == "__main__":
    unittest.main()
