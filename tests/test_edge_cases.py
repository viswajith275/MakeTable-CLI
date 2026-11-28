"""Edge-case regression tests for helpers, models, solver, CLI and TUI."""

import csv
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from pydantic import ValidationError
from rich.console import Console
from textual.widgets import DataTable, Input, Select, SelectionList, Static
from typer.testing import CliRunner

from maketable import (
    ClassInput,
    DaySlot,
    GeneratedResponse,
    Level,
    ProjectInput,
    RoomConstraints,
    RoomInput,
    SubjectConstraints,
    SubjectInput,
    TeacherAssignmentConstraint,
    TeacherAssignmentInput,
    TeacherConstraints,
    TeacherInput,
    TimeTableEntryOutput,
    TimeTableGenerationInput,
    ViolationOut,
    WeekDay,
    format_day_slots,
    generate,
    parse_day_slots,
)
from maketable.cli import (
    OutputFormat,
    _load_hint_input,
    _load_input,
    _print_entries_table,
    _print_violations,
    _resolve_format,
    app as cli_app,
)
from maketable.models import class_weekly_requirements, csv_safe, valid_block_starts
from maketable.tui import (
    ConfirmScreen,
    Field,
    FormScreen,
    MessageScreen,
    PromptScreen,
    TimetableApp,
)

ROOT = Path(__file__).resolve().parents[1]


def tiny_project() -> TimeTableGenerationInput:
    room = RoomInput(name="R1")
    class_ = ClassInput(name="C1", room_id=room.id)
    teacher = TeacherInput(
        name="T1",
        constraints=TeacherConstraints(
            max_per_day=4, max_per_week=9, max_consecutive=2
        ),
    )
    subject = SubjectInput(
        name="Math",
        constraints=SubjectConstraints(
            max_per_day=2,
            min_per_day=0,
            max_per_week=4,
            min_per_week=3,
            max_consecutive=2,
            min_consecutive=0,
        ),
    )
    assignment = TeacherAssignmentInput(
        class_id=class_.id, teacher_id=teacher.id, subject_id=subject.id
    )
    return TimeTableGenerationInput(
        project=ProjectInput(
            name="Tiny", slots=4, days=[WeekDay.Mon, WeekDay.Tue, WeekDay.Wed]
        ),
        rooms=[room],
        classes=[class_],
        teachers=[teacher],
        subjects=[subject],
        teacher_assignments=[assignment],
    )


def one_entry_response() -> GeneratedResponse:
    return GeneratedResponse(
        success=True,
        violations=[],
        entries=[
            TimeTableEntryOutput(
                assignment_id=uuid4(),
                class_name="C1",
                room_name="R1",
                subject_name="Math",
                teacher_name="T1",
                day=WeekDay.Mon,
                slot=1,
            )
        ],
    )


class HelperEdgeCaseTests(unittest.TestCase):
    def test_parse_day_slots_boundaries_and_errors(self):
        self.assertEqual(parse_day_slots(None), [])
        self.assertEqual(parse_day_slots("Mon:1"), [DaySlot(day=WeekDay.Mon, slot=1)])
        self.assertEqual(parse_day_slots("Sun:24"), [DaySlot(day=WeekDay.Sun, slot=24)])
        self.assertEqual(len(parse_day_slots("Mon:1-1")), 1)
        self.assertEqual(len(parse_day_slots("Mon:1-24")), 24)

        for bad, message in (
            ("Mon:1-2-3", "Invalid slot range"),
            ("Mon:-1", "Invalid slot range"),
            ("Mon:abc", "Invalid slot"),
            ("Mon:", "Invalid slot"),
            ("Mon:1.5", "Invalid slot"),
            ("Mon:0", "within 1..24"),
            ("Mon:25", "within 1..24"),
            ("Mon:4-2", "within 1..24"),
            ("Mon:1-25", "within 1..24"),
        ):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, message):
                    parse_day_slots(bad)

    def test_day_slot_format_and_string(self):
        self.assertEqual(format_day_slots([]), "")
        self.assertEqual(str(DaySlot(day=WeekDay.Sun, slot=24)), "Sun:24")
        self.assertEqual(
            format_day_slots([DaySlot(day=WeekDay.Mon, slot=1), DaySlot(day=WeekDay.Tue, slot=2)]),
            "Mon:1, Tue:2",
        )

    def test_valid_block_starts_edges(self):
        self.assertEqual(valid_block_starts(1, [], 1), [0])
        self.assertEqual(valid_block_starts(1, [1], 1), [])
        self.assertEqual(valid_block_starts(4, [1, 4], 2), [1])
        self.assertEqual(valid_block_starts(4, [2, 3], 2), [])
        self.assertEqual(valid_block_starts(4, [2], 2), [2])
        self.assertEqual(valid_block_starts(5, [], 5), [0])
        self.assertEqual(valid_block_starts(5, [], 6), [])
        self.assertEqual(valid_block_starts(5, [1], 0), [])
        self.assertEqual(valid_block_starts(5, [1], -1), [])

    def test_csv_safe_values(self):
        self.assertEqual(csv_safe(None), "")
        self.assertEqual(csv_safe(0), "0")
        self.assertEqual(csv_safe(False), "False")
        for prefix in ("=", "+", "-", "@"):
            self.assertEqual(csv_safe(f"{prefix}1"), f"'{prefix}1")


class ModelEdgeCaseTests(unittest.TestCase):
    def test_project_break_slot_bounds(self):
        with self.assertRaises(ValidationError):
            ProjectInput(break_slots=[0])
        with self.assertRaises(ValidationError):
            ProjectInput(break_slots=[1, 1])
        self.assertEqual(ProjectInput(break_slots=[3, 1]).break_slots, [1, 3])

        data = tiny_project()
        data.project.slots = 2
        data.project.break_slots = [3]
        with self.assertRaisesRegex(ValidationError, "exceeds project total slots"):
            TimeTableGenerationInput.model_validate(data.model_dump())

        data.project.break_slots = [1, 2]
        with self.assertRaisesRegex(ValidationError, "cannot occupy all slots"):
            TimeTableGenerationInput.model_validate(data.model_dump())

    def test_teacher_limit_edges(self):
        data = tiny_project()
        data.teachers[0].constraints.max_days_per_week = 4
        with self.assertRaisesRegex(ValidationError, "max_days_per_week"):
            TimeTableGenerationInput.model_validate(data.model_dump())

        data = tiny_project()
        data.teachers[0].constraints.max_per_week = 999
        with self.assertRaisesRegex(ValidationError, "max_per_week"):
            TimeTableGenerationInput.model_validate(data.model_dump())

        data = tiny_project()
        data.teachers[0].constraints.max_consecutive = 99
        with self.assertRaisesRegex(ValidationError, "max_consecutive"):
            TimeTableGenerationInput.model_validate(data.model_dump())

    def test_subject_fixed_slot_validation_edges(self):
        with self.assertRaises(ValidationError):
            DaySlot(day=WeekDay.Mon, slot=0)

        data = tiny_project()
        data.subjects[0].constraints.fixed_slots = [
            DaySlot(day=WeekDay.Mon, slot=1)
        ]
        TimeTableGenerationInput.model_validate(data.model_dump())

        data = tiny_project()
        data.subjects[0].constraints.fixed_slots = [
            DaySlot(day=WeekDay.Sat, slot=1)
        ]
        with self.assertRaisesRegex(ValidationError, "not a project day"):
            TimeTableGenerationInput.model_validate(data.model_dump())

        data = tiny_project()
        data.subjects[0].constraints.fixed_slots = [
            DaySlot(day=WeekDay.Mon, slot=5)
        ]
        with self.assertRaisesRegex(ValidationError, "outside valid project slots"):
            TimeTableGenerationInput.model_validate(data.model_dump())

        data = tiny_project()
        data.project.break_slots = [2]
        data.subjects[0].constraints.fixed_slots = [
            DaySlot(day=WeekDay.Mon, slot=2)
        ]
        with self.assertRaisesRegex(ValidationError, "project break slot"):
            TimeTableGenerationInput.model_validate(data.model_dump())

    def test_assignment_constraint_duplicates(self):
        with self.assertRaises(ValidationError):
            TeacherAssignmentConstraint(first_slot_days=[WeekDay.Mon, WeekDay.Mon])
        duplicate = uuid4()
        with self.assertRaises(ValidationError):
            TeacherAssignmentConstraint(co_schedule_with=[duplicate, duplicate])

    def test_class_weekly_requirements_empty(self):
        self.assertEqual(class_weekly_requirements([], []), {})

    def test_generated_response_empty_helpers(self):
        response = GeneratedResponse(success=True, entries=[], violations=[])
        self.assertEqual(response.get_classes(), [])
        self.assertEqual(response.get_teachers(), [])
        self.assertEqual(response.get_rooms(), [])
        self.assertEqual(response.get_class_grid("C1"), {})
        self.assertEqual(response.get_teacher_grid("T1", total_slots=3), {})
        self.assertEqual(response.get_room_grid("R1", total_slots=3), {})
        self.assertEqual(response.to_csv().splitlines()[0].split(",")[0], "day")

    def test_generated_response_grid_fills_empty_slots(self):
        response = one_entry_response()
        grid = response.get_class_grid("C1", total_slots=3)
        self.assertEqual(grid["Mon"][1][0].subject_name, "Math")
        self.assertEqual(grid["Mon"][2], [])
        self.assertEqual(grid["Mon"][3], [])


class SolverEdgeCaseTests(unittest.TestCase):
    def test_solver_option_edges(self):
        data = tiny_project()
        base = {"time_limit_sec": 2, "workers": 1}
        for kwargs in (
            {"workers": 0},
            {"workers": -3},
            {"patience": 0, "gap_limit": 0},
            {"time_limit_sec": 0.01},
            {"fast": True, "time_limit_sec": 0.01},
        ):
            with self.subTest(kwargs=kwargs):
                response = generate(data, **{**base, **kwargs})
                self.assertIsInstance(response, GeneratedResponse)

    def test_break_slots_never_scheduled(self):
        data = tiny_project()
        data.project.break_slots = [2]
        response = generate(data, time_limit_sec=3, workers=1)
        self.assertTrue(response.success)
        self.assertFalse(any(e.slot == 2 for e in response.entries))

    def test_multi_period_block_does_not_cross_break(self):
        data = tiny_project()
        data.project.slots = 5
        data.project.break_slots = [3]
        data.subjects[0].constraints.min_per_week = 2
        data.subjects[0].constraints.max_per_week = 2
        data.teacher_assignments[0].constraints.period_length = 2
        response = generate(data, time_limit_sec=4, workers=1)
        self.assertTrue(response.success)
        by_day = {}
        for entry in response.entries:
            self.assertNotEqual(entry.slot, 3)
            by_day.setdefault(entry.day, []).append(entry.slot)
        for slots in by_day.values():
            sorted_slots = sorted(slots)
            for index in range(0, len(sorted_slots), 2):
                self.assertEqual(sorted_slots[index + 1], sorted_slots[index] + 1)

    def test_teacher_consecutive_limit_treats_break_as_separator(self):
        data = tiny_project()
        data.project.slots = 4
        data.project.days = [WeekDay.Mon]
        data.project.break_slots = [2]
        data.teachers[0].constraints = TeacherConstraints(
            max_per_day=2, max_per_week=2, max_consecutive=1
        )
        data.subjects[0].constraints = SubjectConstraints(
            max_per_day=2,
            max_per_week=2,
            min_per_week=2,
            max_consecutive=1,
            min_consecutive=0,
        )
        response = generate(data, time_limit_sec=4, workers=1)
        self.assertTrue(response.success)
        self.assertFalse(
            any("Max consecutive" in v.error_msg for v in response.violations)
        )
        self.assertFalse(any(e.slot == 2 for e in response.entries))

    def test_first_slot_and_fixed_slots_with_multi_period_block(self):
        data = tiny_project()
        data.project.slots = 6
        data.project.days = [WeekDay.Mon, WeekDay.Tue]
        data.subjects[0].constraints = SubjectConstraints(
            max_per_day=2,
            max_per_week=4,
            min_per_week=4,
            max_consecutive=2,
            min_consecutive=0,
            fixed_slots=[
                DaySlot(day=WeekDay.Tue, slot=5),
                DaySlot(day=WeekDay.Tue, slot=6),
            ],
        )
        data.teacher_assignments[0].constraints = TeacherAssignmentConstraint(
            period_length=2, first_slot_days=[WeekDay.Mon]
        )
        response = generate(data, time_limit_sec=4, workers=1)
        self.assertTrue(response.success)
        self.assertTrue(
            any(e.day == WeekDay.Mon and e.slot == 1 for e in response.entries)
        )
        self.assertTrue(
            any(e.day == WeekDay.Tue and e.slot == 5 for e in response.entries)
        )
        self.assertTrue(
            any(e.day == WeekDay.Tue and e.slot == 6 for e in response.entries)
        )

    def test_avoid_slot_on_break_is_ignored(self):
        data = tiny_project()
        data.project.break_slots = [2]
        data.teachers[0].constraints.avoid_slots = [DaySlot(day=WeekDay.Mon, slot=2)]
        response = generate(data, time_limit_sec=3, workers=1)
        self.assertTrue(response.success)
        self.assertFalse(any("avoided slot" in v.error_msg for v in response.violations))

    def test_hint_edge_cases_are_ignored(self):
        data = tiny_project()
        assignment_id = data.teacher_assignments[0].id
        hint = GeneratedResponse(
            success=True,
            violations=[],
            entries=[
                TimeTableEntryOutput(
                    assignment_id=uuid4(),
                    class_name="Unknown",
                    room_name="Unknown",
                    subject_name="Unknown",
                    teacher_name="Unknown",
                    day=WeekDay.Mon,
                    slot=1,
                ),
                TimeTableEntryOutput(
                    assignment_id=assignment_id,
                    class_name="C1",
                    room_name="R1",
                    subject_name="Math",
                    teacher_name="T1",
                    day=WeekDay.Sat,
                    slot=1,
                ),
                TimeTableEntryOutput(
                    assignment_id=assignment_id,
                    class_name="C1",
                    room_name="R1",
                    subject_name="Math",
                    teacher_name="T1",
                    day=WeekDay.Mon,
                    slot=99,
                ),
                TimeTableEntryOutput(
                    assignment_id=assignment_id,
                    class_name="C1",
                    room_name="R1",
                    subject_name="Math",
                    teacher_name="T1",
                    day=WeekDay.Mon,
                    slot=2,
                ),
            ],
        )
        data.project.break_slots = [2]
        response = generate(data, time_limit_sec=3, workers=1, hint=hint)
        self.assertTrue(response.success)
        self.assertFalse(any(e.slot == 2 for e in response.entries))


class CliEdgeCaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.project_path = self.tmp_path / "project.json"
        self.project_path.write_text(tiny_project().model_dump_json(), encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_home_path_expansion_for_input_and_hint(self):
        env = {"HOME": str(self.tmp_path), "USERPROFILE": str(self.tmp_path)}
        with patch.dict(os.environ, env):
            validate = self.runner.invoke(cli_app, ["validate", "~/project.json"])
            self.assertEqual(validate.exit_code, 0, validate.output)

            hint_path = self.tmp_path / "hint.json"
            hint_path.write_text(
                GeneratedResponse(success=True, entries=[], violations=[]).model_dump_json(),
                encoding="utf-8",
            )
            generated = self.runner.invoke(
                cli_app,
                [
                    "generate",
                    "~/project.json",
                    "--previous-hint",
                    "~/hint.json",
                    "-f",
                    "json",
                    "-w",
                    "1",
                    "--fast",
                ],
            )
            self.assertEqual(generated.exit_code, 0, generated.output)

    def test_hint_validation_errors(self):
        bad_json = self.tmp_path / "bad_hint.json"
        bad_json.write_text("{not json", encoding="utf-8")
        result = self.runner.invoke(
            cli_app, ["generate", str(self.project_path), "--previous-hint", str(bad_json)]
        )
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Invalid JSON", result.output)

        bad_input = self.tmp_path / "bad_input.json"
        bad_input.write_text("{not json", encoding="utf-8")
        result = self.runner.invoke(cli_app, ["validate", str(bad_input)])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Invalid JSON", result.output)

        bad_model = self.tmp_path / "bad_model_hint.json"
        bad_model.write_text(json.dumps({"success": "yes"}), encoding="utf-8")
        result = self.runner.invoke(
            cli_app, ["generate", str(self.project_path), "--previous-hint", str(bad_model)]
        )
        self.assertEqual(result.exit_code, 1)

    def test_input_and_output_errors(self):
        directory_input = self.runner.invoke(cli_app, ["validate", str(self.tmp_path)])
        self.assertEqual(directory_input.exit_code, 1)

        output_dir = self.tmp_path / "outdir"
        output_dir.mkdir()
        result = self.runner.invoke(
            cli_app,
            ["generate", str(self.project_path), "-o", str(output_dir), "-w", "1", "--fast"],
        )
        self.assertEqual(result.exit_code, 1)

        schema_result = self.runner.invoke(
            cli_app, ["schema", "-o", str(output_dir)]
        )
        self.assertEqual(schema_result.exit_code, 1)

    def test_resolve_format(self):
        self.assertEqual(_resolve_format(None, None), OutputFormat.table)
        self.assertEqual(_resolve_format(None, Path("x.JSON")), OutputFormat.json)
        self.assertEqual(_resolve_format(None, Path("x.csv")), OutputFormat.csv)
        self.assertEqual(_resolve_format(None, Path("x.txt")), OutputFormat.table)
        self.assertEqual(
            _resolve_format(OutputFormat.table, Path("x.json")), OutputFormat.table
        )

    def test_explicit_format_overrides_suffix(self):
        output = self.tmp_path / "result.txt"
        result = self.runner.invoke(
            cli_app,
            [
                "generate",
                str(self.project_path),
                "-o",
                str(output),
                "-f",
                "json",
                "-w",
                "1",
                "--fast",
            ],
        )
        self.assertEqual(result.exit_code, 0, result.output)
        parsed = GeneratedResponse.model_validate_json(output.read_text(encoding="utf-8"))
        self.assertTrue(parsed.success)

    def test_table_stdout_and_no_violations(self):
        result = self.runner.invoke(
            cli_app, ["generate", str(self.project_path), "-f", "table", "-w", "1", "--fast"]
        )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Timetable", result.output)

        quiet = self.runner.invoke(
            cli_app,
            [
                "generate",
                str(self.project_path),
                "-f",
                "table",
                "--no-violations",
                "-w",
                "1",
                "--fast",
            ],
        )
        self.assertEqual(quiet.exit_code, 0, quiet.output)
        self.assertNotIn("Violations", quiet.output)

    def test_print_helpers_empty_and_violations(self):
        recorder = Console(record=True, width=120)
        _print_entries_table(GeneratedResponse(success=True, entries=[], violations=[]), recorder)
        self.assertIn("No entries to display", recorder.export_text())

        recorder = Console(record=True, width=120)
        _print_violations(GeneratedResponse(success=True, entries=[], violations=[]), recorder)
        self.assertIn("No violations", recorder.export_text())

        recorder = Console(record=True, width=120)
        _print_violations(
            GeneratedResponse(
                success=True,
                entries=[],
                violations=[ViolationOut(error_msg="Broken", val=2)],
            ),
            recorder,
        )
        text = recorder.export_text()
        self.assertIn("Broken", text)
        self.assertIn("2", text)

    def test_loader_helpers_accept_plain_paths(self):
        loaded = _load_input(self.project_path)
        self.assertEqual(loaded.project.name, "Tiny")
        self.assertIsNone(_load_hint_input(None))


class TuiEdgeCaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_initial_status_is_unsaved(self):
        app = TimetableApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            status = str(app.query_one("#status", Static).content)
            self.assertIn("unsaved", status)

    async def test_new_and_load_reset_table_search(self):
        app = TimetableApp()
        async with app.run_test() as pilot:
            app.action_load_sample()
            await pilot.pause()
            app._table_search["room"] = "zzz"
            app._refresh_entity("room")
            await pilot.pause()
            self.assertEqual(app.query_one("#table-room", DataTable).row_count, 0)

            app.dirty = False
            app._new()
            await pilot.pause()
            self.assertTrue(all(value == "" for value in app._table_search.values()))

            app._table_search["room"] = "zzz"
            app._load_path(ROOT / "test_json.json")
            await pilot.pause()
            self.assertTrue(all(value == "" for value in app._table_search.values()))

    async def test_invalid_load_returns_false_and_keeps_state(self):
        app = TimetableApp()
        async with app.run_test() as pilot:
            app.action_load_sample()
            await pilot.pause()
            before = app.data
            with tempfile.TemporaryDirectory() as tmp:
                bad = Path(tmp) / "bad.json"
                bad.write_text("{not json", encoding="utf-8")
                self.assertFalse(app._load_path(bad))
                await pilot.pause()
                self.assertEqual(app.data, before)

    async def test_export_paths_expand_home(self):
        app = TimetableApp()
        async with app.run_test() as pilot:
            app.response = one_entry_response()
            with tempfile.TemporaryDirectory() as tmp:
                with patch.dict(os.environ, {"HOME": tmp, "USERPROFILE": tmp}):
                    app._do_export_csv(Path("~/out.csv"))
                    app._do_export_json(Path("~/out.json"))
                self.assertTrue((Path(tmp) / "out.csv").exists())
                self.assertTrue((Path(tmp) / "out.json").exists())

    async def test_subject_fixed_slots_roundtrip(self):
        app = TimetableApp()
        async with app.run_test() as pilot:
            app._apply_form("room", {"name": "R1", "is_lab": False, "capacity": 1}, None)
            app._apply_form(
                "subject",
                {
                    "name": "Minor",
                    "morning_tendency": Level.Low.value,
                    "max_per_day": 3,
                    "min_per_day": 0,
                    "max_per_week": 6,
                    "min_per_week": 6,
                    "max_consecutive": 3,
                    "min_consecutive": 0,
                    "fixed_slots": "Tue:2-3",
                },
                None,
            )
            subject = app.data.subjects[0]
            self.assertEqual(len(subject.constraints.fixed_slots), 2)
            values = app._values("subject", subject)
            self.assertEqual(values["fixed_slots"], "Tue:2, Tue:3")
            app._apply_form("subject", {**values, "fixed_slots": "Mon:1"}, subject)
            self.assertEqual(
                app.data.subjects[0].constraints.fixed_slots,
                [DaySlot(day=WeekDay.Mon, slot=1)],
            )

    async def test_subject_fixed_slots_on_break_rejected(self):
        app = TimetableApp()
        async with app.run_test() as pilot:
            app.data.project.break_slots = [2]
            error = app._validate_entity_form(
                "subject",
                {
                    "name": "Minor",
                    "morning_tendency": Level.Low.value,
                    "max_per_day": 3,
                    "min_per_day": 0,
                    "max_per_week": 6,
                    "min_per_week": 0,
                    "max_consecutive": 3,
                    "min_consecutive": 0,
                    "fixed_slots": "Mon:2",
                },
                None,
            )
            self.assertIsNotNone(error)
            self.assertIn("break slot", error)

    async def test_project_edit_validation(self):
        app = TimetableApp()
        async with app.run_test() as pilot:
            app._edit_project()
            await pilot.pause()
            self.assertIsInstance(app.screen, FormScreen)
            app.screen.query_one("#field-slots", Input).value = "0"
            await pilot.click("#save")
            await pilot.pause()
            self.assertIsInstance(app.screen, FormScreen)

            app.screen.query_one("#field-slots", Input).value = "8"
            days = app.screen.query_one("#field-days", SelectionList)
            days.deselect_all()
            await pilot.click("#save")
            await pilot.pause()
            self.assertIsInstance(app.screen, FormScreen)
            await pilot.press("escape")
            await pilot.pause()

    async def test_assignment_duplicate_tag_rejected(self):
        app = TimetableApp()
        async with app.run_test() as pilot:
            app.action_load_sample()
            await pilot.pause()
            original = app.data.teacher_assignments[0]
            values = app._values("assignment", original)
            error = app._validate_entity_form("assignment", values, None)
            self.assertIsNotNone(error)
            self.assertIn("already exists", error)

    async def test_delete_room_clears_target_room(self):
        app = TimetableApp()
        async with app.run_test() as pilot:
            home = RoomInput(name="Home")
            target = RoomInput(name="Target")
            class_ = ClassInput(name="C1", room_id=home.id)
            teacher = TeacherInput(
                name="T1",
                constraints=TeacherConstraints(
                    max_per_day=4, max_per_week=10, max_consecutive=2
                ),
            )
            subject = SubjectInput(
                name="Math",
                constraints=SubjectConstraints(min_per_week=2, max_per_week=4),
            )
            assignment = TeacherAssignmentInput(
                class_id=class_.id,
                teacher_id=teacher.id,
                subject_id=subject.id,
                target_room_id=target.id,
            )
            app.data = TimeTableGenerationInput(
                rooms=[home, target],
                classes=[class_],
                teachers=[teacher],
                subjects=[subject],
                teacher_assignments=[assignment],
            )
            app._refresh_all()
            await pilot.pause()

            target_index = next(
                index for index, room in enumerate(app._rows["room"]) if room.id == target.id
            )
            app.query_one("#table-room", DataTable).move_cursor(row=target_index)
            app._delete("room")
            await pilot.pause()
            self.assertIsInstance(app.screen, ConfirmScreen)
            await pilot.click("#ok")
            await pilot.pause()
            self.assertNotIn(target.id, {room.id for room in app.data.rooms})
            self.assertIsNone(app.data.teacher_assignments[0].target_room_id)

    async def test_generate_option_validation_does_not_start_solver(self):
        app = TimetableApp()
        async with app.run_test() as pilot:
            app.action_load_sample()
            await pilot.pause()
            for field_id, value in (
                ("#opt-time", "0"),
                ("#opt-time", "abc"),
                ("#opt-workers", "0"),
                ("#opt-patience", "-1"),
                ("#opt-gap", "-0.1"),
            ):
                with self.subTest(field=field_id, value=value):
                    app.query_one(field_id, Input).value = value
                    app.action_generate()
                    await pilot.pause()
                    self.assertIsNone(app.response)
                    self.assertFalse(app.query_one("#generate").disabled)

    async def test_solve_error_modal(self):
        app = TimetableApp()
        async with app.run_test() as pilot:
            app._on_solve_error(RuntimeError("boom"))
            await pilot.pause()
            self.assertTrue(any(isinstance(screen, MessageScreen) for screen in app.screen_stack))
            self.assertFalse(app.query_one("#generate").disabled)

    async def test_stale_response_marker(self):
        app = TimetableApp()
        async with app.run_test() as pilot:
            app.action_load_sample()
            await pilot.pause()
            response = generate(app.data, time_limit_sec=2, workers=1)
            app._on_solved(response, app._revision)
            await pilot.pause()
            app._apply_form(
                "room",
                {
                    "name": "Renamed Room",
                    "is_lab": False,
                    "capacity": 1,
                },
                app.data.rooms[0],
            )
            await pilot.pause()
            self.assertTrue(app.response_stale)
            summary = str(app.query_one("#tt-summary", Static).content)
            self.assertIn("earlier project state", summary)

    async def test_empty_response_all_views(self):
        app = TimetableApp()
        async with app.run_test() as pilot:
            app._on_solved(GeneratedResponse(success=True, entries=[], violations=[]))
            await pilot.pause()
            for mode in ("class_grid", "teacher_grid", "room_grid", "list"):
                app.query_one("#tt-view-mode", Select).value = mode
                await pilot.pause()
                self.assertEqual(app.query_one("#table-timetable", DataTable).row_count, 0)

    async def test_form_screen_required_and_number_validation(self):
        app = TimetableApp()
        async with app.run_test() as pilot:
            result = {}
            fields = [Field("name", "Name"), Field("count", "Count", "int")]
            app.push_screen(
                FormScreen("Test", fields, {"name": "", "count": ""}),
                lambda values: result.setdefault("values", values),
            )
            await pilot.pause()

            await pilot.click("#save")
            await pilot.pause()
            self.assertIsInstance(app.screen, FormScreen)

            app.screen.query_one("#field-name", Input).value = "ok"
            app.screen.query_one("#field-count", Input).value = "abc"
            await pilot.click("#save")
            await pilot.pause()
            self.assertIsInstance(app.screen, FormScreen)

            app.screen.query_one("#field-count", Input).value = "3"
            await pilot.click("#save")
            await pilot.pause()
            self.assertEqual(result["values"], {"name": "ok", "count": 3})

    async def test_prompt_screen_returns_typed_value(self):
        app = TimetableApp()
        async with app.run_test() as pilot:
            result = {}
            app.push_screen(
                PromptScreen("Prompt", "Value:", "default"),
                lambda value: result.setdefault("value", value),
            )
            await pilot.pause()
            app.screen.query_one("#prompt-input", Input).value = "typed"
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(result["value"], "typed")

    async def test_commit_rollback_and_validation_messages(self):
        app = TimetableApp()
        async with app.run_test() as pilot:
            app._apply_form("room", {"name": "R1", "is_lab": False, "capacity": 1}, None)
            before = app.data.model_dump()

            def mutate() -> None:
                app.data.rooms.append(RoomInput(name="r1"))

            self.assertFalse(app._commit(mutate))
            await pilot.pause()
            self.assertEqual(app.data.model_dump(), before)
            self.assertTrue(any(isinstance(screen, MessageScreen) for screen in app.screen_stack))

    async def test_co_schedule_links_are_symmetric(self):
        app = TimetableApp()
        async with app.run_test() as pilot:
            room = RoomInput(name="R1")
            class_ = ClassInput(name="C1", room_id=room.id)
            teacher = TeacherInput(
                name="T1",
                constraints=TeacherConstraints(
                    max_per_day=4, max_per_week=10, max_consecutive=2
                ),
            )
            subject = SubjectInput(
                name="Math", constraints=SubjectConstraints(min_per_week=2, max_per_week=4)
            )
            first = TeacherAssignmentInput(
                class_id=class_.id, teacher_id=teacher.id, subject_id=subject.id
            )
            second = TeacherAssignmentInput(
                class_id=class_.id, teacher_id=teacher.id, subject_id=subject.id, tag="B"
            )
            first.constraints.co_schedule_with = [second.id]
            app.data = TimeTableGenerationInput(
                rooms=[room],
                classes=[class_],
                teachers=[teacher],
                subjects=[subject],
                teacher_assignments=[first, second],
            )
            app._sync_co_schedule_links(app.data.teacher_assignments, first)
            self.assertIn(first.id, second.constraints.co_schedule_with)
            second.constraints.co_schedule_with = []
            first.constraints.co_schedule_with = []
            app._sync_co_schedule_links(app.data.teacher_assignments, first)
            self.assertNotIn(first.id, second.constraints.co_schedule_with)


if __name__ == "__main__":
    unittest.main()
