import unittest
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from maketable import (
    ClassInput,
    GeneratedResponse,
    RoomConstraints,
    RoomInput,
    SubjectConstraints,
    SubjectInput,
    TeacherConstraints,
    TeacherInput,
    TeacherAssignmentInput,
    TimeTableEntryOutput,
    TimeTableGenerationInput,
    WeekDay,
    DaySlot,
    generate,
)
from maketable.models import ProjectInput

ROOT = Path(__file__).resolve().parents[1]


def tiny_input() -> TimeTableGenerationInput:
    room = RoomInput(name="R1")
    class_ = ClassInput(name="C1", room_id=room.id)
    teacher = TeacherInput(
        name="T1",
        constraints=TeacherConstraints(
            max_per_day=6, max_per_week=20, max_consecutive=2
        ),
    )
    subject = SubjectInput(
        name="Math",
        constraints=SubjectConstraints(
            max_per_day=1,
            max_per_week=3,
            max_consecutive=1,
            min_per_week=3,
        ),
    )
    assignment = TeacherAssignmentInput(
        class_id=class_.id,
        teacher_id=teacher.id,
        subject_id=subject.id,
    )
    return TimeTableGenerationInput(
        rooms=[room],
        classes=[class_],
        teachers=[teacher],
        subjects=[subject],
        teacher_assignments=[assignment],
    )


class ModelTests(unittest.TestCase):
    def test_sample_file_is_valid(self):
        data = TimeTableGenerationInput.model_validate_json(
            (ROOT / "test_json.json").read_text()
        )
        self.assertEqual(len(data.classes), 3)
        self.assertEqual(len(data.teacher_assignments), 21)

    def test_unknown_room_reference_is_rejected(self):
        data = tiny_input()
        data.classes[0].room_id = uuid4()
        with self.assertRaises(ValidationError):
            TimeTableGenerationInput.model_validate(data.model_dump())

    def test_duplicate_assignment_is_rejected(self):
        data = tiny_input()
        data.teacher_assignments.append(
            data.teacher_assignments[0].model_copy(update={"id": uuid4()})
        )
        with self.assertRaises(ValidationError):
            TimeTableGenerationInput.model_validate(data.model_dump())

    def test_subject_min_cannot_exceed_max(self):
        with self.assertRaises(ValidationError):
            SubjectConstraints(
                max_per_day=1,
                min_per_day=2,
                max_per_week=1,
                max_consecutive=1,
            )

    def test_first_slot_days_defaults_to_none(self):
        assignment = TeacherAssignmentInput(
            class_id=uuid4(), teacher_id=uuid4(), subject_id=uuid4()
        )
        self.assertIsNone(assignment.constraints.first_slot_days)

    def test_project_defaults(self):
        project = ProjectInput()
        self.assertGreater(project.slots, 0)
        self.assertIn(WeekDay.Mon, project.days)

    def test_case_insensitive_duplicate_name_rejected(self):
        data = tiny_input()
        data.rooms.append(RoomInput(name="r1"))  # duplicate of "R1"
        with self.assertRaises(ValidationError) as ctx:
            TimeTableGenerationInput.model_validate(data.model_dump())
        self.assertIn("duplicate room names", str(ctx.exception))

    def test_period_length_must_fit_between_breaks(self):
        data = tiny_input()
        data.project.slots = 4
        data.project.break_slots = [2]
        data.teachers[0].constraints.max_per_day = 4
        data.teachers[0].constraints.max_per_week = 12
        data.teacher_assignments[0].constraints.period_length = 3
        with self.assertRaises(ValidationError) as ctx:
            TimeTableGenerationInput.model_validate(data.model_dump())
        self.assertIn("does not fit", str(ctx.exception))

    def test_oversized_day_slot_range_is_rejected(self):
        from maketable import parse_day_slots

        with self.assertRaises(ValueError):
            parse_day_slots("Mon:1-99999999")

    def test_teacher_max_per_day_exceeding_slots_rejected(self):
        data = tiny_input()
        data.teachers[0].constraints.max_per_day = 10  # slots is 8
        with self.assertRaises(ValidationError) as ctx:
            TimeTableGenerationInput.model_validate(data.model_dump())
        self.assertIn("exceeding project slots per day", str(ctx.exception))

    def test_class_over_allocation_rejected(self):
        data = tiny_input()
        data.project.slots = 4
        data.teachers[0].constraints.max_per_day = 4
        data.teachers[0].constraints.max_per_week = 20
        # Week has 4 * 5 = 20 slots.
        # Math requires 12. Add Physics requiring 12.
        # Both individually <= 20, but together require 24 > 20.
        data.subjects[0].constraints.max_per_day = 4
        data.subjects[0].constraints.max_per_week = 15
        data.subjects[0].constraints.min_per_week = 12
        sub2 = SubjectInput(
            name="Physics",
            constraints=SubjectConstraints(max_per_day=4, max_per_week=15, min_per_week=12)
        )
        data.subjects.append(sub2)
        data.teacher_assignments.append(
            TeacherAssignmentInput(
                class_id=data.classes[0].id,
                teacher_id=data.teachers[0].id,
                subject_id=sub2.id,
            )
        )
        with self.assertRaises(ValidationError) as ctx:
            TimeTableGenerationInput.model_validate(data.model_dump())
        self.assertIn("exceeds the total available weekly slots", str(ctx.exception))

    def test_validate_project_helper(self):
        from maketable import validate_project

        data = tiny_input()
        errors = validate_project(data)
        self.assertEqual(errors, [])

        data.classes[0].room_id = uuid4()
        errors = validate_project(data)
        self.assertTrue(len(errors) > 0)

    def test_co_scheduled_classes_must_fit_room_capacity(self):
        room = RoomInput(name="Hall", constraints=RoomConstraints(capacity=1))
        class_a = ClassInput(name="A", room_id=room.id)
        class_b = ClassInput(name="B", room_id=room.id)
        teacher = TeacherInput(
            name="T",
            constraints=TeacherConstraints(
                max_per_day=2, max_per_week=3, max_consecutive=2
            ),
        )
        subject = SubjectInput(
            name="S",
            constraints=SubjectConstraints(max_per_day=1, max_per_week=3),
        )
        assign_a = TeacherAssignmentInput(
            class_id=class_a.id, teacher_id=teacher.id, subject_id=subject.id
        )
        assign_b = TeacherAssignmentInput(
            class_id=class_b.id, teacher_id=teacher.id, subject_id=subject.id
        )
        assign_a.constraints.co_schedule_with = [assign_b.id]
        assign_b.constraints.co_schedule_with = [assign_a.id]

        with self.assertRaises(ValidationError) as ctx:
            TimeTableGenerationInput(
                rooms=[room],
                classes=[class_a, class_b],
                teachers=[teacher],
                subjects=[subject],
                teacher_assignments=[assign_a, assign_b],
            )
        self.assertIn("share room 'Hall' with capacity 1", str(ctx.exception))

    def test_first_slot_requirement_with_break_slot_rejected(self):
        data = tiny_input()
        data.project.break_slots = [1]
        data.teacher_assignments[0].constraints.first_slot_days = [WeekDay.Mon]
        with self.assertRaises(ValidationError) as ctx:
            TimeTableGenerationInput.model_validate(data.model_dump())
        self.assertIn("slot 1 is a project break slot", str(ctx.exception))


class SolverTests(unittest.TestCase):
    def test_no_entries_reports_failure(self):
        data = tiny_input()
        data.teachers[0].constraints.unavailable_slots = [
            DaySlot(day=day, slot=slot)
            for day in data.project.days
            for slot in range(1, data.project.slots + 1)
        ]
        response = generate(data, time_limit_sec=2, workers=1)
        self.assertFalse(response.success)
        self.assertEqual(response.entries, [])
        self.assertTrue(response.violations)

    def test_tiny_timetable(self):
        response = generate(tiny_input(), time_limit_sec=3, workers=1)
        self.assertTrue(response.success)
        self.assertEqual(len(response.entries), 3)
        for entry in response.entries:
            self.assertGreaterEqual(entry.slot, 1)
            self.assertLessEqual(entry.slot, 8)

    def test_unknown_hint_is_ignored(self):
        hint = GeneratedResponse(
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
            violations=[],
            success=True,
        )
        response = generate(tiny_input(), time_limit_sec=3, workers=1, hint=hint)
        self.assertTrue(response.success)

    def test_fast_and_stopping_options(self):
        # Fast mode must still produce a usable timetable, and the stopping
        # knobs must be accepted by both the solver and the library wrapper.
        response = generate(tiny_input(), time_limit_sec=3, workers=1, fast=True)
        self.assertTrue(response.success)
        self.assertEqual(len(response.entries), 3)

        response = generate(
            tiny_input(),
            time_limit_sec=3,
            workers=1,
            gap_limit=0.05,
            patience=0,
        )
        self.assertTrue(response.success)

    def test_response_grid_and_csv_methods(self):
        response = generate(tiny_input(), time_limit_sec=3, workers=1)
        self.assertTrue(response.success)

        classes = response.get_classes()
        self.assertEqual(classes, ["C1"])

        teachers = response.get_teachers()
        self.assertEqual(teachers, ["T1"])

        rooms = response.get_rooms()
        self.assertEqual(rooms, ["R1"])

        grid = response.get_class_grid("C1", total_slots=8)
        self.assertIn("Mon", grid)
        self.assertEqual(len(grid["Mon"]), 8)

        csv_str = response.to_csv()
        self.assertIn("C1", csv_str)
        self.assertIn("Math", csv_str)

    def test_multiple_teachers_sharing_subject_in_class(self):
        data = tiny_input()
        t2 = TeacherInput(
            name="T2",
            constraints=TeacherConstraints(max_per_day=5, max_per_week=15, max_consecutive=2)
        )
        data.teachers.append(t2)
        # Second assignment for same class and same subject with teacher T2
        data.teacher_assignments.append(
            TeacherAssignmentInput(
                class_id=data.classes[0].id,
                teacher_id=t2.id,
                subject_id=data.subjects[0].id,
            )
        )
        response = generate(data, time_limit_sec=3, workers=1)
        self.assertTrue(response.success)
        self.assertGreaterEqual(len(response.entries), 3)

    def test_subject_violation_contains_class_name(self):
        data = tiny_input()
        # Subject requires min 2 per day, but teacher can only do max 1 per day
        data.subjects[0].constraints.min_per_day = 2
        data.subjects[0].constraints.max_per_day = 2
        data.teachers[0].constraints.max_per_day = 1
        response = generate(data, time_limit_sec=3, workers=1)
        self.assertTrue(response.success)
        self.assertTrue(len(response.violations) > 0)
        # Verify the class name "C1" is in the violation error message
        self.assertTrue(any("C1:" in v.error_msg for v in response.violations))

    def test_break_slots_are_not_counted_as_gaps(self):
        from ortools.sat.python import cp_model
        from maketable.solver import TimeTableGenerator

        room = RoomInput(name="R")
        class_ = ClassInput(name="C", room_id=room.id)
        teacher = TeacherInput(
            name="T",
            constraints=TeacherConstraints(
                max_per_day=2, max_per_week=2, max_consecutive=2
            ),
        )
        subject = SubjectInput(
            name="S",
            constraints=SubjectConstraints(
                max_per_day=2, max_per_week=2, min_per_week=2, max_consecutive=2
            ),
        )
        assignment = TeacherAssignmentInput(
            class_id=class_.id, teacher_id=teacher.id, subject_id=subject.id
        )
        # Slots 1 and 3 are the only working slots; a break at 2 must not be
        # charged as an idle gap between them.
        data = TimeTableGenerationInput(
            project=ProjectInput(
                slots=3, days=[WeekDay.Mon], break_slots=[2]
            ),
            rooms=[room],
            classes=[class_],
            teachers=[teacher],
            subjects=[subject],
            teacher_assignments=[assignment],
        )

        generator = TimeTableGenerator(RawData=data)
        generator._build_and_apply_all_constraints()
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 5
        solver.parameters.num_search_workers = 1
        status = solver.solve(generator.model)

        self.assertEqual(status, cp_model.OPTIMAL)
        # Only the two scheduled periods contribute: -10 per period.
        self.assertEqual(solver.objective_value, -20.0)

    def test_get_room_grid_accepts_total_slots(self):
        response = GeneratedResponse(
            success=True,
            entries=[
                TimeTableEntryOutput(
                    assignment_id=uuid4(),
                    class_name="C1",
                    teacher_name="T1",
                    subject_name="Math",
                    room_name="R1",
                    day=WeekDay.Mon,
                    slot=1,
                )
            ],
            violations=[],
        )
        grid = response.get_room_grid("R1", total_slots=6)
        self.assertEqual(list(grid[WeekDay.Mon.value].keys()), [1, 2, 3, 4, 5, 6])
        self.assertEqual(len(grid[WeekDay.Mon.value][1]), 1)
        self.assertEqual(grid[WeekDay.Mon.value][6], [])

    def test_build_once_idempotent_with_solve(self):
        from maketable.solver import TimeTableGenerator

        data = tiny_input()
        gen = TimeTableGenerator(data)
        gen._build_and_apply_all_constraints()
        vars_before = len(gen.model.Proto().variables)
        ctrs_before = len(gen.model.Proto().constraints)

        gen._build_and_apply_all_constraints()
        self.assertEqual(len(gen.model.Proto().variables), vars_before)
        self.assertEqual(len(gen.model.Proto().constraints), ctrs_before)

        res = gen.solve(time_limit_sec=3, workers=1)
        self.assertTrue(res.success)
        self.assertEqual(len(gen.model.Proto().variables), vars_before)

    def test_scalable_15_classes_solve(self):
        from testing.mock_data_generator import generate as generate_mock, validate

        raw = generate_mock(
            num_rooms=18,
            num_classes=15,
            num_subjects=6,
            slots=6,
            seed=42,
        )
        self.assertEqual(validate(raw), [])
        data = TimeTableGenerationInput(**raw)
        res = generate(data, time_limit_sec=4, seed=42, workers=4, fast=True)
        self.assertTrue(res.success)
        self.assertGreater(len(res.entries), 150)

    def test_mock_generator_presets_are_valid_and_feature_rich(self):
        from testing.mock_data_generator import generate as generate_mock, validate

        for preset in ("school", "college", "pressure"):
            raw = generate_mock(num_classes=4, preset=preset, seed=7)
            self.assertEqual(validate(raw), [])
            data = TimeTableGenerationInput(**raw)
            if preset in ("college", "pressure"):
                self.assertTrue(data.project.break_slots)
                self.assertTrue(any(a.constraints.co_schedule_with for a in data.teacher_assignments))
                self.assertTrue(any(a.constraints.period_length > 1 for a in data.teacher_assignments))


class CliTests(unittest.TestCase):
    def test_format_inferred_from_output_suffix(self):
        from maketable.cli import OutputFormat, _resolve_format

        self.assertIs(_resolve_format(None, Path("out.json")), OutputFormat.json)
        self.assertIs(_resolve_format(None, Path("out.CSV")), OutputFormat.csv)
        self.assertIs(_resolve_format(None, Path("out.txt")), OutputFormat.table)
        self.assertIs(_resolve_format(None, None), OutputFormat.table)
        # An explicit --format always wins over the suffix.
        self.assertIs(
            _resolve_format(OutputFormat.csv, Path("out.json")), OutputFormat.csv
        )


class TuiImportTests(unittest.TestCase):
    def test_tui_imports(self):
        from maketable.tui import TimetableApp

        self.assertTrue(callable(TimetableApp))


if __name__ == "__main__":
    unittest.main()
