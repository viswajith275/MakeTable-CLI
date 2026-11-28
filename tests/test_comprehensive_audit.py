"""Comprehensive edge-case and regression tests across models, solver, CLI, and TUI."""

import csv
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from pydantic import ValidationError
from rich.console import Console
from textual.widgets import Button, DataTable, Input, Select, Static, TabbedContent
from typer.testing import CliRunner

from maketable import (
    ClassConstraints,
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
    TimeTableGenerator,
    ViolationOut,
    WeekDay,
    format_day_slots,
    generate,
    parse_day_slots,
    validate_project,
)
from maketable.cli import (
    OutputFormat,
    _load_hint_input,
    _load_input,
    _print_entries_table,
    _print_violations,
    _resolve_format,
    _write_file,
    _write_output,
    app as cli_app,
)
from maketable.models import (
    MAX_SLOTS_PER_DAY,
    _co_schedule_roots,
    class_weekly_requirements,
    csv_safe,
    valid_block_starts,
)
from maketable.tui import (
    ConfirmScreen,
    Field,
    FormScreen,
    HelpScreen,
    MessageScreen,
    PromptScreen,
    TimetableApp,
    format_error,
)


def _make_project(
    *,
    slots: int = 4,
    days: list[WeekDay] | None = None,
    break_slots: list[int] | None = None,
) -> TimeTableGenerationInput:
    active_days = days or [WeekDay.Mon, WeekDay.Tue]
    active_breaks = break_slots or []
    total_slots = max(1, (slots - len(active_breaks)) * len(active_days))
    room = RoomInput(name="R1")
    class_ = ClassInput(name="C1", room_id=room.id)
    teacher = TeacherInput(
        name="T1",
        constraints=TeacherConstraints(
            max_per_day=slots, max_per_week=total_slots, max_consecutive=slots
        ),
    )
    subject = SubjectInput(
        name="Math",
        constraints=SubjectConstraints(
            max_per_day=slots,
            min_per_day=0,
            max_per_week=total_slots,
            min_per_week=min(2, total_slots),
            max_consecutive=slots,
            min_consecutive=0,
        ),
    )
    assignment = TeacherAssignmentInput(
        class_id=class_.id, teacher_id=teacher.id, subject_id=subject.id
    )
    return TimeTableGenerationInput(
        project=ProjectInput(
            name="Audit School",
            slots=slots,
            days=active_days,
            break_slots=active_breaks,
        ),
        rooms=[room],
        classes=[class_],
        teachers=[teacher],
        subjects=[subject],
        teacher_assignments=[assignment],
    )


class ModelsComprehensiveTests(unittest.TestCase):
    def test_parse_day_slots_deduplicates_preserving_order(self):
        parsed = parse_day_slots("Mon:1-3, Mon:2, mon:1; Tue:4, Tue:4")
        self.assertEqual(
            [(s.day, s.slot) for s in parsed],
            [
                (WeekDay.Mon, 1),
                (WeekDay.Mon, 2),
                (WeekDay.Mon, 3),
                (WeekDay.Tue, 4),
            ],
        )

    def test_parse_day_slots_whitespace_and_empty_chunks(self):
        parsed = parse_day_slots(" , ; Mon : 1 - 2 ; ")
        self.assertEqual(
            [(s.day, s.slot) for s in parsed],
            [(WeekDay.Mon, 1), (WeekDay.Mon, 2)],
        )
        self.assertEqual(MAX_SLOTS_PER_DAY, 24)

    def test_valid_block_starts_boundary_matrix(self):
        self.assertEqual(valid_block_starts(0, [], 1), [])
        self.assertEqual(valid_block_starts(3, [], 3), [0])
        self.assertEqual(valid_block_starts(6, [3], 2), [0, 3, 4])
        self.assertEqual(valid_block_starts(6, [3], 3), [3])
        self.assertEqual(valid_block_starts(6, [2, 5], 3), [])

    def test_co_schedule_roots_transitive_and_unknown_refs(self):
        cid, tid, sid = uuid4(), uuid4(), uuid4()
        a1 = TeacherAssignmentInput(class_id=cid, teacher_id=tid, subject_id=sid, tag="A")
        a2 = TeacherAssignmentInput(class_id=cid, teacher_id=tid, subject_id=sid, tag="B")
        a3 = TeacherAssignmentInput(class_id=cid, teacher_id=tid, subject_id=sid, tag="C")
        a4 = TeacherAssignmentInput(class_id=cid, teacher_id=tid, subject_id=sid, tag="D")
        # Asymmetric chain: a1 -> a2 -> a3, plus an unknown UUID ignored safely
        a1.constraints.co_schedule_with = [a2.id, uuid4()]
        a2.constraints.co_schedule_with = [a3.id]

        roots = _co_schedule_roots([a1, a2, a3, a4])
        self.assertEqual(roots[a1.id], roots[a2.id])
        self.assertEqual(roots[a2.id], roots[a3.id])
        self.assertNotEqual(roots[a1.id], roots[a4.id])

    def test_class_weekly_requirements_ignores_unknown_subject_and_counts_splits(self):
        cid, tid = uuid4(), uuid4()
        s1 = SubjectInput(name="S1", constraints=SubjectConstraints(min_per_week=3, max_per_week=4))
        s2 = SubjectInput(name="S2", constraints=SubjectConstraints(min_per_week=5, max_per_week=5))
        a1 = TeacherAssignmentInput(class_id=cid, teacher_id=tid, subject_id=s1.id, tag="1")
        a2 = TeacherAssignmentInput(class_id=cid, teacher_id=tid, subject_id=s2.id, tag="2")
        a3 = TeacherAssignmentInput(class_id=cid, teacher_id=tid, subject_id=uuid4(), tag="3")
        a1.constraints.co_schedule_with = [a2.id]
        a2.constraints.co_schedule_with = [a1.id]

        reqs = class_weekly_requirements([a1, a2, a3], [s1, s2])
        self.assertEqual(reqs[cid], 5)

    def test_generated_response_grid_order_and_plain_dicts(self):
        aid = uuid4()
        resp = GeneratedResponse(
            success=True,
            violations=[],
            entries=[
                TimeTableEntryOutput(
                    assignment_id=aid,
                    class_name="C1",
                    room_name="R1",
                    subject_name="Math",
                    teacher_name="T1",
                    day=WeekDay.Fri,
                    slot=2,
                ),
                TimeTableEntryOutput(
                    assignment_id=aid,
                    class_name="C1",
                    room_name="R1",
                    subject_name="Math",
                    teacher_name="T1",
                    day=WeekDay.Mon,
                    slot=1,
                ),
            ],
        )
        grid = resp.get_class_grid("C1", total_slots=3)
        self.assertEqual(list(grid.keys()), ["Mon", "Fri"])
        self.assertIs(type(grid["Mon"]), dict)
        self.assertNotIn(99, grid["Mon"])

    def test_validate_project_expands_home_path(self):
        proj = _make_project()
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "proj.json"
            p.write_text(proj.model_dump_json(), encoding="utf-8")
            with patch.dict(os.environ, {"HOME": tmp}):
                self.assertEqual(validate_project(Path("~/proj.json")), [])

    def test_model_constraints_and_blank_checks(self):
        self.assertEqual(ClassConstraints().model_dump(), {})
        self.assertEqual(RoomConstraints().capacity, 1)
        self.assertEqual(csv_safe(""), "")
        with self.assertRaises(ValidationError):
            RoomConstraints(capacity=0)
        with self.assertRaises(ValidationError):
            TeacherConstraints(max_per_day=0, max_per_week=1, max_consecutive=1)
        with self.assertRaises(ValidationError):
            TeacherAssignmentConstraint(period_length=0)


class SolverComprehensiveTests(unittest.TestCase):
    def test_duplicate_avoid_and_fixed_slots_do_not_double_count_violations(self):
        proj = _make_project(slots=1, days=[WeekDay.Mon])
        proj.teachers[0].constraints.avoid_slots = [
            DaySlot(day=WeekDay.Mon, slot=1),
            DaySlot(day=WeekDay.Mon, slot=1),
        ]
        proj.subjects[0].constraints.min_per_week = 1
        proj.subjects[0].constraints.max_per_week = 1
        proj.subjects[0].constraints.fixed_slots = [
            DaySlot(day=WeekDay.Mon, slot=1),
            DaySlot(day=WeekDay.Mon, slot=1),
        ]
        resp = generate(proj, time_limit_sec=3, workers=1)
        self.assertTrue(resp.success)
        avoid_v = [v for v in resp.violations if "avoided slot" in v.error_msg]
        self.assertEqual(len(avoid_v), 1)
        self.assertEqual(avoid_v[0].val, 1)

        # Also test duplicate fixed_slots when blocked by unavailability
        proj2 = _make_project(slots=2, days=[WeekDay.Mon])
        proj2.teachers[0].constraints.avoid_slots = []
        proj2.teachers[0].constraints.unavailable_slots = [DaySlot(day=WeekDay.Mon, slot=1)]
        proj2.subjects[0].constraints.min_per_week = 1
        proj2.subjects[0].constraints.max_per_week = 1
        proj2.subjects[0].constraints.fixed_slots = [
            DaySlot(day=WeekDay.Mon, slot=1),
            DaySlot(day=WeekDay.Mon, slot=1),
        ]
        resp2 = generate(proj2, time_limit_sec=3, workers=1)
        self.assertTrue(resp2.success)
        fixed_v = [v for v in resp2.violations if "Fixed slot Mon:1" in v.error_msg]
        self.assertEqual(len(fixed_v), 1)
        self.assertEqual(fixed_v[0].val, 1)

    def test_empty_hint_falls_back_to_greedy_hints(self):
        proj = _make_project(slots=4, days=[WeekDay.Mon, WeekDay.Tue])
        empty_hint = GeneratedResponse(success=False, entries=[], violations=[])
        gen = TimeTableGenerator(RawData=proj, HintData=empty_hint)
        gen._build_and_apply_all_constraints()
        self.assertGreater(len(gen.model.Proto().solution_hint.vars), 0)
        res = gen.solve(time_limit_sec=3, workers=1)
        self.assertTrue(res.success)

    def test_single_slot_single_day_project(self):
        proj = _make_project(slots=1, days=[WeekDay.Mon])
        proj.subjects[0].constraints.min_per_week = 1
        proj.subjects[0].constraints.max_per_week = 1
        resp = generate(proj, time_limit_sec=2, workers=1)
        self.assertTrue(resp.success)
        self.assertEqual(len(resp.entries), 1)

    def test_unreferenced_entities_do_not_produce_false_violations(self):
        proj = _make_project(slots=4, days=[WeekDay.Mon, WeekDay.Tue])
        unused_teacher = TeacherInput(
            name="Unused T",
            constraints=TeacherConstraints(
                max_per_day=1,
                max_per_week=1,
                max_consecutive=1,
                avoid_slots=[DaySlot(day=WeekDay.Mon, slot=1)],
            ),
        )
        unused_subject = SubjectInput(
            name="Unused S",
            constraints=SubjectConstraints(
                min_per_day=1,
                max_per_day=2,
                min_per_week=2,
                max_per_week=2,
                fixed_slots=[DaySlot(day=WeekDay.Mon, slot=1)],
            ),
        )
        proj.teachers.append(unused_teacher)
        proj.subjects.append(unused_subject)
        resp = generate(proj, time_limit_sec=3, workers=1)
        self.assertTrue(resp.success)
        self.assertEqual(resp.violations, [])

    def test_room_capacity_greater_than_one_allows_concurrent_non_co_scheduled_classes(self):
        hall = RoomInput(name="Big Hall", constraints=RoomConstraints(capacity=2))
        c1 = ClassInput(name="C1", room_id=hall.id)
        c2 = ClassInput(name="C2", room_id=hall.id)
        t1 = TeacherInput(
            name="T1", constraints=TeacherConstraints(max_per_day=1, max_per_week=1, max_consecutive=1)
        )
        t2 = TeacherInput(
            name="T2", constraints=TeacherConstraints(max_per_day=1, max_per_week=1, max_consecutive=1)
        )
        s = SubjectInput(
            name="S",
            constraints=SubjectConstraints(
                max_per_day=1, max_per_week=1, min_per_week=1, max_consecutive=1
            ),
        )
        a1 = TeacherAssignmentInput(class_id=c1.id, teacher_id=t1.id, subject_id=s.id)
        a2 = TeacherAssignmentInput(class_id=c2.id, teacher_id=t2.id, subject_id=s.id)
        data = TimeTableGenerationInput(
            project=ProjectInput(slots=1, days=[WeekDay.Mon]),
            rooms=[hall],
            classes=[c1, c2],
            teachers=[t1, t2],
            subjects=[s],
            teacher_assignments=[a1, a2],
        )
        resp = generate(data, time_limit_sec=3, workers=1)
        self.assertTrue(resp.success)
        self.assertEqual(len(resp.entries), 2)

    def test_strict_mode_vs_two_phase_soft_fallback(self):
        proj = _make_project(slots=2, days=[WeekDay.Mon])
        proj.teachers[0].constraints.max_per_day = 1
        proj.subjects[0].constraints.min_per_week = 2
        proj.subjects[0].constraints.max_per_week = 2

        strict_resp = generate(proj, time_limit_sec=2, workers=1, strict=True)
        self.assertFalse(strict_resp.success)
        self.assertEqual(strict_resp.entries, [])

        soft_resp = generate(proj, time_limit_sec=2, workers=1, strict=False)
        self.assertTrue(soft_resp.success)
        self.assertTrue(
            any(
                "Min weekly classes" in v.error_msg or "Max daily classes" in v.error_msg
                for v in soft_resp.violations
            )
        )

    def test_hard_impossible_fast_path_and_pruned_slot_violation_reporting(self):
        proj = _make_project(slots=2, days=[WeekDay.Mon])
        proj.teachers[0].constraints.unavailable_slots = [
            DaySlot(day=WeekDay.Mon, slot=1),
            DaySlot(day=WeekDay.Mon, slot=2),
        ]
        proj.subjects[0].constraints.min_per_day = 1
        proj.subjects[0].constraints.min_per_week = 1
        proj.subjects[0].constraints.max_per_week = 1
        proj.subjects[0].constraints.fixed_slots = [DaySlot(day=WeekDay.Mon, slot=1)]
        proj.teacher_assignments[0].constraints.first_slot_days = [WeekDay.Mon]

        strict_resp = generate(proj, time_limit_sec=2, workers=1, strict=True)
        self.assertFalse(strict_resp.success)

        # Even with all slots pruned, soft mode should still surface missed constraints
        gen = TimeTableGenerator(RawData=proj)
        gen._build_and_apply_all_constraints(hard=False)
        msgs = {s.error_msg for s in gen.error_slacks.values()}
        self.assertTrue(any("Min daily classes" in m for m in msgs))
        self.assertTrue(any("Min weekly classes" in m for m in msgs))
        self.assertTrue(any("Fixed slot Mon:1" in m for m in msgs))
        self.assertTrue(any("First slot required on Mon" in m for m in msgs))

    def test_min_consecutive_skips_when_period_length_covers_it(self):
        proj = _make_project(slots=4, days=[WeekDay.Mon])
        proj.subjects[0].constraints.min_consecutive = 2
        proj.subjects[0].constraints.max_consecutive = 2
        proj.subjects[0].constraints.min_per_week = 2
        proj.subjects[0].constraints.max_per_week = 2
        proj.teacher_assignments[0].constraints.period_length = 2

        gen = TimeTableGenerator(RawData=proj)
        gen._build_and_apply_all_constraints(hard=True)
        var_names = [v.name for v in gen.model.Proto().variables]
        self.assertFalse(any(name.startswith("blk_start_") for name in var_names))
        resp = gen.solve(time_limit_sec=2, workers=1)
        self.assertTrue(resp.success)
        self.assertEqual(len(resp.entries), 2)

    def test_lazy_solver_import_in_package_init(self):
        code = (
            "import sys, maketable; "
            "assert 'ortools' not in sys.modules; "
            "assert 'maketable.solver' not in sys.modules; "
            "cls = maketable.TimeTableGenerator; "
            "assert cls.__name__ == 'TimeTableGenerator'; "
            "assert 'ortools.sat.python.cp_model' in sys.modules"
        )
        res = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_on_phase_callback_reports_hard_and_soft_transitions(self):
        # Clean project -> only hard phase
        clean = _make_project(slots=4, days=[WeekDay.Mon])
        phases_clean = []
        resp_clean = generate(
            clean,
            time_limit_sec=2,
            workers=1,
            on_phase=lambda p, m: phases_clean.append((p, m)),
        )
        self.assertTrue(resp_clean.success)
        self.assertEqual([p for p, _ in phases_clean], ["hard"])

        # Conflicting project -> hard phase followed by soft fallback
        conflict = _make_project(slots=2, days=[WeekDay.Mon])
        conflict.teachers[0].constraints.max_per_day = 1
        conflict.subjects[0].constraints.min_per_week = 2
        conflict.subjects[0].constraints.max_per_week = 2
        phases_soft = []
        resp_soft = generate(
            conflict,
            time_limit_sec=2,
            workers=1,
            on_phase=lambda p, m: phases_soft.append((p, m)),
        )
        self.assertTrue(resp_soft.success)
        self.assertEqual([p for p, _ in phases_soft], ["hard", "soft"])
        self.assertIn("softening", phases_soft[1][1].lower())
        self.assertIn("violations may appear", phases_soft[1][1].lower())

    def test_solver_cancel_event_aborts_promptly(self):
        from testing.mock_data_generator import generate as gen_mock

        data = TimeTableGenerationInput(**gen_mock(num_classes=15, preset="college", seed=7))
        cancel_event = threading.Event()
        timer = threading.Timer(0.15, cancel_event.set)
        timer.start()
        t0 = time.perf_counter()
        try:
            resp = generate(
                data,
                time_limit_sec=20.0,
                workers=2,
                patience=10.0,
                cancel_event=cancel_event,
            )
        finally:
            timer.cancel()
        elapsed = time.perf_counter() - t0
        self.assertFalse(resp.success)
        self.assertLess(elapsed, 3.0)
        self.assertTrue(any("cancelled" in v.error_msg.lower() for v in resp.violations))


class CliComprehensiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.project_path = self.tmp_path / "proj.json"
        self.project_path.write_text(_make_project().model_dump_json(), encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_csv_stdout_has_no_trailing_empty_row(self):
        res = self.runner.invoke(
            cli_app, ["generate", str(self.project_path), "-f", "csv", "--fast", "-w", "1"]
        )
        self.assertEqual(res.exit_code, 0, res.output)
        rows = list(csv.reader(io.StringIO(res.stdout)))
        self.assertGreaterEqual(len(rows), 3)
        self.assertTrue(all(len(row) == 9 for row in rows), rows)

    def test_invalid_solver_cli_flags_rejected(self):
        for args in (
            ["-t", "0"],
            ["-t", "-2"],
            ["-t", "nan"],
            ["-w", "0"],
            ["--patience", "-1"],
            ["--patience", "inf"],
            ["--gap-limit", "-0.1"],
            ["--gap-limit", "nan"],
        ):
            with self.subTest(args=args):
                res = self.runner.invoke(
                    cli_app, ["generate", str(self.project_path), *args]
                )
                self.assertEqual(res.exit_code, 1)

    def test_cli_helpers_direct_coverage(self):
        loaded = _load_input(self.project_path)
        self.assertEqual(loaded.project.name, "Audit School")
        self.assertIsNone(_load_hint_input(None))
        self.assertEqual(_resolve_format(OutputFormat.json, Path("a.csv")), OutputFormat.json)

        out_file = self.tmp_path / "out.txt"
        _write_file(out_file, "hello")
        self.assertEqual(out_file.read_text(encoding="utf-8"), "hello")

        resp = GeneratedResponse(
            success=True,
            entries=[],
            violations=[ViolationOut(error_msg="Warn", val=1)],
        )
        rec = Console(record=True, width=100)
        _print_entries_table(resp, rec)
        _print_violations(resp, rec)
        txt = rec.export_text()
        self.assertIn("No entries to display", txt)
        self.assertIn("Warn", txt)

        # _write_output to json & csv files with violations enabled
        _write_output(resp, self.tmp_path / "r.json", OutputFormat.json, show_violations=True)
        _write_output(resp, self.tmp_path / "r.csv", OutputFormat.csv, show_violations=True)
        self.assertTrue((self.tmp_path / "r.json").exists())
        self.assertTrue((self.tmp_path / "r.csv").exists())

    def test_cli_strict_flag_rejects_soft_only_conflict(self):
        proj = _make_project(slots=2, days=[WeekDay.Mon])
        proj.teachers[0].constraints.max_per_day = 1
        proj.subjects[0].constraints.min_per_week = 2
        proj.subjects[0].constraints.max_per_week = 2
        strict_file = self.tmp_path / "strict.json"
        strict_file.write_text(proj.model_dump_json(), encoding="utf-8")

        res = self.runner.invoke(
            cli_app, ["generate", str(strict_file), "--strict", "-w", "1"]
        )
        self.assertEqual(res.exit_code, 1)


class TuiComprehensiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_tilde_path_loads_existing_project_on_mount(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "school.json"
            p.write_text(_make_project().model_dump_json(), encoding="utf-8")
            with patch.dict(os.environ, {"HOME": tmp}):
                app = TimetableApp(Path("~/school.json"))
                async with app.run_test(size=(100, 30)) as pilot:
                    await pilot.pause()
                    self.assertEqual(len(app.data.rooms), 1)
                    self.assertFalse(app.dirty)
                    self.assertEqual(app.path, p)

    async def test_search_inputs_cleared_on_new_and_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "school.json"
            p.write_text(_make_project().model_dump_json(), encoding="utf-8")

            app = TimetableApp()
            async with app.run_test(size=(100, 30)) as pilot:
                app._load_path(p)
                await pilot.pause()

                app.query_one("#filter-room", Input).value = "R1"
                app.query_one("#tt-filter", Input).value = "Math"
                await pilot.pause()

                app.dirty = False
                app._new()
                await pilot.pause()
                self.assertEqual(app.query_one("#filter-room", Input).value, "")
                self.assertEqual(app.query_one("#tt-filter", Input).value, "")

                app.query_one("#filter-room", Input).value = "R1"
                app.query_one("#tt-filter", Input).value = "Math"
                await pilot.pause()

                app._load_path(p)
                await pilot.pause()
                self.assertEqual(app.query_one("#filter-room", Input).value, "")
                self.assertEqual(app.query_one("#tt-filter", Input).value, "")

    async def test_ctrl_g_from_focused_entity_table_switches_to_timetable_tab(self):
        app = TimetableApp()
        async with app.run_test(size=(100, 30)) as pilot:
            app.data = _make_project()
            app._refresh_all()
            await pilot.pause()

            await pilot.click("#--content-tab-tab-room")
            await pilot.pause()
            app.query_one("#table-room", DataTable).focus()
            await pilot.pause()

            with patch.object(
                TimeTableGenerator,
                "solve",
                lambda self, **kw: GeneratedResponse(success=True, entries=[], violations=[]),
            ):
                await pilot.press("ctrl+g")
                await app.workers.wait_for_complete()
                await pilot.pause()

            self.assertEqual(app.query_one("#tabs", TabbedContent).active, "tab-timetable")

    async def test_failed_solve_summary_does_not_say_solved(self):
        app = TimetableApp()
        async with app.run_test(size=(100, 30)) as pilot:
            app._on_solved(
                GeneratedResponse(
                    success=False,
                    entries=[],
                    violations=[ViolationOut(error_msg="Blocked", val=1)],
                )
            )
            await pilot.pause()
            summary = str(app.query_one("#tt-summary", Static).content)
            self.assertIn("No feasible timetable found.", summary)
            self.assertNotIn("Solved.", summary)

    async def test_form_screen_number_rejects_non_finite_and_format_error(self):
        self.assertEqual(format_error(ValueError("bad")), "bad")
        app = TimetableApp()
        async with app.run_test(size=(100, 30)) as pilot:
            captured = {}
            app.push_screen(
                FormScreen("Num", [Field("rate", "Rate", "number")], {"rate": "1.5"}),
                lambda v: captured.setdefault("val", v),
            )
            await pilot.pause()
            app.screen.query_one("#field-rate", Input).value = "nan"
            await pilot.click("#save")
            await pilot.pause()
            self.assertIsInstance(app.screen, FormScreen)

            app.screen.query_one("#field-rate", Input).value = "2.5"
            app.screen.query_one("#field-rate", Input).focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(captured["val"], {"rate": 2.5})

    async def test_tui_solve_phase_indicators_and_softening_warning(self):
        app = TimetableApp()
        async with app.run_test(size=(100, 30)) as pilot:
            app.data = _make_project()
            app._refresh_all()
            await pilot.pause()

            seen_statuses = []

            def fake_solve(self, **kwargs):
                on_phase = kwargs.get("on_phase")
                self_phases = [
                    ("hard", "Phase 1/2: Solving with hard constraints…"),
                    (
                        "soft",
                        "Phase 2/2: Hard constraints infeasible — softening constraints (violations may appear)…",
                    ),
                ]
                for p, m in self_phases:
                    if on_phase is not None:
                        on_phase(p, m)
                    seen_statuses.append((p, m))
                return GeneratedResponse(
                    success=True,
                    entries=[],
                    violations=[ViolationOut(error_msg="Soft", val=1)],
                )

            with patch.object(TimeTableGenerator, "solve", fake_solve):
                app.action_generate()
                await app.workers.wait_for_complete()
                await pilot.pause()

            self.assertEqual([p for p, _ in seen_statuses], ["hard", "soft"])
            app._on_solve_phase(
                "soft",
                "Phase 2/2: Hard constraints infeasible — softening constraints (violations may appear)…",
            )
            await pilot.pause()
            self.assertIn(
                "softening constraints",
                str(app.query_one("#status", Static).content).lower(),
            )
            self.assertIn(
                "violations may appear",
                str(app.query_one("#tt-summary", Static).content).lower(),
            )

    async def test_tui_cancel_solve_button_aborts_and_restores_ui(self):
        from maketable.models import TimeTableEntryOutput

        app = TimetableApp()
        async with app.run_test(size=(70, 22)) as pilot:
            app.data = _make_project()
            app._refresh_all()
            await pilot.pause()

            # Existing response must be preserved if solve is cancelled
            prev = GeneratedResponse(
                success=True,
                entries=[
                    TimeTableEntryOutput(
                        assignment_id=app.data.teacher_assignments[0].id,
                        class_name="C1",
                        room_name="R1",
                        subject_name="Math",
                        teacher_name="T1",
                        day=WeekDay.Mon,
                        slot=1,
                    )
                ],
                violations=[],
            )
            app._on_solved(prev)
            await pilot.pause()

            cancel_btn = app.query_one("#cancel-solve", Button)
            gen_btn = app.query_one("#generate", Button)
            self.assertFalse(cancel_btn.display)
            self.assertTrue(gen_btn.display)

            started = threading.Event()

            def slow_solve(self, **kwargs):
                cancel_ev = kwargs["cancel_event"]
                started.set()
                cancel_ev.wait(timeout=5.0)
                return GeneratedResponse(success=False, entries=[], violations=[])

            with patch.object(TimeTableGenerator, "solve", slow_solve):
                await pilot.click("#generate")
                await pilot.pause()
                self.assertTrue(started.wait(timeout=2.0))
                self.assertTrue(cancel_btn.display)
                self.assertFalse(cancel_btn.disabled)
                self.assertFalse(gen_btn.display)
                self.assertTrue(gen_btn.disabled)

                await pilot.click("#cancel-solve")
                await app.workers.wait_for_complete()
                await pilot.pause()

            self.assertFalse(cancel_btn.display)
            self.assertTrue(gen_btn.display)
            self.assertFalse(gen_btn.disabled)
            self.assertIs(app.response, prev)


if __name__ == "__main__":
    unittest.main()
