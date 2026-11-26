import unittest
from uuid import uuid4

from maketable import (
    ClassInput,
    DaySlot,
    GeneratedResponse,
    ProjectInput,
    RoomConstraints,
    RoomInput,
    SubjectConstraints,
    SubjectInput,
    TeacherAssignmentConstraint,
    TeacherAssignmentInput,
    TeacherConstraints,
    TeacherInput,
    TimeTableGenerationInput,
    WeekDay,
    generate,
    parse_day_slots,
    format_day_slots,
)


def base_department_setup() -> TimeTableGenerationInput:
    auditorium = RoomInput(
        name="Auditorium",
        constraints=RoomConstraints(capacity=3),
    )
    lab_room = RoomInput(
        name="Computer Lab",
        is_lab=True,
        constraints=RoomConstraints(capacity=1),
    )
    class_a = ClassInput(name="CS-A", room_id=auditorium.id)
    class_b = ClassInput(name="CS-B", room_id=auditorium.id)

    prof_turing = TeacherInput(
        name="Prof. Turing",
        constraints=TeacherConstraints(
            max_per_day=4,
            max_per_week=12,
            max_consecutive=3,
        ),
    )
    prof_lovelace = TeacherInput(
        name="Prof. Lovelace",
        constraints=TeacherConstraints(
            max_per_day=4,
            max_per_week=12,
            max_consecutive=3,
        ),
    )

    algorithms = SubjectInput(
        name="Algorithms",
        constraints=SubjectConstraints(
            max_per_day=2,
            min_per_day=0,
            max_per_week=4,
            min_per_week=3,
            max_consecutive=2,
        ),
    )
    programming_lab = SubjectInput(
        name="Programming Lab",
        constraints=SubjectConstraints(
            max_per_day=2,
            min_per_day=0,
            max_per_week=4,
            min_per_week=2,
            max_consecutive=2,
        ),
    )

    assign_a = TeacherAssignmentInput(
        class_id=class_a.id,
        teacher_id=prof_turing.id,
        subject_id=algorithms.id,
        target_room_id=auditorium.id,
    )
    assign_lab = TeacherAssignmentInput(
        class_id=class_a.id,
        teacher_id=prof_lovelace.id,
        subject_id=programming_lab.id,
        target_room_id=lab_room.id,
    )

    return TimeTableGenerationInput(
        project=ProjectInput(
            slots=6,
            days=[WeekDay.Mon, WeekDay.Tue, WeekDay.Wed, WeekDay.Thu, WeekDay.Fri],
        ),
        rooms=[auditorium, lab_room],
        classes=[class_a, class_b],
        teachers=[prof_turing, prof_lovelace],
        subjects=[algorithms, programming_lab],
        teacher_assignments=[assign_a, assign_lab],
    )


class CollegeFeaturesTest(unittest.TestCase):
    def test_parse_and_format_day_slots(self):
        slots = parse_day_slots("Mon:1, Wed:4, Fri:2-3")
        self.assertEqual(len(slots), 4)
        self.assertEqual(slots[0].day, WeekDay.Mon)
        self.assertEqual(slots[0].slot, 1)
        self.assertEqual(slots[1].day, WeekDay.Wed)
        self.assertEqual(slots[1].slot, 4)
        self.assertEqual(slots[2].day, WeekDay.Fri)
        self.assertEqual(slots[2].slot, 2)
        self.assertEqual(slots[3].day, WeekDay.Fri)
        self.assertEqual(slots[3].slot, 3)

        formatted = format_day_slots(slots)
        self.assertEqual(formatted, "Mon:1, Wed:4, Fri:2, Fri:3")

    def test_universal_break_slots(self):
        data = base_department_setup()
        # Department lunch hour at slot 3 every day
        data.project.break_slots = [3]

        response = generate(data, time_limit_sec=4, workers=1)
        self.assertTrue(response.success)
        self.assertEqual(response.break_slots, [3])

        # Verify absolutely no entry is scheduled in slot 3
        slot_3_entries = [e for e in response.entries if e.slot == 3]
        self.assertEqual(len(slot_3_entries), 0)

    def test_teacher_unavailable_slots(self):
        data = base_department_setup()
        # Prof Turing has research meetings on Mon:1 and Wed:2
        data.teachers[0].constraints.unavailable_slots = [
            DaySlot(day=WeekDay.Mon, slot=1),
            DaySlot(day=WeekDay.Wed, slot=2),
        ]

        response = generate(data, time_limit_sec=4, workers=1)
        self.assertTrue(response.success)

        for entry in response.entries:
            if entry.teacher_name == "Prof. Turing":
                self.assertFalse(entry.day == WeekDay.Mon and entry.slot == 1)
                self.assertFalse(entry.day == WeekDay.Wed and entry.slot == 2)

    def test_teacher_max_days_per_week(self):
        data = base_department_setup()
        # Prof Turing teaches Algorithms (3 periods per week).
        # Request at most 2 working days per week (e.g. compress teaching into 2 days).
        data.teachers[0].constraints.max_days_per_week = 2

        response = generate(data, time_limit_sec=4, workers=1)
        self.assertTrue(response.success)

        turing_days = {e.day for e in response.entries if e.teacher_name == "Prof. Turing"}
        self.assertLessEqual(len(turing_days), 2)

    def test_atomic_multi_period_lab_block(self):
        data = base_department_setup()
        # Make the lab assignment atomic 2-hour block
        data.teacher_assignments[1].constraints.period_length = 2

        response = generate(data, time_limit_sec=4, workers=1)
        self.assertTrue(response.success)

        lab_entries = [e for e in response.entries if e.subject_name == "Programming Lab"]
        self.assertGreater(len(lab_entries), 0)
        self.assertEqual(len(lab_entries) % 2, 0)

        # Check that they occur in pairs of slot and slot+1 on the same day
        by_day = {}
        for e in lab_entries:
            by_day.setdefault(e.day, []).append(e.slot)

        for day, day_slots in by_day.items():
            sorted_s = sorted(day_slots)
            # Must be consecutive pairs: [s, s+1]
            for i in range(0, len(sorted_s), 2):
                self.assertEqual(sorted_s[i + 1], sorted_s[i] + 1)

    def test_co_scheduled_merged_classes(self):
        data = base_department_setup()
        # Create Assignment for CS-B with same teacher and subject in Auditorium
        assign_b = TeacherAssignmentInput(
            class_id=data.classes[1].id,  # CS-B
            teacher_id=data.teachers[0].id,  # Prof Turing
            subject_id=data.subjects[0].id,  # Algorithms
            target_room_id=data.rooms[0].id,  # Auditorium
        )
        assign_a = data.teacher_assignments[0]

        # Link them together
        assign_a.constraints.co_schedule_with = [assign_b.id]
        assign_b.constraints.co_schedule_with = [assign_a.id]
        data.teacher_assignments.append(assign_b)

        response = generate(data, time_limit_sec=4, workers=1)
        self.assertTrue(response.success)

        entries_a = {
            (e.day, e.slot): e
            for e in response.entries
            if e.class_name == "CS-A" and e.subject_name == "Algorithms"
        }
        entries_b = {
            (e.day, e.slot): e
            for e in response.entries
            if e.class_name == "CS-B" and e.subject_name == "Algorithms"
        }

        # They must be scheduled in the exact same slots
        self.assertEqual(set(entries_a.keys()), set(entries_b.keys()))
        self.assertGreater(len(entries_a), 0)

    def test_split_class_concurrent_lab_batches(self):
        data = base_department_setup()
        class_a = data.classes[0]  # CS-A
        prof_turing = data.teachers[0]
        prof_lovelace = data.teachers[1]

        # Add 2 dedicated lab rooms
        lab1 = RoomInput(name="Hardware Lab", is_lab=True)
        lab2 = RoomInput(name="Software Lab", is_lab=True)
        data.rooms.extend([lab1, lab2])

        # Add 2 lab subjects (2 periods per week, 2-hour block)
        hw_lab_subj = SubjectInput(
            name="Hardware Lab",
            constraints=SubjectConstraints(min_per_week=2, max_per_week=2, max_per_day=2),
        )
        sw_lab_subj = SubjectInput(
            name="Software Lab",
            constraints=SubjectConstraints(min_per_week=2, max_per_week=2, max_per_day=2),
        )
        data.subjects.extend([hw_lab_subj, sw_lab_subj])

        # Create two split-batch assignments for CS-A occurring at the same time
        assign_batch1 = TeacherAssignmentInput(
            class_id=class_a.id,
            teacher_id=prof_lovelace.id,
            subject_id=hw_lab_subj.id,
            target_room_id=lab1.id,
            tag="Batch 1",
            constraints=TeacherAssignmentConstraint(period_length=2),
        )
        assign_batch2 = TeacherAssignmentInput(
            class_id=class_a.id,
            teacher_id=prof_turing.id,
            subject_id=sw_lab_subj.id,
            target_room_id=lab2.id,
            tag="Batch 2",
            constraints=TeacherAssignmentConstraint(period_length=2),
        )
        assign_batch1.constraints.co_schedule_with = [assign_batch2.id]
        assign_batch2.constraints.co_schedule_with = [assign_batch1.id]

        data.teacher_assignments.extend([assign_batch1, assign_batch2])

        response = generate(data, time_limit_sec=5, workers=1)
        self.assertTrue(response.success)

        b1_entries = {
            (e.day, e.slot): e
            for e in response.entries
            if e.class_name == "CS-A" and e.tag == "Batch 1"
        }
        b2_entries = {
            (e.day, e.slot): e
            for e in response.entries
            if e.class_name == "CS-A" and e.tag == "Batch 2"
        }

        self.assertEqual(len(b1_entries), 2)
        self.assertEqual(len(b2_entries), 2)
        # Concurrent: must occupy the exact same day and slots
        self.assertEqual(set(b1_entries.keys()), set(b2_entries.keys()))

        # Verify rooms and teachers are separate
        for k in b1_entries:
            e1 = b1_entries[k]
            e2 = b2_entries[k]
            self.assertEqual(e1.room_name, "Hardware Lab")
            self.assertEqual(e1.teacher_name, prof_lovelace.name)
            self.assertEqual(e2.room_name, "Software Lab")
            self.assertEqual(e2.teacher_name, prof_turing.name)

        # Verify class grid returns both entries for the slot
        grid = response.get_class_grid("CS-A", total_slots=data.project.slots)
        sample_day, sample_slot = next(iter(b1_entries.keys()))
        slot_entries = grid[sample_day.value][sample_slot]
        self.assertEqual(len(slot_entries), 2)
        tags_in_slot = {e.tag for e in slot_entries}
        self.assertEqual(tags_in_slot, {"Batch 1", "Batch 2"})

        # Verify CSV serialization includes tag column
        csv_text = response.to_csv()
        self.assertIn("tag", csv_text.splitlines()[0])
        self.assertIn("Batch 1", csv_text)
        self.assertIn("Batch 2", csv_text)

    def test_merged_classes_respect_room_capacity(self):
        room = RoomInput(name="Hall", constraints=RoomConstraints(capacity=2))
        classes = [ClassInput(name=name, room_id=room.id) for name in "ABCD"]
        teachers = [
            TeacherInput(
                name=f"T{i}",
                constraints=TeacherConstraints(
                    max_per_day=4, max_per_week=4, max_consecutive=4
                ),
            )
            for i in (1, 2)
        ]
        subject = SubjectInput(
            name="Seminar",
            constraints=SubjectConstraints(
                max_per_day=4,
                max_per_week=4,
                min_per_week=4,
                max_consecutive=4,
            ),
        )

        assignments = []
        for pair, teacher in zip(
            ((classes[0], classes[1]), (classes[2], classes[3])), teachers
        ):
            first = TeacherAssignmentInput(
                class_id=pair[0].id, teacher_id=teacher.id, subject_id=subject.id
            )
            second = TeacherAssignmentInput(
                class_id=pair[1].id, teacher_id=teacher.id, subject_id=subject.id
            )
            first.constraints.co_schedule_with = [second.id]
            second.constraints.co_schedule_with = [first.id]
            assignments.extend([first, second])

        data = TimeTableGenerationInput(
            project=ProjectInput(slots=4, days=[WeekDay.Mon]),
            rooms=[room],
            classes=classes,
            teachers=teachers,
            subjects=[subject],
            teacher_assignments=assignments,
        )
        response = generate(data, time_limit_sec=5, workers=1)
        self.assertTrue(response.success)

        # Two merged pairs compete for the same 2-place room, so no slot may
        # host more than two of the four classes.
        usage = {}
        for entry in response.entries:
            if entry.room_name == "Hall":
                key = (entry.day, entry.slot)
                usage.setdefault(key, set()).add(entry.class_name)
        for class_names in usage.values():
            self.assertLessEqual(len(class_names), 2)

    def test_duplicate_assignment_with_different_tags(self):
        data = base_department_setup()
        class_a = data.classes[0]
        teacher = data.teachers[0]
        subject = data.subjects[0]

        a1 = TeacherAssignmentInput(
            class_id=class_a.id,
            teacher_id=teacher.id,
            subject_id=subject.id,
            tag="Batch A",
        )
        a2 = TeacherAssignmentInput(
            class_id=class_a.id,
            teacher_id=teacher.id,
            subject_id=subject.id,
            tag="Batch B",
        )
        data.teacher_assignments = [a1, a2]
        # Should not raise because tags are distinct
        self.assertEqual(len(data.teacher_assignments), 2)

        # Same tag should raise
        a3 = TeacherAssignmentInput(
            class_id=class_a.id,
            teacher_id=teacher.id,
            subject_id=subject.id,
            tag="Batch A",
        )
        data.teacher_assignments.append(a3)
        with self.assertRaises(ValueError):
            TimeTableGenerationInput.model_validate(data.model_dump())

    def test_co_scheduled_teacher_in_different_rooms_rejected(self):
        room_a = RoomInput(name="Room A", constraints=RoomConstraints(capacity=2))
        room_b = RoomInput(name="Room B", constraints=RoomConstraints(capacity=2))
        c1 = ClassInput(name="CSE-A", room_id=room_a.id)
        c2 = ClassInput(name="CSE-B", room_id=room_b.id)
        teacher = TeacherInput(
            name="Prof. X",
            constraints=TeacherConstraints(max_per_day=4, max_per_week=8, max_consecutive=2),
        )
        subj = SubjectInput(
            name="Elective",
            constraints=SubjectConstraints(max_per_day=2, max_per_week=2, min_per_week=2),
        )
        a1 = TeacherAssignmentInput(
            class_id=c1.id, teacher_id=teacher.id, subject_id=subj.id, target_room_id=room_a.id
        )
        a2 = TeacherAssignmentInput(
            class_id=c2.id, teacher_id=teacher.id, subject_id=subj.id, target_room_id=room_b.id
        )
        a1.constraints.co_schedule_with = [a2.id]
        a2.constraints.co_schedule_with = [a1.id]

        with self.assertRaises(ValueError):
            TimeTableGenerationInput(
                project=ProjectInput(slots=4, days=[WeekDay.Mon]),
                rooms=[room_a, room_b],
                classes=[c1, c2],
                teachers=[teacher],
                subjects=[subj],
                teacher_assignments=[a1, a2],
            )


if __name__ == "__main__":
    unittest.main()
