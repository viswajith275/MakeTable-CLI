import csv
import io
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

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
    WeekDay,
    format_day_slots,
    parse_day_slots,
    validate_project,
)
from maketable.models import class_weekly_requirements, csv_safe, valid_block_starts


def _make_base_project() -> TimeTableGenerationInput:
    room = RoomInput(name="Room 101")
    class_ = ClassInput(name="Grade 10", room_id=room.id)
    teacher = TeacherInput(
        name="Ms. Ada",
        constraints=TeacherConstraints(max_per_day=4, max_per_week=15, max_consecutive=2),
    )
    subject = SubjectInput(
        name="Math",
        constraints=SubjectConstraints(
            morning_tendency=Level.Med,
            max_per_day=2,
            min_per_day=0,
            max_per_week=5,
            min_per_week=3,
            max_consecutive=2,
            min_consecutive=1,
        ),
    )
    assignment = TeacherAssignmentInput(
        class_id=class_.id,
        teacher_id=teacher.id,
        subject_id=subject.id,
    )
    return TimeTableGenerationInput(
        project=ProjectInput(name="Test School", slots=6, days=[WeekDay.Mon, WeekDay.Tue, WeekDay.Wed]),
        rooms=[room],
        classes=[class_],
        teachers=[teacher],
        subjects=[subject],
        teacher_assignments=[assignment],
    )


class HelperFunctionsTests(unittest.TestCase):
    def test_parse_day_slots_valid_formats(self):
        self.assertEqual(parse_day_slots(""), [])
        self.assertEqual(parse_day_slots("   "), [])
        parsed = parse_day_slots("mon:1; WED:2-4, fri:6,")
        self.assertEqual(
            [(s.day, s.slot) for s in parsed],
            [
                (WeekDay.Mon, 1),
                (WeekDay.Wed, 2),
                (WeekDay.Wed, 3),
                (WeekDay.Wed, 4),
                (WeekDay.Fri, 6),
            ],
        )
        self.assertEqual(str(parsed[0]), "Mon:1")
        self.assertEqual(format_day_slots(parsed), "Mon:1, Wed:2, Wed:3, Wed:4, Fri:6")

    def test_parse_day_slots_invalid_inputs(self):
        for bad in (
            "Mon1",
            "Funday:1",
            "Mon:0",
            "Mon:25",
            "Mon:4-2",
            "Mon:0-2",
            "Mon:2-25",
            "Mon:abc",
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    parse_day_slots(bad)

    def test_valid_block_starts(self):
        self.assertEqual(valid_block_starts(5, [], 0), [])
        self.assertEqual(valid_block_starts(5, [3], 1), [0, 1, 3, 4])
        self.assertEqual(valid_block_starts(5, [3], 2), [0, 3])
        self.assertEqual(valid_block_starts(5, [2, 4], 2), [])
        self.assertEqual(valid_block_starts(3, [], 4), [])

    def test_csv_safe_neutralizes_formula_prefixes(self):
        self.assertEqual(csv_safe(None), "")
        self.assertEqual(csv_safe("Math"), "Math")
        for prefix in ("=", "+", "-", "@"):
            self.assertEqual(csv_safe(f"{prefix}SUM(A1)"), f"'{prefix}SUM(A1)")


class EntityValidationTests(unittest.TestCase):
    def test_blank_names_and_tag_normalization(self):
        for factory in (
            lambda: ProjectInput(name="   "),
            lambda: RoomInput(name="  "),
            lambda: ClassInput(name="  ", room_id=uuid4()),
            lambda: TeacherInput(
                name="",
                constraints=TeacherConstraints(max_per_day=1, max_per_week=1, max_consecutive=1),
            ),
            lambda: SubjectInput(name=" \t ", constraints=SubjectConstraints()),
        ):
            with self.assertRaises(ValidationError):
                factory()

        padded = TeacherAssignmentInput(
            class_id=uuid4(),
            teacher_id=uuid4(),
            subject_id=uuid4(),
            tag="  Batch A  ",
        )
        self.assertEqual(padded.tag, "Batch A")

        blank_tag = TeacherAssignmentInput(
            class_id=uuid4(),
            teacher_id=uuid4(),
            subject_id=uuid4(),
            tag="   ",
        )
        self.assertIsNone(blank_tag.tag)

    def test_project_input_validators(self):
        with self.assertRaises(ValidationError):
            ProjectInput(days=[])
        with self.assertRaises(ValidationError):
            ProjectInput(days=[WeekDay.Mon, WeekDay.Mon])
        with self.assertRaises(ValidationError):
            ProjectInput(break_slots=[2, 2])
        with self.assertRaises(ValidationError):
            ProjectInput(break_slots=[0])
        with self.assertRaises(ValidationError):
            ProjectInput(slots=0)
        with self.assertRaises(ValidationError):
            ProjectInput(slots=25)

        p = ProjectInput(break_slots=[5, 2])
        self.assertEqual(p.break_slots, [2, 5])

    def test_subject_and_assignment_constraint_validators(self):
        with self.assertRaises(ValidationError):
            SubjectConstraints(min_per_week=6, max_per_week=5)
        with self.assertRaises(ValidationError):
            SubjectConstraints(min_consecutive=3, max_consecutive=2)
        with self.assertRaises(ValidationError):
            TeacherAssignmentConstraint(first_slot_days=[WeekDay.Mon, WeekDay.Mon])
        dup_id = uuid4()
        with self.assertRaises(ValidationError):
            TeacherAssignmentConstraint(co_schedule_with=[dup_id, dup_id])


class ProjectCrossReferenceTests(unittest.TestCase):
    def test_duplicate_ids_and_names_rejected(self):
        data = _make_base_project()
        dup_room = data.rooms[0].model_copy(update={"name": "Room 102"})
        data.rooms.append(dup_room)
        with self.assertRaises(ValidationError) as ctx:
            TimeTableGenerationInput.model_validate(data.model_dump())
        self.assertIn("duplicate room ids", str(ctx.exception))

        for label, attr, make_dup in (
            ("class", "classes", lambda c: ClassInput(name=c.name.lower(), room_id=c.room_id)),
            ("teacher", "teachers", lambda t: TeacherInput(name=t.name.upper(), constraints=t.constraints)),
            ("subject", "subjects", lambda s: SubjectInput(name=s.name.lower(), constraints=s.constraints)),
        ):
            with self.subTest(label=label):
                copy_data = _make_base_project()
                items = getattr(copy_data, attr)
                items.append(make_dup(items[0]))
                with self.assertRaises(ValidationError) as ctx:
                    TimeTableGenerationInput.model_validate(copy_data.model_dump())
                self.assertIn(f"duplicate {label} names", str(ctx.exception))

    def test_break_slot_bounds(self):
        data = _make_base_project()
        data.project.break_slots = [7]  # exceeds slots=6
        with self.assertRaises(ValidationError) as ctx:
            TimeTableGenerationInput.model_validate(data.model_dump())
        self.assertIn("exceeds project total slots", str(ctx.exception))

        data = _make_base_project()
        data.project.slots = 2
        data.project.break_slots = [1, 2]
        data.teachers[0].constraints.max_per_day = 2
        data.teachers[0].constraints.max_per_week = 2
        data.teachers[0].constraints.max_consecutive = 2
        data.subjects[0].constraints.max_per_day = 2
        data.subjects[0].constraints.max_consecutive = 2
        data.subjects[0].constraints.min_per_week = 0
        with self.assertRaises(ValidationError) as ctx:
            TimeTableGenerationInput.model_validate(data.model_dump())
        self.assertIn("cannot occupy all slots", str(ctx.exception))

    def test_teacher_limits_and_slot_references(self):
        cases = [
            ("max_per_week", lambda d: setattr(d.teachers[0].constraints, "max_per_week", 19), "exceeding total weekly slots"),
            ("max_consecutive", lambda d: setattr(d.teachers[0].constraints, "max_consecutive", 7), "exceeding project slots per day"),
            ("max_days_per_week", lambda d: setattr(d.teachers[0].constraints, "max_days_per_week", 4), "exceeding project days count"),
            ("non_project_day", lambda d: setattr(d.teachers[0].constraints, "unavailable_slots", [DaySlot(day=WeekDay.Fri, slot=1)]), "not a project day"),
            ("slot_out_of_range", lambda d: setattr(d.teachers[0].constraints, "avoid_slots", [DaySlot(day=WeekDay.Mon, slot=7)]), "outside valid project slots"),
        ]
        for label, mutate, expected in cases:
            with self.subTest(label=label):
                data = _make_base_project()
                mutate(data)
                with self.assertRaises(ValidationError) as ctx:
                    TimeTableGenerationInput.model_validate(data.model_dump())
                self.assertIn(expected, str(ctx.exception))

    def test_subject_bounds_against_project(self):
        cases = [
            ("max_per_day", lambda c: setattr(c, "max_per_day", 7), "exceeding project slots per day"),
            ("min_per_day", lambda c: (setattr(c, "max_per_day", 7), setattr(c, "min_per_day", 7)), "exceeding project slots per day"),
            ("min_per_week", lambda c: (setattr(c, "max_per_week", 20), setattr(c, "min_per_week", 19)), "exceeding total weekly slots"),
            ("max_consecutive", lambda c: setattr(c, "max_consecutive", 7), "exceeding project slots per day"),
            ("min_consecutive", lambda c: (setattr(c, "max_consecutive", 7), setattr(c, "min_consecutive", 7)), "exceeding project slots per day"),
        ]
        for label, mutate, expected in cases:
            with self.subTest(label=label):
                data = _make_base_project()
                mutate(data.subjects[0].constraints)
                with self.assertRaises(ValidationError) as ctx:
                    TimeTableGenerationInput.model_validate(data.model_dump())
                self.assertIn(expected, str(ctx.exception))

    def test_assignment_references_co_schedule_and_first_slot_days(self):
        for field in ("class_id", "teacher_id", "subject_id", "target_room_id"):
            with self.subTest(field=field):
                data = _make_base_project()
                setattr(data.teacher_assignments[0], field, uuid4())
                with self.assertRaises(ValidationError):
                    TimeTableGenerationInput.model_validate(data.model_dump())

        # period_length > project.slots
        data = _make_base_project()
        data.teacher_assignments[0].constraints.period_length = 7
        with self.assertRaises(ValidationError) as ctx:
            TimeTableGenerationInput.model_validate(data.model_dump())
        self.assertIn("exceeds project slots", str(ctx.exception))

        # co_schedule unknown id
        data = _make_base_project()
        data.teacher_assignments[0].constraints.co_schedule_with = [uuid4()]
        with self.assertRaises(ValidationError) as ctx:
            TimeTableGenerationInput.model_validate(data.model_dump())
        self.assertIn("unknown co_schedule_with", str(ctx.exception))

        # co_schedule self-reference
        data = _make_base_project()
        aid = data.teacher_assignments[0].id
        data.teacher_assignments[0].constraints.co_schedule_with = [aid]
        with self.assertRaises(ValidationError) as ctx:
            TimeTableGenerationInput.model_validate(data.model_dump())
        self.assertIn("cannot co-schedule with itself", str(ctx.exception))

        # co_schedule mismatched period_length
        data = _make_base_project()
        t2 = TeacherInput(
            name="Mr. Euler",
            constraints=TeacherConstraints(max_per_day=4, max_per_week=15, max_consecutive=2),
        )
        s2 = SubjectInput(name="Physics", constraints=SubjectConstraints(min_per_week=2, max_per_week=4))
        a2 = TeacherAssignmentInput(
            class_id=data.classes[0].id,
            teacher_id=t2.id,
            subject_id=s2.id,
            constraints=TeacherAssignmentConstraint(period_length=2),
        )
        data.teachers.append(t2)
        data.subjects.append(s2)
        data.teacher_assignments.append(a2)
        data.teacher_assignments[0].constraints.co_schedule_with = [a2.id]
        with self.assertRaises(ValidationError) as ctx:
            TimeTableGenerationInput.model_validate(data.model_dump())
        self.assertIn("identical period_length", str(ctx.exception))

        # first_slot_days on inactive day
        data = _make_base_project()
        data.teacher_assignments[0].constraints.first_slot_days = [WeekDay.Sat]
        with self.assertRaises(ValidationError) as ctx:
            TimeTableGenerationInput.model_validate(data.model_dump())
        self.assertIn("not a project day", str(ctx.exception))

    def test_class_weekly_requirements_counts_co_scheduled_tracks_once(self):
        data = _make_base_project()
        lab1 = RoomInput(name="Lab 1", is_lab=True)
        lab2 = RoomInput(name="Lab 2", is_lab=True)
        t2 = TeacherInput(
            name="Mr. Babbage",
            constraints=TeacherConstraints(max_per_day=4, max_per_week=15, max_consecutive=2),
        )
        s2 = SubjectInput(
            name="Physics",
            constraints=SubjectConstraints(min_per_week=4, max_per_week=4),
        )
        a1 = data.teacher_assignments[0]  # Math min_per_week=3
        a1.target_room_id = lab1.id
        a2 = TeacherAssignmentInput(
            class_id=data.classes[0].id,
            teacher_id=t2.id,
            subject_id=s2.id,
            target_room_id=lab2.id,
            tag="Batch 2",
        )
        a1.constraints.co_schedule_with = [a2.id]
        a2.constraints.co_schedule_with = [a1.id]
        data.rooms.extend([lab1, lab2])
        data.teachers.append(t2)
        data.subjects.append(s2)
        data.teacher_assignments.append(a2)

        reqs = class_weekly_requirements(data.teacher_assignments, data.subjects)
        self.assertEqual(reqs[data.classes[0].id], 4)

    def test_validate_project_all_input_types(self):
        data = _make_base_project()
        json_str = data.model_dump_json()

        self.assertEqual(validate_project(data), [])
        self.assertEqual(validate_project(data.model_dump()), [])
        self.assertEqual(validate_project(json_str), [])
        self.assertNotEqual(validate_project("{not-json"), [])
        self.assertIn("Unsupported input type", validate_project(123)[0])

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "project.json"
            p.write_text(json_str, encoding="utf-8")
            self.assertEqual(validate_project(p), [])
            self.assertNotEqual(validate_project(Path(tmp) / "missing.json"), [])


class GeneratedResponseTests(unittest.TestCase):
    def test_grids_and_csv_sanitization(self):
        entry = TimeTableEntryOutput(
            assignment_id=uuid4(),
            class_name="=ClassA",
            room_name="+Room1",
            subject_name="-Math",
            teacher_name="@Teacher",
            day=WeekDay.Tue,
            slot=2,
            period_length=2,
            tag="=Batch1",
        )
        resp = GeneratedResponse(entries=[entry], violations=[], success=True, break_slots=[3])

        self.assertEqual(resp.get_classes(), ["=ClassA"])
        self.assertEqual(resp.get_teachers(), ["@Teacher"])
        self.assertEqual(resp.get_rooms(), ["+Room1"])

        t_grid = resp.get_teacher_grid("@Teacher", total_slots=4)
        self.assertEqual(set(t_grid["Tue"].keys()), {1, 2, 3, 4})
        self.assertEqual(t_grid["Tue"][2], [entry])

        c_grid_auto = resp.get_class_grid("=ClassA")
        self.assertEqual(set(c_grid_auto["Tue"].keys()), {1, 2})

        rows = list(csv.reader(io.StringIO(resp.to_csv())))
        self.assertEqual(rows[0], [
            "day", "slot", "class_name", "subject_name", "teacher_name", "room_name", "tag", "period_length", "assignment_id"
        ])
        self.assertEqual(rows[1][2:7], ["'=ClassA", "'-Math", "'@Teacher", "'+Room1", "'=Batch1"])


if __name__ == "__main__":
    unittest.main()
