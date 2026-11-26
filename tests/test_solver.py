import unittest

from maketable import (
    ClassInput,
    DaySlot,
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
    TimeTableGenerationInput,
    TimeTableGenerator,
    WeekDay,
    generate,
)


def _solver_fixture() -> TimeTableGenerationInput:
    room = RoomInput(name="Main Room")
    class_ = ClassInput(name="C1", room_id=room.id)
    teacher = TeacherInput(
        name="T1",
        constraints=TeacherConstraints(max_per_day=4, max_per_week=10, max_consecutive=3),
    )
    subject = SubjectInput(
        name="Math",
        constraints=SubjectConstraints(
            max_per_day=2,
            min_per_day=0,
            max_per_week=4,
            min_per_week=4,
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
        project=ProjectInput(slots=4, days=[WeekDay.Mon, WeekDay.Tue, WeekDay.Wed]),
        rooms=[room],
        classes=[class_],
        teachers=[teacher],
        subjects=[subject],
        teacher_assignments=[assignment],
    )


class SolverConstraintTests(unittest.TestCase):
    def test_empty_assignments_returns_empty_success(self):
        data = _solver_fixture()
        data.project.break_slots = [2]
        data.teachers[0].constraints.max_per_week = 9
        data.teacher_assignments = []
        resp = generate(data, time_limit_sec=2, workers=1)
        self.assertTrue(resp.success)
        self.assertEqual(resp.entries, [])
        self.assertEqual(resp.violations, [])
        self.assertEqual(resp.break_slots, [2])

    def test_reproducibility_with_single_worker_and_seed(self):
        data = _solver_fixture()
        r1 = generate(data, time_limit_sec=3, seed=123, workers=1)
        r2 = generate(data, time_limit_sec=3, seed=123, workers=1)
        self.assertEqual(
            [(e.day, e.slot, e.subject_name) for e in r1.entries],
            [(e.day, e.slot, e.subject_name) for e in r2.entries],
        )

    def test_target_room_override_and_room_capacity_hard_constraint(self):
        home_room = RoomInput(name="Home", constraints=RoomConstraints(capacity=1))
        shared_lab = RoomInput(name="Lab", is_lab=True, constraints=RoomConstraints(capacity=1))
        c1 = ClassInput(name="C1", room_id=home_room.id)
        c2 = ClassInput(name="C2", room_id=home_room.id)
        t1 = TeacherInput(
            name="T1",
            constraints=TeacherConstraints(max_per_day=2, max_per_week=4, max_consecutive=2),
        )
        t2 = TeacherInput(
            name="T2",
            constraints=TeacherConstraints(max_per_day=2, max_per_week=4, max_consecutive=2),
        )
        s = SubjectInput(
            name="LabWork",
            constraints=SubjectConstraints(max_per_day=2, max_per_week=2, min_per_week=2, max_consecutive=2),
        )
        a1 = TeacherAssignmentInput(class_id=c1.id, teacher_id=t1.id, subject_id=s.id, target_room_id=shared_lab.id)
        a2 = TeacherAssignmentInput(class_id=c2.id, teacher_id=t2.id, subject_id=s.id, target_room_id=shared_lab.id)

        data = TimeTableGenerationInput(
            project=ProjectInput(slots=4, days=[WeekDay.Mon]),
            rooms=[home_room, shared_lab],
            classes=[c1, c2],
            teachers=[t1, t2],
            subjects=[s],
            teacher_assignments=[a1, a2],
        )
        resp = generate(data, time_limit_sec=4, workers=1)
        self.assertTrue(resp.success)
        self.assertEqual(len(resp.entries), 4)
        self.assertTrue(all(e.room_name == "Lab" for e in resp.entries))
        # Capacity 1 means C1 and C2 cannot occupy Lab in the same slot
        slots_by_class = {
            "C1": {(e.day, e.slot) for e in resp.entries if e.class_name == "C1"},
            "C2": {(e.day, e.slot) for e in resp.entries if e.class_name == "C2"},
        }
        self.assertTrue(slots_by_class["C1"].isdisjoint(slots_by_class["C2"]))

    def test_teacher_avoid_slots_prefers_free_slots_and_reports_when_forced(self):
        # Case 1: alternative slots exist -> avoid slot is skipped with zero violations
        data = _solver_fixture()
        data.subjects[0].constraints.min_per_week = 2
        data.subjects[0].constraints.max_per_week = 2
        data.subjects[0].constraints.max_per_day = 2
        data.project.days = [WeekDay.Mon]
        data.teachers[0].constraints.max_per_week = 4
        data.teachers[0].constraints.avoid_slots = [DaySlot(day=WeekDay.Mon, slot=1)]
        clean = generate(data, time_limit_sec=3, workers=1)
        self.assertTrue(clean.success)
        self.assertFalse(any(e.slot == 1 for e in clean.entries))
        self.assertEqual(clean.violations, [])

        # Case 2: only slot 1 is available -> forced to use avoided slot and report violation
        forced_data = _solver_fixture()
        forced_data.project.slots = 1
        forced_data.project.days = [WeekDay.Mon]
        forced_data.teachers[0].constraints.max_per_day = 1
        forced_data.teachers[0].constraints.max_per_week = 1
        forced_data.teachers[0].constraints.max_consecutive = 1
        forced_data.teachers[0].constraints.avoid_slots = [DaySlot(day=WeekDay.Mon, slot=1)]
        forced_data.subjects[0].constraints.max_per_day = 1
        forced_data.subjects[0].constraints.max_per_week = 1
        forced_data.subjects[0].constraints.min_per_week = 1
        forced_data.subjects[0].constraints.max_consecutive = 1
        forced_resp = generate(forced_data, time_limit_sec=3, workers=1)
        self.assertTrue(forced_resp.success)
        self.assertTrue(any("avoided slot" in v.error_msg for v in forced_resp.violations))

    def test_first_slot_days_satisfied_and_violation_when_blocked(self):
        data = _solver_fixture()
        data.teacher_assignments[0].constraints.first_slot_days = [WeekDay.Mon, WeekDay.Wed]
        resp = generate(data, time_limit_sec=4, workers=1)
        self.assertTrue(resp.success)
        mon_slots = [e.slot for e in resp.entries if e.day == WeekDay.Mon]
        wed_slots = [e.slot for e in resp.entries if e.day == WeekDay.Wed]
        self.assertIn(1, mon_slots)
        self.assertIn(1, wed_slots)

        # Block teacher on Mon:1 -> should report first-slot violation
        data.teachers[0].constraints.unavailable_slots = [DaySlot(day=WeekDay.Mon, slot=1)]
        blocked = generate(data, time_limit_sec=4, workers=1)
        self.assertTrue(blocked.success)
        self.assertTrue(any("First slot required on Mon" in v.error_msg for v in blocked.violations))

    def test_subject_min_and_max_consecutive_and_morning_tendency(self):
        # min_consecutive = 2 forces 2 consecutive periods when scheduled
        room = RoomInput(name="R1")
        c1 = ClassInput(name="C1", room_id=room.id)
        t1 = TeacherInput(
            name="T1",
            constraints=TeacherConstraints(max_per_day=4, max_per_week=4, max_consecutive=4),
        )
        subj = SubjectInput(
            name="Physics",
            constraints=SubjectConstraints(
                morning_tendency=Level.High,
                max_per_day=2,
                min_per_day=0,
                max_per_week=2,
                min_per_week=2,
                max_consecutive=2,
                min_consecutive=2,
            ),
        )
        a1 = TeacherAssignmentInput(class_id=c1.id, teacher_id=t1.id, subject_id=subj.id)
        data = TimeTableGenerationInput(
            project=ProjectInput(slots=4, days=[WeekDay.Mon]),
            rooms=[room],
            classes=[c1],
            teachers=[t1],
            subjects=[subj],
            teacher_assignments=[a1],
        )
        resp = generate(data, time_limit_sec=3, workers=1)
        self.assertTrue(resp.success)
        self.assertEqual(resp.violations, [])
        # High morning tendency + min_consecutive=2 on slots 1..4 should place slots 1 and 2
        self.assertEqual([e.slot for e in resp.entries], [1, 2])

    def test_teacher_and_subject_soft_violation_messages(self):
        # Force teacher max_per_day=1 violation while subject min_consecutive=2 keeps scheduling profitable
        room = RoomInput(name="R1")
        c1 = ClassInput(name="C1", room_id=room.id)
        t1 = TeacherInput(
            name="Prof. Limit",
            constraints=TeacherConstraints(
                max_per_day=1,
                max_per_week=2,
                max_consecutive=2,
            ),
        )
        subj = SubjectInput(
            name="Chem",
            constraints=SubjectConstraints(
                max_per_day=2,
                min_per_day=0,
                max_per_week=2,
                min_per_week=2,
                max_consecutive=2,
                min_consecutive=2,
            ),
        )
        a1 = TeacherAssignmentInput(class_id=c1.id, teacher_id=t1.id, subject_id=subj.id)
        data = TimeTableGenerationInput(
            project=ProjectInput(slots=2, days=[WeekDay.Mon]),
            rooms=[room],
            classes=[c1],
            teachers=[t1],
            subjects=[subj],
            teacher_assignments=[a1],
        )
        resp = generate(data, time_limit_sec=4, workers=1)
        self.assertTrue(resp.success)
        self.assertEqual(len(resp.entries), 2)
        messages = [v.error_msg for v in resp.violations]
        self.assertTrue(any("Max daily classes exceeded for Prof. Limit" in m for m in messages))

    def test_warm_start_with_valid_hint_produces_valid_result(self):
        data = _solver_fixture()
        first = generate(data, time_limit_sec=3, workers=1, seed=42)
        self.assertTrue(first.success)

        re_solved = TimeTableGenerator(RawData=data, HintData=first).solve(
            time_limit_sec=3, workers=1, seed=42
        )
        self.assertTrue(re_solved.success)
        self.assertEqual(len(re_solved.entries), len(first.entries))


if __name__ == "__main__":
    unittest.main()
