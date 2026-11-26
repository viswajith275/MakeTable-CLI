import json
import tempfile
import unittest
from pathlib import Path

from textual.widgets import DataTable, Input, Select

from maketable import generate
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


if __name__ == "__main__":
    unittest.main()
