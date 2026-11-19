"""Offline tests for --excel - no scale and no Excel needed (Excel is mocked).

Run:  python -m unittest discover -s tests -v
"""

import argparse
import contextlib
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import excel_cell  # noqa: E402
import test_scale  # noqa: E402


def make_args():
    return argparse.Namespace(all=False, settle=1, mode="continuous", min_weight=0.0,
                              no_raw=True, webhook=False, auto_send=False, excel=True)


class ToNumberTests(unittest.TestCase):
    def test_numbers(self):
        self.assertEqual(excel_cell.to_number("2.70"), 2.7)
        self.assertEqual(excel_cell.to_number("2700"), 2700)
        self.assertIsInstance(excel_cell.to_number("2700"), int)


class ExcelEventTests(unittest.TestCase):
    """Drive report() with readings and check what is written to Excel."""

    def feed(self, readings, write=None):
        args, state = make_args(), {"timeouts": 0}
        write = write or mock.Mock(return_value="Book1 / Sheet1 / B2")
        with mock.patch.object(excel_cell, "write_selected_cell", write), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            for value, stable in readings:
                test_scale.report(args, state, value, "g", stable, b"", f"Weight: {value} g")
        return write, out.getvalue()

    def test_every_new_stable_weight_is_written(self):
        write, out = self.feed([
            ("0", True),                                   # empty
            ("1200", False), ("2700", True),               # parcel 1 settles
            ("2700", True),                                # unchanged: not written again
            ("4200", True),                                # parcel added on top
            ("1500", True),                                # swapped without emptying
            ("0", True),                                   # empty: not written
        ])
        self.assertEqual([c.args[0] for c in write.call_args_list], ["2700", "4200", "1500"])
        self.assertEqual(out.count("ITEM"), 3)

    def test_parcels_with_the_same_weight_are_all_written(self):
        write, out = self.feed([
            ("0", True), ("2700", True),                   # parcel 1
            ("0", True), ("2700", True),                   # parcel 2, same weight
            ("0", True), ("2700", True),                   # parcel 3, same weight
        ])
        self.assertEqual([c.args[0] for c in write.call_args_list], ["2700"] * 3)
        self.assertEqual(out.count("ITEM"), 3)

    def test_motion_is_never_written(self):
        write, _ = self.feed([("0", True), ("2700", False), ("2800", False)])
        write.assert_not_called()

    def test_excel_error_is_reported_not_raised(self):
        failing = mock.Mock(side_effect=excel_cell.ExcelError("Excel is not running"))
        _, out = self.feed([("0", True), ("2700", True)], write=failing)
        self.assertIn("NOT written to Excel: Excel is not running", out)
        self.assertIn("Type it in by hand: 2700 g", out)


if __name__ == "__main__":
    unittest.main()
