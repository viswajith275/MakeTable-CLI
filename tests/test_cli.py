import csv
import io
import json
import re
import tempfile
import unittest
from pathlib import Path

from typer.testing import CliRunner

from maketable.cli import app
from maketable.models import GeneratedResponse

ROOT = Path(__file__).resolve().parents[1]
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    return " ".join(_ANSI_RE.sub("", text).split())


class CliCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()
        self.sample_path = ROOT / "test_json.json"

    def test_validate_valid_missing_and_invalid_files(self):
        res = self.runner.invoke(app, ["validate", str(self.sample_path)])
        self.assertEqual(res.exit_code, 0, res.output)
        out = _plain(res.output)
        self.assertIn("Input is valid.", out)
        self.assertIn("21 assignments", out)

        missing = self.runner.invoke(app, ["validate", str(ROOT / "does_not_exist.json")])
        self.assertEqual(missing.exit_code, 1)

        with tempfile.TemporaryDirectory() as tmp:
            bad_json = Path(tmp) / "bad.json"
            bad_json.write_text("{invalid json", encoding="utf-8")
            bad_res = self.runner.invoke(app, ["validate", str(bad_json)])
            self.assertEqual(bad_res.exit_code, 1)

            invalid_model = Path(tmp) / "invalid_model.json"
            invalid_model.write_text(
                json.dumps({"project": {"name": "   ", "slots": 8}}),
                encoding="utf-8",
            )
            val_res = self.runner.invoke(app, ["validate", str(invalid_model)])
            self.assertEqual(val_res.exit_code, 1)

    def test_schema_stdout_and_file_output(self):
        res = self.runner.invoke(app, ["schema"])
        self.assertEqual(res.exit_code, 0)
        parsed = json.loads(res.stdout)
        self.assertIn("properties", parsed)
        self.assertIn("teacher_assignments", parsed["properties"])

        with tempfile.TemporaryDirectory() as tmp:
            out_file = Path(tmp) / "schema.json"
            file_res = self.runner.invoke(app, ["schema", "-o", str(out_file)])
            self.assertEqual(file_res.exit_code, 0)
            self.assertTrue(out_file.exists())
            self.assertEqual(json.loads(out_file.read_text(encoding="utf-8")), parsed)

    def test_generate_formats_files_and_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            # Small valid project for fast deterministic CLI generation
            tiny_json = tmp_dir / "project.json"
            payload = {
                "project": {"name": "CLI School", "slots": 4, "days": ["Mon", "Tue"], "break_slots": [3]},
                "rooms": [{"id": "00000000-0000-0000-0000-000000000001", "name": "R1"}],
                "classes": [{"id": "00000000-0000-0000-0000-000000000002", "name": "C1", "room_id": "00000000-0000-0000-0000-000000000001"}],
                "teachers": [{"id": "00000000-0000-0000-0000-000000000003", "name": "T1", "constraints": {"max_per_day": 3, "max_per_week": 6, "max_consecutive": 2}}],
                "subjects": [{"id": "00000000-0000-0000-0000-000000000004", "name": "Math", "constraints": {"max_per_day": 2, "max_per_week": 3, "min_per_week": 3, "max_consecutive": 2}}],
                "teacher_assignments": [{"class_id": "00000000-0000-0000-0000-000000000002", "teacher_id": "00000000-0000-0000-0000-000000000003", "subject_id": "00000000-0000-0000-0000-000000000004"}],
            }
            tiny_json.write_text(json.dumps(payload), encoding="utf-8")

            # validate reports break slots
            val_res = self.runner.invoke(app, ["validate", str(tiny_json)])
            self.assertEqual(val_res.exit_code, 0)
            self.assertIn("1 break slot(s)", _plain(val_res.output))

            # stdout JSON
            json_res = self.runner.invoke(
                app, ["generate", str(tiny_json), "-f", "json", "-w", "1", "-s", "42", "--fast"]
            )
            self.assertEqual(json_res.exit_code, 0, json_res.output)
            parsed_resp = GeneratedResponse.model_validate_json(json_res.stdout)
            self.assertTrue(parsed_resp.success)
            self.assertEqual(parsed_resp.break_slots, [3])

            # stdout CSV
            csv_res = self.runner.invoke(
                app, ["generate", str(tiny_json), "-f", "csv", "-w", "1", "--fast"]
            )
            self.assertEqual(csv_res.exit_code, 0, csv_res.output)
            csv_rows = list(csv.reader(io.StringIO(csv_res.stdout)))
            self.assertEqual(csv_rows[0][0], "day")
            self.assertGreaterEqual(len(csv_rows), 4)

            # file outputs (json, csv, table) + previous hint
            out_json = tmp_dir / "result.json"
            out_csv = tmp_dir / "result.csv"
            out_txt = tmp_dir / "result.txt"

            self.assertEqual(
                self.runner.invoke(
                    app, ["generate", str(tiny_json), "-o", str(out_json), "-w", "1", "-t", "5", "--patience", "0", "--gap-limit", "0.05"]
                ).exit_code,
                0,
            )
            self.assertTrue(out_json.exists())

            self.assertEqual(
                self.runner.invoke(
                    app, ["generate", str(tiny_json), "-o", str(out_csv), "--previous-hint", str(out_json), "-w", "1", "--fast"]
                ).exit_code,
                0,
            )
            self.assertTrue(out_csv.exists())

            self.assertEqual(
                self.runner.invoke(
                    app, ["generate", str(tiny_json), "-o", str(out_txt), "--no-violations", "-w", "1", "--fast"]
                ).exit_code,
                0,
            )
            table_text = out_txt.read_text(encoding="utf-8")
            self.assertIn("Timetable", table_text)
            self.assertNotIn("Violations", table_text)

    def test_generate_error_paths_and_non_tty_tui(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)

            # Empty teacher assignments -> exit 1
            empty_assign = tmp_dir / "empty.json"
            empty_assign.write_text(json.dumps({"project": {"name": "Empty"}}), encoding="utf-8")
            res = self.runner.invoke(app, ["generate", str(empty_assign)])
            self.assertEqual(res.exit_code, 1)

            # Unschedulable teacher (unavailable all slots) -> exit 1
            blocked_json = tmp_dir / "blocked.json"
            blocked_payload = {
                "project": {"name": "Blocked", "slots": 1, "days": ["Mon"]},
                "rooms": [{"id": "00000000-0000-0000-0000-000000000001", "name": "R1"}],
                "classes": [{"id": "00000000-0000-0000-0000-000000000002", "name": "C1", "room_id": "00000000-0000-0000-0000-000000000001"}],
                "teachers": [{
                    "id": "00000000-0000-0000-0000-000000000003",
                    "name": "T1",
                    "constraints": {
                        "max_per_day": 1, "max_per_week": 1, "max_consecutive": 1,
                        "unavailable_slots": [{"day": "Mon", "slot": 1}],
                    },
                }],
                "subjects": [{"id": "00000000-0000-0000-0000-000000000004", "name": "Math", "constraints": {"max_per_day": 1, "max_per_week": 1, "min_per_week": 1, "max_consecutive": 1}}],
                "teacher_assignments": [{"class_id": "00000000-0000-0000-0000-000000000002", "teacher_id": "00000000-0000-0000-0000-000000000003", "subject_id": "00000000-0000-0000-0000-000000000004"}],
            }
            blocked_json.write_text(json.dumps(blocked_payload), encoding="utf-8")
            blocked_res = self.runner.invoke(app, ["generate", str(blocked_json), "-w", "1"])
            self.assertEqual(blocked_res.exit_code, 1)

            # Missing hint file -> exit 1
            hint_res = self.runner.invoke(
                app, ["generate", str(self.sample_path), "--previous-hint", str(tmp_dir / "nope.json")]
            )
            self.assertEqual(hint_res.exit_code, 1)

        # Non-TTY root callback prints help (exit 0), explicit `tui` fails (exit 1)
        root_res = self.runner.invoke(app, [])
        self.assertEqual(root_res.exit_code, 0)
        self.assertIn("Usage:", _plain(root_res.output))

        tui_res = self.runner.invoke(app, ["tui"])
        self.assertEqual(tui_res.exit_code, 1)


if __name__ == "__main__":
    unittest.main()
