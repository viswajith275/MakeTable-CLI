import json
import tempfile
import unittest
from pathlib import Path

from textual.widgets import DataTable, Input, Select, Static

from maketable import (
    ClassInput,
    ProjectInput,
    RoomConstraints,
    RoomInput,
    SubjectConstraints,
    SubjectInput,
    TeacherAssignmentInput,
    TeacherConstraints,
    TeacherInput,
    TimeTableGenerationInput,
    WeekDay,
    generate,
)
from maketable.tui import ConfirmScreen, FormScreen, HelpScreen, TimetableApp


class TuiWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_save_open_and_export_flows(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            project_file = tmp_dir / "my_school"  # verifies .json suffix append
            csv_file = tmp_dir / "out_csv"
            json_file = tmp_dir / "out_json"

            app = TimetableApp()
            async with app.run_test() as pilot:
                app.action_load_sample()
                await pilot.pause()

                # Save project to disk
                app._save_path(project_file)
                await pilot.pause()
                saved_json = tmp_dir / "my_school.json"
                self.assertTrue(saved_json.exists())
                self.assertFalse(app.dirty)
                self.assertEqual(app.path, saved_json)

                # Generate and export CSV + JSON to disk
                response = generate(app.data, time_limit_sec=2, workers=1)
                app._on_solved(response)
                await pilot.pause()

                app._do_export_csv(csv_file)
                app._do_export_json(json_file)
                await pilot.pause()

                self.assertTrue((tmp_dir / "out_csv.csv").exists())
                self.assertTrue((tmp_dir / "out_json.json").exists())
                parsed = json.loads((tmp_dir / "out_json.json").read_text(encoding="utf-8"))
                self.assertTrue(parsed["success"])

                # Switch to room_grid and list view modes
                mode = app.query_one("#tt-view-mode", Select)
                mode.value = "room_grid"
                await pilot.pause()
                self.assertGreater(app.query_one("#table-timetable", DataTable).row_count, 0)

                mode.value = "list"
                await pilot.pause()
                self.assertEqual(
                    app.query_one("#table-timetable", DataTable).row_count,
                    len(response.entries),
                )

                # Re-open saved project resets response & dirty flag
                app.dirty = True
                app._load_path(saved_json)
                await pilot.pause()
                self.assertFalse(app.dirty)
                self.assertIsNone(app.response)

    async def test_duplicate_and_cascade_delete_teacher(self):
        app = TimetableApp()
        async with app.run_test() as pilot:
            app.action_load_sample()
            await pilot.pause()

            # Duplicate first room via keybinding `c`
            app.query_one("#tabs").active = "tab-room"
            await pilot.pause()
            room_count = len(app.data.rooms)
            await pilot.press("c")
            await pilot.pause()
            self.assertIsInstance(app.screen, FormScreen)
            # Name should default to "<first room> (copy)"
            name_val = app.screen.query_one("#field-name", Input).value
            self.assertTrue(name_val.endswith("(copy)"))
            await pilot.click("#save")
            await pilot.pause()
            self.assertEqual(len(app.data.rooms), room_count + 1)

            # Deleting a teacher with assignments opens ConfirmScreen ("Delete & Cascade")
            app.query_one("#tabs").active = "tab-teacher"
            await pilot.pause()
            teacher_count = len(app.data.teachers)
            assign_count = len(app.data.teacher_assignments)
            target_teacher = app._selected("teacher")
            related_count = sum(
                1 for a in app.data.teacher_assignments if a.teacher_id == target_teacher.id
            )
            self.assertGreater(related_count, 0)

            await pilot.press("d")
            await pilot.pause()
            self.assertIsInstance(app.screen, ConfirmScreen)
            await pilot.click("#ok")
            await pilot.pause()

            self.assertEqual(len(app.data.teachers), teacher_count - 1)
            self.assertEqual(len(app.data.teacher_assignments), assign_count - related_count)

    async def test_keyboard_shortcuts_help_add_and_dirty_new_confirm(self):
        app = TimetableApp()
        async with app.run_test() as pilot:
            # `?` opens HelpScreen
            await pilot.press("question_mark")
            await pilot.pause()
            self.assertIsInstance(app.screen, HelpScreen)
            await pilot.press("escape")
            await pilot.pause()

            # `a` on Room tab opens Add room modal
            app.query_one("#tabs").active = "tab-room"
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            self.assertIsInstance(app.screen, FormScreen)
            app.screen.query_one("#field-name", Input).value = "Seminar Hall"
            await pilot.click("#save")
            await pilot.pause()
            self.assertEqual([r.name for r in app.data.rooms], ["Seminar Hall"])
            self.assertTrue(app.dirty)

            # `Ctrl+N` when dirty pushes ConfirmScreen
            await pilot.press("ctrl+n")
            await pilot.pause()
            self.assertIsInstance(app.screen, ConfirmScreen)
            await pilot.click("#ok")
            await pilot.pause()
            self.assertEqual(app.data.rooms, [])

    async def test_co_schedule_symmetry_and_per_class_feasibility(self):
        hall = RoomInput(name="Hall", constraints=RoomConstraints(capacity=2))
        c1 = ClassInput(name="C1", room_id=hall.id)
        c2 = ClassInput(name="C2", room_id=hall.id)
        t1 = TeacherInput(
            name="T1",
            constraints=TeacherConstraints(max_per_day=4, max_per_week=8, max_consecutive=2),
        )
        s1 = SubjectInput(
            name="Math",
            constraints=SubjectConstraints(max_per_day=4, max_per_week=4, min_per_week=4),
        )
        a1 = TeacherAssignmentInput(class_id=c1.id, teacher_id=t1.id, subject_id=s1.id)
        a2 = TeacherAssignmentInput(class_id=c2.id, teacher_id=t1.id, subject_id=s1.id)
        data = TimeTableGenerationInput(
            project=ProjectInput(slots=4, days=[WeekDay.Mon, WeekDay.Tue]),
            rooms=[hall],
            classes=[c1, c2],
            teachers=[t1],
            subjects=[s1],
            teacher_assignments=[a1, a2],
        )

        app = TimetableApp()
        async with app.run_test() as pilot:
            app.data = data
            app._refresh_all()
            await pilot.pause()

            # Linking a1 -> a2 via form also updates a2 -> a1 symmetrically
            vals = app._values("assignment", a1)
            vals["co_schedule_with"] = [str(a2.id)]
            app._apply_form("assignment", vals, a1)
            await pilot.pause()
            self.assertEqual(a2.constraints.co_schedule_with, [a1.id])
            self.assertIsNone(app.data.teacher_assignments[0].constraints.first_slot_days)

            # Unlinking from a2's side clears a1's link as well
            vals2 = app._values("assignment", a2)
            self.assertEqual(vals2["co_schedule_with"], [str(a1.id)])
            vals2["co_schedule_with"] = []
            app._apply_form("assignment", vals2, a2)
            await pilot.pause()
            self.assertEqual(app.data.teacher_assignments[0].constraints.co_schedule_with, [])

            # Per-class feasibility flags Over-allocated if one class exceeds total_slots
            # even when total_student_slots across all classes is larger.
            s2 = SubjectInput(
                name="Physics",
                constraints=SubjectConstraints(max_per_day=4, max_per_week=6, min_per_week=6),
            )
            t2 = TeacherInput(
                name="T2",
                constraints=TeacherConstraints(max_per_day=4, max_per_week=8, max_consecutive=2),
            )
            app.data.subjects.append(s2)
            app.data.teachers.append(t2)
            app.data.teacher_assignments.append(
                TeacherAssignmentInput(class_id=c1.id, teacher_id=t2.id, subject_id=s2.id)
            )
            app._refresh_project()
            await pilot.pause()
            feas = str(app.query_one("#project-feasibility", Static).content)
            self.assertIn("Over-allocated", feas)


if __name__ == "__main__":
    unittest.main()
