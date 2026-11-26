import asyncio
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from textual.widgets import Button, Checkbox, DataTable, Input, Select, SelectionList, Static

from maketable.models import (
    ClassInput,
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
    ViolationOut,
    WeekDay,
)
from maketable.tui import (
    ConfirmScreen,
    FormScreen,
    HelpScreen,
    MessageScreen,
    PromptScreen,
    TimetableApp,
)


class TuiAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_app_mount_and_tabs(self):
        app = TimetableApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            # Check tabs exist
            tabs = app.query_one("#tabs")
            self.assertIsNotNone(tabs)
            self.assertEqual(tabs.active, "tab-project")

            # Check project summary rendered
            summary = app.query_one("#project-summary")
            self.assertIn("Untitled project", str(summary.content))

    async def test_load_sample_project(self):
        app = TimetableApp()
        async with app.run_test() as pilot:
            app.action_load_sample()
            await pilot.pause()

            self.assertEqual(len(app.data.rooms), 5)
            self.assertEqual(len(app.data.classes), 3)
            self.assertEqual(len(app.data.teachers), 9)
            self.assertEqual(len(app.data.subjects), 7)
            self.assertEqual(len(app.data.teacher_assignments), 21)

            # Check entity tables populated
            room_table = app.query_one("#table-room")
            self.assertEqual(room_table.row_count, 5)

            teacher_table = app.query_one("#table-teacher")
            self.assertEqual(teacher_table.row_count, 9)

    async def test_form_validation_guard_rails(self):
        app = TimetableApp()
        async with app.run_test() as pilot:
            # Add room "Room 101"
            app._apply_form("room", {"name": "Room 101", "is_lab": False, "capacity": 1}, None)
            self.assertEqual(len(app.data.rooms), 1)

            # Pre-submit validator catches duplicate name
            err = app._validate_entity_form("room", {"name": "room 101", "is_lab": False, "capacity": 1}, None)
            self.assertIsNotNone(err)
            self.assertIn("already exists", err)

            # Pre-submit validator catches invalid teacher limits
            err = app._validate_entity_form("teacher", {"name": "T1", "max_per_day": 50, "max_per_week": 10, "max_consecutive": 1}, None)
            self.assertIsNotNone(err)
            self.assertIn("cannot exceed project slots", err)

            # Pre-submit validator catches subject min > max
            err = app._validate_entity_form("subject", {
                "name": "Math", "morning_tendency": "Low",
                "max_per_day": 2, "min_per_day": 3,
                "max_per_week": 5, "min_per_week": 2,
                "max_consecutive": 2, "min_consecutive": 1
            }, None)
            self.assertIsNotNone(err)
            self.assertIn("cannot exceed max per day", err)

            # Pre-submit validator catches invalid teacher slot syntax
            err = app._validate_entity_form("teacher", {
                "name": "T2", "max_per_day": 4, "max_per_week": 10, "max_consecutive": 1,
                "unavailable_slots": "InvalidDay:1"
            }, None)
            self.assertIsNotNone(err)
            self.assertIn("Invalid unavailable slots", err)

            # Valid teacher with unavailable slots applies cleanly
            app._apply_form("teacher", {
                "name": "Prof. X", "max_per_day": 4, "max_per_week": 12, "max_consecutive": 2,
                "unavailable_slots": "Mon:1, Wed:4", "avoid_slots": "Fri:5", "max_days_per_week": "4"
            }, None)
            self.assertEqual(len(app.data.teachers), 1)
            self.assertEqual(len(app.data.teachers[0].constraints.unavailable_slots), 2)
            self.assertEqual(app.data.teachers[0].constraints.max_days_per_week, 4)

    async def test_cascade_delete_guards(self):
        app = TimetableApp()
        async with app.run_test() as pilot:
            app.action_load_sample()
            await pilot.pause()

            room_to_delete = app.data.rooms[0]  # Room A used as home room by Class 10A
            app._rows["room"] = app.data.rooms
            app.query_one("#table-room").move_cursor(row=0)

            # Attempting to delete Room A should trigger "Room is in use as Home Room"
            app._delete("room")
            await pilot.pause()

            # MessageScreen should be pushed
            self.assertTrue(any(isinstance(s, type(app.screen)) and "Room is in use" in getattr(s, "title_text", "") for s in app.screen_stack))

    async def test_filter_entity_table(self):
        app = TimetableApp()
        async with app.run_test() as pilot:
            app.action_load_sample()
            await pilot.pause()

            # Filter rooms by "Lab"
            app._table_search["room"] = "lab"
            app._refresh_entity("room")
            await pilot.pause()

            room_table = app.query_one("#table-room")
            # 2 labs: Science Lab, Computer Lab
            self.assertEqual(room_table.row_count, 2)

    async def test_edit_non_first_entity(self):
        app = TimetableApp()
        async with app.run_test() as pilot:
            app.action_load_sample()
            await pilot.pause()

            teacher = app.data.teachers[-1]
            values = app._values("teacher", teacher)
            self.assertIsNone(app._validate_entity_form("teacher", values, teacher.id))

            values["name"] = "Renamed Teacher"
            app._apply_form("teacher", values, teacher)
            await pilot.pause()
            self.assertEqual(app.data.teachers[-1].name, "Renamed Teacher")

    async def test_delete_co_scheduled_assignment(self):
        app = TimetableApp()
        async with app.run_test() as pilot:
            app.action_load_sample()
            await pilot.pause()

            removed, survivor = app.data.teacher_assignments[:2]
            removed.constraints.co_schedule_with = [survivor.id]
            survivor.constraints.co_schedule_with = [removed.id]
            count = len(app.data.teacher_assignments)

            app._remove("assignment", removed)
            await pilot.pause()

            self.assertEqual(len(app.data.teacher_assignments), count - 1)
            self.assertEqual(survivor.constraints.co_schedule_with, [])

    async def test_solve_options_are_visible_and_wired(self):
        from maketable.models import GeneratedResponse
        from maketable.solver import TimeTableGenerator

        app = TimetableApp()
        async with app.run_test(size=(70, 22)) as pilot:
            app.action_load_sample()
            await pilot.pause()
            app.query_one("#tabs").active = "tab-timetable"
            await pilot.pause()

            # Narrow inputs and compact selects must expose readable text within
            # a 70-column window instead of clipping labels or border lines.
            for widget_id in ("#opt-time", "#opt-seed", "#opt-workers"):
                self.assertGreaterEqual(
                    app.query_one(widget_id, Input).content_region.width, 3
                )
            for sel_id in ("#tt-view-mode", "#tt-selector"):
                sel = app.query_one(sel_id, Select)
                sc = sel.query_one("SelectCurrent")
                self.assertGreaterEqual(sc.content_region.height, 1, sel_id)
                self.assertLessEqual(sc.region.height, sel.region.height, sel_id)

            # Every solve and export control must stay inside a 70-column screen.
            for widget_id in (
                "#opt-gap",
                "#opt-patience",
                "#opt-fast",
                "#generate",
                "#export-csv",
                "#export-json",
            ):
                widget = app.query_one(widget_id)
                self.assertTrue(widget.visible, widget_id)
                self.assertLessEqual(
                    widget.region.x + widget.region.width, 70, widget_id
                )

            # Edit Time, Seed, Workers, Gap, Patience interactively via focus & keys
            for iid, new_val in (
                ("#opt-time", "3"),
                ("#opt-seed", "99"),
                ("#opt-workers", "2"),
                ("#opt-gap", "0.05"),
                ("#opt-patience", "1"),
            ):
                inp = app.query_one(iid, Input)
                inp.focus()
                await pilot.pause()
                await pilot.press("end", "backspace", "backspace", "backspace", "backspace")
                for ch in new_val:
                    await pilot.press(ch)
                await pilot.pause()
            app.query_one("#opt-fast", Checkbox).value = True

            captured: dict = {}

            def fake_solve(self, **kwargs):
                captured.update(kwargs)
                return GeneratedResponse(success=False, entries=[], violations=[])

            with patch.object(TimeTableGenerator, "solve", fake_solve):
                # Pressing Enter inside any #opt-* field submits Generate
                app.query_one("#opt-seed", Input).focus()
                await pilot.pause()
                await pilot.press("enter")
                await app.workers.wait_for_complete()
                await pilot.pause()

            self.assertTrue(captured.get("fast"))
            self.assertEqual(captured.get("gap_limit"), 0.05)
            self.assertEqual(captured.get("patience"), 1.0)
            self.assertEqual(captured.get("time_limit_sec"), 3.0)
            self.assertEqual(captured.get("seed"), 99)
            self.assertEqual(captured.get("workers"), 2)

    async def test_assignment_co_schedule_excludes_self_and_split_workload(self):
        r1 = RoomInput(name="R1", constraints=RoomConstraints(capacity=2))
        lab1 = RoomInput(name="Lab 1", is_lab=True)
        lab2 = RoomInput(name="Lab 2", is_lab=True)
        c1 = ClassInput(name="CS-A", room_id=r1.id)
        t1 = TeacherInput(
            name="T1",
            constraints=TeacherConstraints(max_per_day=4, max_per_week=8, max_consecutive=2),
        )
        t2 = TeacherInput(
            name="T2",
            constraints=TeacherConstraints(max_per_day=4, max_per_week=8, max_consecutive=2),
        )
        s1 = SubjectInput(
            name="Physics Lab",
            constraints=SubjectConstraints(min_per_week=2, max_per_week=2),
        )
        s2 = SubjectInput(
            name="Chem Lab",
            constraints=SubjectConstraints(min_per_week=2, max_per_week=2),
        )
        a1 = TeacherAssignmentInput(
            class_id=c1.id,
            teacher_id=t1.id,
            subject_id=s1.id,
            target_room_id=lab1.id,
            tag="Batch 1",
            constraints=TeacherAssignmentConstraint(period_length=2),
        )
        a2 = TeacherAssignmentInput(
            class_id=c1.id,
            teacher_id=t2.id,
            subject_id=s2.id,
            target_room_id=lab2.id,
            tag="Batch 2",
            constraints=TeacherAssignmentConstraint(period_length=2),
        )
        a1.constraints.co_schedule_with = [a2.id]
        a2.constraints.co_schedule_with = [a1.id]
        data = TimeTableGenerationInput(
            project=ProjectInput(name="Dept", slots=5, days=[WeekDay.Mon, WeekDay.Tue], break_slots=[3]),
            rooms=[r1, lab1, lab2],
            classes=[c1],
            teachers=[t1, t2],
            subjects=[s1, s2],
            teacher_assignments=[a1, a2],
        )

        app = TimetableApp()
        async with app.run_test(size=(80, 24)) as pilot:
            app.data = data
            app._refresh_all()
            await pilot.pause()

            # Split co-scheduled labs require 2 class periods, not 4
            feasibility = str(app.query_one("#project-feasibility", Static).content)
            self.assertIn("Minimum Required Class Periods: [bold]2[/bold] periods", feasibility)

            # Editing an assignment must exclude itself from co_schedule_with choices
            app.query_one("#tabs").active = "tab-assignment"
            await pilot.pause()
            app._edit("assignment")
            await pilot.pause()
            edited = app._selected("assignment")
            sel_list = app.screen.query_one("#field-co_schedule_with", SelectionList)
            opt_vals = {opt.value for opt in sel_list._options}
            self.assertNotIn(str(edited.id), opt_vals)
            await pilot.press("escape")
            await pilot.pause()

    async def test_violations_visibility_cursor_preserve_and_new_clears_selector(self):
        app = TimetableApp()
        async with app.run_test(size=(70, 22)) as pilot:
            app.action_load_sample()
            await pilot.pause()

            # Before solve: violations block is hidden
            self.assertFalse(app.query_one("#violations-title").display)
            self.assertFalse(app.query_one("#table-violations").display)

            # Cursor preservation when editing non-first teacher row
            app.query_one("#tabs").active = "tab-teacher"
            await pilot.pause()
            teacher_table = app.query_one("#table-teacher", DataTable)
            teacher_table.move_cursor(row=4)
            target_teacher = app._selected("teacher")
            values = app._values("teacher", target_teacher)
            values["max_days_per_week"] = ""
            app._apply_form("teacher", values, target_teacher)
            await pilot.pause()
            self.assertEqual(app._selected("teacher").id, target_teacher.id)

            # Form with None max_days_per_week renders "" cleanly, never "None"
            app._edit("teacher")
            await pilot.pause()
            self.assertEqual(
                app.screen.query_one("#field-max_days_per_week", Input).value, ""
            )
            await pilot.press("escape")
            await pilot.pause()

            # Solve with violations shows violations block; 0 violations hides it; New clears selector
            bad = GeneratedResponse(
                success=True,
                entries=[],
                violations=[
                    ViolationOut(
                        error_msg="Class 10A: Min weekly classes didn't meet for Physical Education (required: 6)",
                        val=2,
                    )
                ],
            )
            app._on_solved(bad)
            await pilot.pause()
            self.assertTrue(app.query_one("#violations-title").display)
            self.assertTrue(app.query_one("#table-violations").display)

            clean = GeneratedResponse(
                success=True,
                entries=[],
                violations=[],
            )
            # Load a generated response with classes, verify New clears selector
            from maketable import generate

            tiny = generate(app.data, time_limit_sec=2, workers=1)
            app._on_solved(tiny)
            await pilot.pause()
            self.assertGreater(len(app.query_one("#tt-selector", Select)._options), 1)
            app.dirty = False
            app._new()
            await pilot.pause()
            self.assertEqual(len(app.query_one("#tt-selector", Select)._options), 1)
            self.assertFalse(app.query_one("#violations-title").display)

            # Explicitly test 0-violation result hides violations
            clean.entries = tiny.entries
            app._on_solved(clean)
            await pilot.pause()
            self.assertFalse(app.query_one("#violations-title").display)
            self.assertFalse(app.query_one("#table-violations").display)

    async def test_help_modal(self):
        app = TimetableApp()
        async with app.run_test() as pilot:
            app.action_help()
            await pilot.pause()
            self.assertTrue(any(isinstance(s, HelpScreen) for s in app.screen_stack))
            app.screen.dismiss()
            await pilot.pause()

    async def test_solve_and_grid_views(self):
        app = TimetableApp()
        async with app.run_test() as pilot:
            app.action_load_sample()
            await pilot.pause()

            # Run solver directly on app data
            from maketable import generate
            response = generate(app.data, time_limit_sec=2, workers=1)
            app._on_solved(response)
            await pilot.pause()

            self.assertIsNotNone(app.response)
            self.assertTrue(app.response.success)

            # Check class grid
            app.query_one("#tt-view-mode").value = "class_grid"
            await pilot.pause()
            tt_table = app.query_one("#table-timetable")
            self.assertEqual(tt_table.row_count, 5)  # 5 days (Mon..Fri)

            # Check teacher grid
            app.query_one("#tt-view-mode").value = "teacher_grid"
            await pilot.pause()
            self.assertEqual(tt_table.row_count, 5)

            # Check export CSV string
            csv_data = app.response.to_csv()
            self.assertIn("Class 10A", csv_data)
            self.assertIn("day,slot", csv_data)

    async def test_small_window_responsiveness(self):
        # Test terminal with standard small dimensions (80x24) and very small (70x22)
        for w, h in [(80, 24), (70, 22)]:
            app = TimetableApp()
            async with app.run_test(size=(w, h)) as pilot:
                await pilot.pause()
                tabs = app.query_one("#tabs")
                self.assertIsNotNone(tabs)

                # Project tab buttons in 2 rows all exist with uniform 16x1 dimensions
                for btn_id in ("#edit-project", "#validate", "#load-sample", "#help", "#open", "#save", "#save-as", "#new"):
                    btn = app.query_one(btn_id, Button)
                    self.assertEqual(btn.region.height, 1)
                    self.assertEqual(btn.region.width, 16)
                    self.assertGreaterEqual(btn.content_region.height, 1)
                    self.assertGreaterEqual(btn.content_region.width, len(str(btn.label)))

                # Class search placeholder is grammatically correct & entity buttons match size
                tabs.active = "tab-class"
                await pilot.pause()
                self.assertEqual(app.query_one("#filter-class", Input).placeholder, "Search classes…")
                for btn_id in ("#add-class", "#edit-class", "#dup-class", "#del-class"):
                    btn = app.query_one(btn_id, Button)
                    self.assertEqual(btn.region.height, 1)
                    self.assertEqual(btn.region.width, 16)

                # Switch to timetable tab
                tabs.active = "tab-timetable"
                await pilot.pause()

                # Timetable controls & action buttons render visibly with uniform 19x1 size
                for ctl_id in ("#opt-time", "#opt-seed", "#opt-workers", "#opt-hint", "#generate", "#tt-view-mode", "#export-csv", "#export-json"):
                    self.assertIsNotNone(app.query_one(ctl_id))
                pos_grid = []
                for btn_id in ("#generate", "#export-csv", "#export-json"):
                    btn = app.query_one(btn_id, Button)
                    self.assertEqual(btn.region.height, 1)
                    self.assertEqual(btn.region.width, 19)
                    self.assertGreaterEqual(btn.content_region.height, 1)
                    self.assertGreaterEqual(btn.content_region.width, len(str(btn.label)))
                    pos_grid.append(btn.region)

                # Switching to list view keeps action buttons anchored in place
                app.query_one("#tt-view-mode", Select).value = "list"
                await pilot.pause()
                self.assertEqual(
                    [app.query_one(b, Button).region for b in ("#generate", "#export-csv", "#export-json")],
                    pos_grid,
                )
                app.query_one("#tt-view-mode", Select).value = "class_grid"
                await pilot.pause()

                # Room modal boolean checkbox stays compact (1 row tall)
                app.action_load_sample()
                await pilot.pause()
                tabs.active = "tab-room"
                await pilot.pause()
                await pilot.press("e")
                await pilot.pause()
                self.assertEqual(app.screen.query_one("#field-is_lab", Checkbox).region.height, 1)
                await pilot.press("escape")
                await pilot.pause()

                # Modal buttons fit on screen cleanly
                for modal in (
                    ConfirmScreen("Delete this item?", "Delete"),
                    MessageScreen("Validation", "No issues found."),
                    PromptScreen("Export", "File path:", "out.csv"),
                    HelpScreen(),
                ):
                    app.push_screen(modal)
                    await pilot.pause()
                    for btn in app.screen.query("#dialog-buttons Button"):
                        self.assertGreaterEqual(btn.content_region.height, 1)
                        self.assertLessEqual(btn.region.y + btn.region.height, h)
                    await pilot.press("escape")
                    await pilot.pause()


if __name__ == "__main__":
    unittest.main()
