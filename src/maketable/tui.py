"""Interactive editor and management interface for timetable projects.

Run it with ``timetable tui project.json`` (or just ``timetable``). It is a
thin UI over the same Pydantic models the CLI and solver use, so anything
saved here can be fed straight to ``TimeTableGenerator`` or another application.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path
import textwrap
import threading
from typing import Any, Callable
from uuid import UUID, uuid4

from pydantic import ValidationError
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Markdown,
    Select,
    SelectionList,
    Static,
    TabbedContent,
    TabPane,
)

from .models import (
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
    class_weekly_requirements,
    format_day_slots,
    parse_day_slots,
)

PLURALS = {
    "room": "rooms",
    "class": "classes",
    "teacher": "teachers",
    "subject": "subjects",
    "assignment": "teacher_assignments",
}

ENTITY_TABS = [
    ("room", "Rooms", ["Name", "Lab", "Capacity"]),
    ("class", "Classes", ["Name", "Home room"]),
    ("teacher", "Teachers", ["Name", "Max/day", "Max/week", "Max cons", "Max days", "Unavailable", "Avoid"]),
    (
        "subject",
        "Subjects",
        [
            "Name",
            "Morning",
            "Max/day",
            "Min/day",
            "Max/week",
            "Min/week",
            "Max cons",
            "Min cons",
            "Fixed slots",
        ],
    ),
    (
        "assignment",
        "Assignments",
        ["Class", "Teacher", "Subject", "Tag", "Room", "Length", "Co-schedule", "First-slot days"],
    ),
]


# ---------------------------------------------------------------------------
# Modals & Forms
# ---------------------------------------------------------------------------


@dataclass
class Field:
    key: str
    label: str
    kind: str = "text"  # text | int | number | bool | select | multi
    default: Any = ""
    options: list[tuple[str, Any]] = field(default_factory=list)
    required: bool = True
    help: str = ""
    prompt: str = ""


def format_error(error: BaseException) -> str:
    if isinstance(error, ValidationError):
        lines = []
        for err in error.errors():
            loc = " -> ".join(str(part) for part in err["loc"])
            lines.append(f"• {loc}: {err['msg']}" if loc else f"• {err['msg']}")
        return "\n".join(lines)
    return str(error)


class FormScreen(ModalScreen[dict[str, Any] | None]):
    """A modal form with pre-submit validation to preserve user input on errors."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self,
        title: str,
        fields: list[Field],
        values: dict[str, Any] | None = None,
        validator: Callable[[dict[str, Any]], str | None] | None = None,
    ):
        super().__init__()
        self.title_text = title
        self.fields = fields
        self.values = values or {}
        self.validator = validator

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self.title_text, id="dialog-title")
            with VerticalScroll(id="dialog-body"):
                for idx, item in enumerate(self.fields):
                    label_classes = "field-label first-field" if idx == 0 else "field-label"
                    yield Label(item.label, classes=label_classes)
                    if item.help:
                        yield Static(item.help, classes="field-help")
                    yield self._make_widget(item)
            with Horizontal(id="dialog-buttons"):
                yield Button("Cancel", id="cancel", compact=True)
                yield Button("Save", id="save", variant="primary", compact=True)

    def _make_widget(self, item: Field):
        widget_id = f"field-{item.key}"
        value = self.values.get(item.key, item.default)
        if item.kind == "text":
            return Input(
                value="" if value is None else str(value),
                placeholder=item.prompt,
                id=widget_id,
            )
        if item.kind == "int":
            return Input(
                value="" if value is None else str(value),
                type="integer",
                id=widget_id,
            )
        if item.kind == "number":
            return Input(
                value="" if value is None else str(value),
                type="number",
                id=widget_id,
            )
        if item.kind == "bool":
            return Checkbox("Yes", value=bool(value), id=widget_id, compact=True)
        if item.kind == "select":
            valid_values = {opt_val for _, opt_val in item.options}
            blank = value is None or value == "" or value not in valid_values
            initial = Select.NULL if blank else value
            return Select(
                item.options,
                allow_blank=not item.required,
                prompt=item.prompt or "Select…",
                value=initial,
                id=widget_id,
            )
        if item.kind == "multi":
            selected = set(value or [])
            return SelectionList(
                *[
                    (label, option_value, option_value in selected)
                    for label, option_value in item.options
                ],
                id=widget_id,
            )
        raise ValueError(f"unknown field kind: {item.kind}")

    def on_mount(self) -> None:
        for widget in self.query("Input, Select, Checkbox, SelectionList"):
            widget.focus()
            break

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "cancel":
            self.dismiss(None)
        elif event.button.id == "save":
            self._submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._submit()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _submit(self) -> None:
        values: dict[str, Any] = {}
        for item in self.fields:
            widget = self.query_one(f"#field-{item.key}")
            if item.kind == "text":
                value = widget.value.strip()
                if item.required and not value:
                    self.notify(f"{item.label} is required.", severity="error")
                    return
                values[item.key] = value
            elif item.kind in ("int", "number"):
                raw = widget.value.strip()
                if not raw:
                    if item.required:
                        self.notify(f"{item.label} is required.", severity="error")
                        return
                    values[item.key] = None
                    continue
                try:
                    parsed = int(raw) if item.kind == "int" else float(raw)
                except ValueError:
                    self.notify(f"{item.label} must be a number.", severity="error")
                    return
                if item.kind == "number" and not math.isfinite(parsed):
                    self.notify(f"{item.label} must be a finite number.", severity="error")
                    return
                values[item.key] = parsed
            elif item.kind == "bool":
                values[item.key] = widget.value
            elif item.kind == "select":
                value = widget.value
                if value is Select.NULL:
                    if item.required:
                        self.notify(f"{item.label} is required.", severity="error")
                        return
                    values[item.key] = None
                else:
                    values[item.key] = value
            elif item.kind == "multi":
                values[item.key] = list(widget.selected)

        if self.validator is not None:
            err = self.validator(values)
            if err:
                self.notify(err, title="Validation Error", severity="error", timeout=6)
                return

        self.dismiss(values)


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, message: str, confirm_label: str = "Confirm", variant: str = "error"):
        super().__init__()
        self.message = message
        self.confirm_label = confirm_label
        self.variant = variant

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm"):
            yield Static(self.message, id="confirm-text")
            with Horizontal(id="dialog-buttons"):
                yield Button("Cancel", id="cancel", compact=True)
                yield Button(self.confirm_label, id="ok", variant=self.variant, compact=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "ok")

    def action_cancel(self) -> None:
        self.dismiss(False)


class MessageScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, title: str, message: str):
        super().__init__()
        self.title_text = title
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="message"):
            yield Static(self.title_text, id="dialog-title")
            with VerticalScroll(id="message-body"):
                yield Static(self.message)
            with Horizontal(id="dialog-buttons"):
                yield Button("Close", id="close", variant="primary", compact=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class PromptScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, label: str, default: str = ""):
        super().__init__()
        self.title_text = title
        self.label_text = label
        self.default_val = default

    def compose(self) -> ComposeResult:
        with Vertical(id="prompt-dialog"):
            yield Static(self.title_text, id="dialog-title")
            yield Label(self.label_text, classes="field-label")
            yield Input(value=self.default_val, id="prompt-input")
            with Horizontal(id="dialog-buttons"):
                yield Button("Cancel", id="cancel", compact=True)
                yield Button("OK", id="ok", variant="primary", compact=True)

    def on_mount(self) -> None:
        self.query_one("#prompt-input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "ok":
            self.dismiss(self.query_one("#prompt-input", Input).value.strip())
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.dismiss(event.value.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)


HELP_MARKDOWN = """
# MakeTable Quick Reference

MakeTable generates optimized school timetables using OR-Tools CP-SAT.

### Keyboard Shortcuts
- **Ctrl+S**: Quick save project
- **Ctrl+Shift+S**: Save project as…
- **Ctrl+O**: Open project file
- **Ctrl+N**: Start new project
- **Ctrl+G**: Generate timetable
- **F5**: Validate project consistency and guard rails
- **? / F1**: Open this Help guide
- **Ctrl+Q**: Quit application
- **a**: Add item in the currently active tab
- **e** / **Enter**: Edit selected row
- **c**: Duplicate selected row
- **d** / **Delete**: Delete selected row

### Workflow Guide
1. **Rooms**: Add classrooms and labs. Specify simultaneous class capacity.
2. **Classes**: Add classes and assign their default home room.
3. **Teachers**: Add teaching staff with daily, weekly, and consecutive limits, unavailable/avoided slots, and max working days.
4. **Subjects**: Define subjects with morning tendencies, weekly/daily requirements, and optional fixed day/slot pins (e.g. `Tue:6-8, Thu:6-8`).
5. **Assignments**: Map class + teacher + subject, optional batch/track tag, target room, period block length, co-scheduling (merged lectures or split batches), and first-slot days.
6. **Timetable**: Set **Time**, **Seed**, **Workers**, **Gap**, and **Patience**, then press **Generate** (or `Ctrl+G` / `Enter` in an option field). **Fast** caps the solve at 5 seconds for a quick preview; **Hint** reuses the previous result as a warm start. **Gap** is a relative fraction (`0.02` = 2%, blank = off); **Patience** is seconds without improvement before stopping (`0` disables).
7. **Inspect & Export**: Toggle between **Class Schedule**, **Teacher Schedule**, **Room Schedule**, and **All Entries**, then click **Export CSV** or **Export JSON**.
"""


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "close", "Close")]

    def compose(self) -> ComposeResult:
        with Vertical(id="help-dialog"):
            with VerticalScroll(id="help-body"):
                yield Markdown(HELP_MARKDOWN)
            with Horizontal(id="dialog-buttons"):
                yield Button("Close", id="close", variant="primary", compact=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Panes & Layout
# ---------------------------------------------------------------------------


class EntityPane(Vertical):
    def __init__(self, kind: str, columns: list[str]):
        super().__init__(id=f"pane-{kind}")
        self.kind = kind
        self.columns = columns

    def compose(self) -> ComposeResult:
        search_noun = PLURALS[self.kind].replace("teacher_", "")
        yield Input(placeholder=f"Search {search_noun}…", id=f"filter-{self.kind}", classes="table-filter")
        table = DataTable(id=f"table-{self.kind}", cursor_type="row", zebra_stripes=True)
        for column in self.columns:
            table.add_column(column)
        yield table
        with Horizontal(classes="action-bar entity-bar"):
            yield Button("Add (a)", id=f"add-{self.kind}", variant="primary", compact=True)
            yield Button("Edit (e)", id=f"edit-{self.kind}", compact=True)
            yield Button("Duplicate (c)", id=f"dup-{self.kind}", compact=True)
            yield Button("Delete (d)", id=f"del-{self.kind}", variant="error", compact=True)


class ProjectPane(Vertical):
    def compose(self) -> ComposeResult:
        with VerticalScroll(id="project-scroll"):
            yield Static(id="project-summary")
            yield Static(id="project-feasibility")
        with Horizontal(classes="action-bar"):
            yield Button("Edit project", id="edit-project", variant="primary", compact=True)
            yield Button("Validate (F5)", id="validate", compact=True)
            yield Button("Load sample", id="load-sample", compact=True)
            yield Button("Help (?)", id="help", compact=True)
        with Horizontal(classes="action-bar bottom-row"):
            yield Button("Open… (Ctrl+O)", id="open", compact=True)
            yield Button("Save (Ctrl+S)", id="save", compact=True)
            yield Button("Save as…", id="save-as", compact=True)
            yield Button("New (Ctrl+N)", id="new", compact=True)


class TimetablePane(Vertical):
    def compose(self) -> ComposeResult:
        with Horizontal(classes="toolbar-row tight-row"):
            yield Label("Time (s):")
            yield Input(
                value="60",
                type="number",
                id="opt-time",
                compact=True,
                tooltip="Maximum solver budget in seconds (> 0)",
            )
            yield Label("Seed:")
            yield Input(
                value="42",
                type="integer",
                id="opt-seed",
                compact=True,
                tooltip="Random seed (reproducible with Workers = 1)",
            )
            yield Label("Workers:")
            yield Input(
                value="8",
                type="integer",
                id="opt-workers",
                compact=True,
                tooltip="Solver search threads (1 for deterministic runs)",
            )
            yield Checkbox(
                "Hint",
                value=True,
                id="opt-hint",
                compact=True,
                tooltip="Warm-start from the previous generated timetable",
            )

        with Horizontal(classes="toolbar-row"):
            yield Label("Gap:")
            yield Input(
                placeholder="off",
                type="number",
                id="opt-gap",
                compact=True,
                tooltip="Relative gap limit (e.g. 0.02 = 2%); blank disables",
            )
            yield Label("Patience:")
            yield Input(
                value="5",
                type="number",
                id="opt-patience",
                compact=True,
                tooltip="Stop after N seconds without improvement (0 disables)",
            )
            yield Checkbox(
                "Fast",
                value=False,
                id="opt-fast",
                compact=True,
                tooltip="Quick preview capped at 5 seconds (skips patience)",
            )

        with Horizontal(classes="view-toolbar"):
            yield Label("View:")
            yield Select(
                [
                    ("Class Schedule", "class_grid"),
                    ("Teacher Schedule", "teacher_grid"),
                    ("Room Schedule", "room_grid"),
                    ("All Entries", "list"),
                ],
                value="class_grid",
                id="tt-view-mode",
                allow_blank=False,
                compact=True,
            )
            yield Label("Target:", id="tt-selector-label")
            yield Select([], id="tt-selector", allow_blank=True, prompt="Choose…", compact=True)
            yield Label("Search:", id="tt-filter-label")
            yield Input(placeholder="Filter entries…", id="tt-filter", compact=True)

        with Horizontal(classes="action-bar tt-actions"):
            yield Button("Generate (Ctrl+G)", id="generate", variant="primary", compact=True)
            yield Button("Cancel Solve", id="cancel-solve", variant="error", compact=True, disabled=True)
            yield Button("Export CSV", id="export-csv", compact=True)
            yield Button("Export JSON", id="export-json", compact=True)

        yield Static(id="tt-summary")
        table = DataTable(id="table-timetable", cursor_type="row", zebra_stripes=True)
        yield table

        yield Static("Violations", id="violations-title", classes="section-title")
        violations = DataTable(id="table-violations", cursor_type="row", zebra_stripes=True)
        for column in ("Message", "Value"):
            violations.add_column(column)
        yield violations


# ---------------------------------------------------------------------------
# Main App
# ---------------------------------------------------------------------------


class TimetableApp(App):
    TITLE = "MakeTable"
    SUB_TITLE = "School Timetable Editor & Solver"

    CSS = """
    Screen { layout: vertical; }
    TabbedContent { height: 1fr; }
    TabPane { padding: 0 1; }
    DataTable { height: 1fr; }
    Checkbox { height: 1; border: none !important; background: transparent; }
    Button.-style-default.-textual-compact { background: $surface-lighten-1; color: $text; }
    Button.-style-default.-textual-compact:hover { background: $surface-lighten-2; }
    .action-bar { height: auto; padding: 0 0 1 0; align: left middle; }
    .action-bar.bottom-row { padding: 0; }
    .action-bar.entity-bar { padding: 1 0; }
    .action-bar Button { width: 16; min-width: 16; height: 1; margin-right: 1; }
    .action-bar.tt-actions Button { width: 19; min-width: 19; }
    #cancel-solve { display: none; }
    .toolbar-row { height: auto; padding: 0 0 1 0; align: left middle; }
    .toolbar-row.tight-row { padding: 0; }
    .toolbar-row Label { margin: 0 1 0 0; }
    .toolbar-row Input { width: 7; padding: 0 1; margin-right: 2; background: $surface-lighten-1; }
    .toolbar-row Input:focus { background: $primary-muted; color: $text; }
    #opt-workers { width: 5; }
    #opt-gap, #opt-patience { width: 6; }
    .toolbar-row Checkbox { padding: 0 1 0 0; margin-right: 2; }
    .view-toolbar { height: auto; padding: 0 0 1 0; align: left middle; }
    .view-toolbar Select { width: 20; height: auto; margin-right: 2; }
    #tt-selector { width: 22; }
    .view-toolbar Select > SelectCurrent { padding: 0 1; background: $surface-lighten-1; }
    .view-toolbar Select:focus > SelectCurrent { background: $primary-muted; color: $text; }
    .view-toolbar Label { margin: 0 1 0 0; }
    .view-toolbar Input { width: 22; padding: 0 1; margin-right: 2; background: $surface-lighten-1; }
    .view-toolbar Input:focus { background: $primary-muted; color: $text; }
    .table-filter { margin-bottom: 1; height: 3; }
    #project-summary { padding: 0 0 1 0; height: auto; }
    #project-feasibility { padding: 0 0 1 0; height: auto; }
    #tt-summary { height: auto; padding-bottom: 1; }
    #table-timetable { height: 3fr; }
    #table-violations { height: 1fr; min-height: 4; max-height: 8; }
    .section-title { text-style: bold; padding-top: 1; }
    #status { height: 1; padding: 0 1; background: $panel; color: $text-muted; }

    FormScreen, ConfirmScreen, MessageScreen, PromptScreen, HelpScreen { align: center middle; }
    #dialog { width: 92%; max-width: 80; min-width: 44; height: 92%; max-height: 36; border: thick $primary; background: $surface; padding: 0 2; }
    #dialog-title { text-style: bold; padding: 1 0 0 0; }
    #dialog-body { height: 1fr; padding-top: 1; }
    .field-label { margin-top: 1; text-style: bold; }
    .field-label.first-field { margin-top: 0; }
    .field-help { color: $text-muted; }
    #dialog Checkbox { padding: 0; margin: 0; }
    #dialog-buttons { height: auto; padding: 1 0; align: right middle; }
    #dialog-buttons Button { margin-left: 1; min-width: 12; width: auto; height: 1; }
    SelectionList { height: auto; max-height: 8; }

    #confirm { width: 90%; max-width: 60; min-width: 36; height: auto; border: thick $warning; background: $surface; padding: 1 2; }
    #message { width: 90%; max-width: 74; min-width: 40; height: 75%; border: thick $error; background: $surface; padding: 1 2; }
    #message-body { height: 1fr; }
    #prompt-dialog { width: 90%; max-width: 65; min-width: 36; height: auto; border: thick $primary; background: $surface; padding: 1 2; }
    #prompt-input { margin-top: 1; margin-bottom: 1; }
    #help-dialog { width: 92%; max-width: 80; min-width: 44; height: 85%; border: thick $primary; background: $surface; padding: 1 2; }
    #help-body { height: 1fr; }
    """

    BINDINGS = [
        Binding("ctrl+s", "save", "Save"),
        Binding("ctrl+shift+s", "save_as", "Save as", show=False),
        Binding("ctrl+o", "open", "Open"),
        Binding("ctrl+g", "generate", "Generate"),
        Binding("ctrl+n", "new", "New"),
        Binding("ctrl+q", "quit_app", "Quit"),
        Binding("f5", "validate", "Validate", show=False),
        Binding("question_mark", "help", "Help"),
        Binding("f1", "help", "Help", show=False),
        Binding("a", "add_current", "Add", show=False),
        Binding("e", "edit_current", "Edit", show=False),
        Binding("c", "duplicate_current", "Duplicate", show=False),
        Binding("d", "delete_current", "Delete", show=False),
        Binding("delete", "delete_current", "Delete", show=False),
    ]

    def __init__(self, path: Path | None = None):
        super().__init__()
        self.path = path.expanduser() if path is not None else None
        self.data = TimeTableGenerationInput()
        self.dirty = False
        self.response: GeneratedResponse | None = None
        self.response_stale = False
        self._rows: dict[str, list[Any]] = {kind: [] for kind, _, _ in ENTITY_TABS}
        self._table_search: dict[str, str] = {kind: "" for kind, _, _ in ENTITY_TABS}
        # Bumped on every data change so a result solved from an older snapshot
        # can be marked stale instead of silently presented as current.
        self._revision = 0
        self._solve_run_id = 0
        self._solve_cancel_event: threading.Event | None = None

    # -- layout -------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="tab-project", id="tabs"):
            with TabPane("Project", id="tab-project"):
                yield ProjectPane()
            for kind, title, columns in ENTITY_TABS:
                with TabPane(title, id=f"tab-{kind}"):
                    yield EntityPane(kind, columns)
            with TabPane("Timetable", id="tab-timetable"):
                yield TimetablePane()
        yield Static(id="status")
        yield Footer()

    def on_mount(self) -> None:
        if self.path is not None:
            if self.path.exists():
                self._load_path(self.path)
            else:
                self.dirty = True
                self.notify(f"{self.path} does not exist yet; save to create it.", timeout=5)
        self.call_after_refresh(self._refresh_all)

    # -- loading and saving -------------------------------------------------

    def _load_path(self, path: Path) -> bool:
        path = path.expanduser()
        try:
            data = TimeTableGenerationInput.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, ValueError) as error:
            self.notify(
                f"Could not open {path}: {format_error(error)}",
                title="Open failed",
                severity="error",
                timeout=10,
            )
            return False
        self._abort_active_solve()
        self.data = data
        self.path = path
        self.dirty = False
        self._revision += 1
        self.response = None
        self.response_stale = False
        self._table_search = {kind: "" for kind in self._table_search}
        self._clear_search_inputs()
        self._sync_timetable_selectors()
        self.notify(f"Opened {path}", timeout=3)
        self.call_after_refresh(self._refresh_all)
        return True

    def _save_path(self, path: Path) -> None:
        path = path.expanduser()
        if not path.suffix:
            path = path.with_suffix(".json")
        if not self._validate_data():
            return
        try:
            path.write_text(self.data.model_dump_json(indent=2), encoding="utf-8")
        except OSError as error:
            self.notify(f"Could not save: {error}", severity="error")
            return
        self.path = path
        self.dirty = False
        self._refresh_all()
        self.notify(f"Saved {path}", timeout=3)

    def _validate_data(self) -> bool:
        try:
            TimeTableGenerationInput.model_validate(self.data.model_dump())
        except ValidationError as error:
            self.push_screen(MessageScreen("Project is not valid", format_error(error)))
            return False
        return True

    def _commit(self, mutate: Callable[[], None], title: str = "Change rejected") -> bool:
        snapshot = self.data.model_dump()
        try:
            mutate()
            TimeTableGenerationInput.model_validate(self.data.model_dump())
        except (ValidationError, ValueError, KeyError, TypeError) as error:
            self.data = TimeTableGenerationInput.model_validate(snapshot)
            self._refresh_all()
            self.push_screen(MessageScreen(title, format_error(error)))
            return False
        self.dirty = True
        self.response_stale = self.response is not None
        self._revision += 1
        self._refresh_all()
        return True

    # -- form validation guard rails ----------------------------------------

    def _validate_entity_form(self, kind: str, values: dict[str, Any], existing_id: UUID | None) -> str | None:
        """Pre-submit validation guard rail: catches errors before the modal dismisses."""
        slots = self.data.project.slots
        working_slots = max(1, slots - len(self.data.project.break_slots))
        total_slots = working_slots * len(self.data.project.days)

        # 1. Unique name check (case-insensitive)
        if "name" in values:
            name = values["name"].strip()
            if not name:
                return "Name cannot be empty."
            items = getattr(self.data, PLURALS[kind])
            for item in items:
                if existing_id is not None and item.id == existing_id:
                    continue
                if item.name.strip().casefold() == name.casefold():
                    return f"A {kind} named '{name}' already exists (names must be unique)."

        # 2. Teacher limits
        if kind == "teacher":
            max_d = values.get("max_per_day", 0)
            max_w = values.get("max_per_week", 0)
            max_c = values.get("max_consecutive", 0)
            if max_d < 1:
                return "Max classes per day must be at least 1."
            if max_d > slots:
                return f"Max classes per day ({max_d}) cannot exceed project slots per day ({slots})."
            if max_w < 1:
                return "Max classes per week must be at least 1."
            if max_w > total_slots:
                return f"Max classes per week ({max_w}) cannot exceed total weekly slots ({total_slots})."
            if max_c < 1:
                return "Max consecutive classes must be at least 1."
            if max_c > slots:
                return f"Max consecutive classes ({max_c}) cannot exceed slots per day ({slots})."

            max_days = values.get("max_days_per_week")
            if max_days not in ("", None):
                try:
                    md_val = int(max_days)
                    if md_val < 1 or md_val > len(self.data.project.days):
                        return f"Max working days per week must be between 1 and {len(self.data.project.days)}."
                except ValueError:
                    return "Max working days must be a valid integer."

            unavail_str = values.get("unavailable_slots", "")
            if unavail_str:
                try:
                    unavail_parsed = parse_day_slots(unavail_str)
                    for ds in unavail_parsed:
                        if ds.day not in self.data.project.days:
                            return f"Unavailable slot has day '{ds.day.value}', not in active project days."
                        if not 1 <= ds.slot <= slots:
                            return f"Unavailable slot {ds.slot} must be between 1 and {slots}."
                except ValueError as ex:
                    return f"Invalid unavailable slots: {ex}"

            avoid_str = values.get("avoid_slots", "")
            if avoid_str:
                try:
                    avoid_parsed = parse_day_slots(avoid_str)
                    for ds in avoid_parsed:
                        if ds.day not in self.data.project.days:
                            return f"Avoid slot has day '{ds.day.value}', not in active project days."
                        if not 1 <= ds.slot <= slots:
                            return f"Avoid slot {ds.slot} must be between 1 and {slots}."
                except ValueError as ex:
                    return f"Invalid avoid slots: {ex}"

        # 3. Subject limits
        if kind == "subject":
            max_d = values.get("max_per_day", 0)
            min_d = values.get("min_per_day", 0)
            max_w = values.get("max_per_week", 0)
            min_w = values.get("min_per_week", 0)
            max_c = values.get("max_consecutive", 0)
            min_c = values.get("min_consecutive", 0)

            if max_d < 1:
                return "Max per day must be at least 1."
            if max_d > slots:
                return f"Max per day ({max_d}) cannot exceed project slots per day ({slots})."
            if min_d < 0:
                return "Min per day cannot be negative."
            if min_d > max_d:
                return f"Min per day ({min_d}) cannot exceed max per day ({max_d})."
            if max_w < 1:
                return "Max per week must be at least 1."
            if min_w < 0:
                return "Min per week cannot be negative."
            if min_w > total_slots:
                return f"Min per week ({min_w}) cannot exceed total weekly slots ({total_slots})."
            if min_w > max_w:
                return f"Min per week ({min_w}) cannot exceed max per week ({max_w})."
            if max_c < 1:
                return "Max consecutive must be at least 1."
            if max_c > slots:
                return f"Max consecutive ({max_c}) cannot exceed slots per day ({slots})."
            if min_c < 0:
                return "Min consecutive cannot be negative."
            if min_c > max_c:
                return f"Min consecutive ({min_c}) cannot exceed max consecutive ({max_c})."

        # 4. Room capacity
        if kind == "room":
            cap = values.get("capacity", 1)
            if cap < 1:
                return "Room capacity must be at least 1."

        # 5. Assignment checks
        if kind == "assignment":
            period_len = values.get("period_length", 1)
            if period_len < 1 or period_len > slots:
                return f"Period length ({period_len}) must be between 1 and {slots}."

            class_id = UUID(values["class_id"])
            teacher_id = UUID(values["teacher_id"])
            subject_id = UUID(values["subject_id"])
            tag = (values.get("tag") or "").strip() or None
            for a in self.data.teacher_assignments:
                if existing_id is not None and a.id == existing_id:
                    continue
                if (
                    a.class_id == class_id
                    and a.teacher_id == teacher_id
                    and a.subject_id == subject_id
                    and a.tag == tag
                ):
                    tag_suffix = f" with tag '{tag}'" if tag else ""
                    return f"An assignment for this exact class, teacher and subject{tag_suffix} already exists."

        # 6. Deep schema validation on clone
        try:
            snapshot = self.data.model_copy(deep=True)
            test_entity = self._build(kind, values, self._find_entity(kind, existing_id))
            if existing_id is None:
                getattr(snapshot, PLURALS[kind]).append(test_entity)
            else:
                items = getattr(snapshot, PLURALS[kind])
                for idx, it in enumerate(items):
                    if it.id == existing_id:
                        items[idx] = test_entity
                        break
            if kind == "assignment":
                self._sync_co_schedule_links(snapshot.teacher_assignments, test_entity)
            TimeTableGenerationInput.model_validate(snapshot.model_dump())
        except ValidationError as err:
            return format_error(err)
        except Exception as err:
            return str(err)

        return None

    # -- field definitions --------------------------------------------------

    def _room_options(self) -> list[tuple[str, str]]:
        return [
            (room.name, str(room.id))
            for room in sorted(self.data.rooms, key=lambda item: item.name.lower())
        ]

    def _class_options(self) -> list[tuple[str, str]]:
        return [
            (class_.name, str(class_.id))
            for class_ in sorted(self.data.classes, key=lambda item: item.name.lower())
        ]

    def _teacher_options(self) -> list[tuple[str, str]]:
        return [
            (teacher.name, str(teacher.id))
            for teacher in sorted(self.data.teachers, key=lambda item: item.name.lower())
        ]

    def _subject_options(self) -> list[tuple[str, str]]:
        return [
            (subject.name, str(subject.id))
            for subject in sorted(self.data.subjects, key=lambda item: item.name.lower())
        ]

    def _fields(self, kind: str, exclude_id: UUID | None = None) -> list[Field]:
        slots = self.data.project.slots
        working_slots = max(1, slots - len(self.data.project.break_slots))
        days = max(1, len(self.data.project.days))
        if kind == "room":
            return [
                Field("name", "Name", prompt="e.g. Room 101"),
                Field("is_lab", "Lab room", "bool", default=False),
                Field(
                    "capacity",
                    "Simultaneous classes",
                    "int",
                    default=1,
                    help="How many classes may use this room at the same time.",
                ),
            ]
        if kind == "class":
            return [
                Field("name", "Name", prompt="e.g. Grade 10 - A"),
                Field(
                    "room_id",
                    "Home room",
                    "select",
                    options=self._room_options(),
                    prompt="Select a room",
                ),
            ]
        if kind == "teacher":
            return [
                Field("name", "Name", prompt="e.g. Dr. Sarah Connor"),
                Field("max_per_day", "Max classes per day", "int", default=min(slots, 6)),
                Field("max_per_week", "Max classes per week", "int", default=min(working_slots * days, 25)),
                Field("max_consecutive", "Max consecutive classes", "int", default=2),
                Field("max_days_per_week", "Max working days/week", "int", default="", required=False, help="Leave blank for full week, or e.g. 4 for 1 day off"),
                Field("unavailable_slots", "Unavailable slots (hard block)", "text", default="", required=False, help="e.g. Wed:4, Fri:1-2"),
                Field("avoid_slots", "Avoid slots (soft penalty)", "text", default="", required=False, help="e.g. Mon:1, Fri:8"),
            ]
        if kind == "subject":
            return [
                Field("name", "Name", prompt="e.g. Mathematics"),
                Field(
                    "morning_tendency",
                    "Morning tendency",
                    "select",
                    options=[(level.value, level.value) for level in Level],
                    default=Level.Low.value,
                    help="Low (neutral), Med (prefers earlier), High (strongly prefers morning)",
                ),
                Field("max_per_day", "Max per day", "int", default=2),
                Field("min_per_day", "Min per day", "int", default=0),
                Field("max_per_week", "Max per week", "int", default=min(5, working_slots * days)),
                Field("min_per_week", "Min per week", "int", default=0),
                Field("max_consecutive", "Max consecutive", "int", default=2),
                Field("min_consecutive", "Min consecutive", "int", default=0),
                Field(
                    "fixed_slots",
                    "Fixed slots (always scheduled)",
                    "text",
                    default="",
                    required=False,
                    help="e.g. Tue:6-8, Thu:6-8 (day:slot ranges)",
                ),
            ]
        if kind == "assignment":
            class_map = {c.id: c.name for c in self.data.classes}
            subj_map = {s.id: s.name for s in self.data.subjects}
            teacher_map = {t.id: t.name for t in self.data.teachers}
            co_candidates = sorted(
                (a for a in self.data.teacher_assignments if a.id != exclude_id),
                key=lambda a: (
                    class_map.get(a.class_id, "").lower(),
                    subj_map.get(a.subject_id, "").lower(),
                    (a.tag or "").lower(),
                ),
            )
            co_options = [
                (
                    f"{class_map.get(a.class_id, 'Class')} - {subj_map.get(a.subject_id, 'Subj')}"
                    + (f" [{a.tag}]" if a.tag else "")
                    + f" ({teacher_map.get(a.teacher_id, 'Teacher')})",
                    str(a.id),
                )
                for a in co_candidates
            ]
            return [
                Field(
                    "class_id",
                    "Class",
                    "select",
                    options=self._class_options(),
                    prompt="Select a class",
                ),
                Field(
                    "teacher_id",
                    "Teacher",
                    "select",
                    options=self._teacher_options(),
                    prompt="Select a teacher",
                ),
                Field(
                    "subject_id",
                    "Subject",
                    "select",
                    options=self._subject_options(),
                    prompt="Select a subject",
                ),
                Field(
                    "tag",
                    "Batch / Elective Tag (optional)",
                    "text",
                    default="",
                    required=False,
                    help="e.g. Batch 1, Batch 2, Track A, Elective",
                ),
                Field(
                    "target_room_id",
                    "Target room (optional)",
                    "select",
                    options=self._room_options(),
                    required=False,
                    prompt="Default (Class Home Room)",
                ),
                Field(
                    "period_length",
                    "Period length (slots)",
                    "int",
                    default=1,
                    help="1 for normal period, 2 for double, 3 for 3h lab block",
                ),
                Field(
                    "co_schedule_with",
                    "Co-schedule with (merge / split)",
                    "multi",
                    options=co_options,
                    required=False,
                    help="Schedule simultaneously: split lab batches or merged classes",
                ),
                Field(
                    "first_slot_days",
                    "Must be in first slot on",
                    "multi",
                    options=[(day.value, day.value) for day in self.data.project.days],
                    required=False,
                ),
            ]
        raise ValueError(f"unknown entity: {kind}")

    def _defaults(self, kind: str) -> dict[str, Any]:
        return {item.key: item.default for item in self._fields(kind)}

    def _values(self, kind: str, entity: Any) -> dict[str, Any]:
        if kind == "room":
            return {
                "name": entity.name,
                "is_lab": entity.is_lab,
                "capacity": entity.constraints.capacity,
            }
        if kind == "class":
            return {"name": entity.name, "room_id": str(entity.room_id)}
        if kind == "teacher":
            return {
                "name": entity.name,
                "max_per_day": entity.constraints.max_per_day,
                "max_per_week": entity.constraints.max_per_week,
                "max_consecutive": entity.constraints.max_consecutive,
                "max_days_per_week": entity.constraints.max_days_per_week or "",
                "unavailable_slots": format_day_slots(entity.constraints.unavailable_slots),
                "avoid_slots": format_day_slots(entity.constraints.avoid_slots),
            }
        if kind == "subject":
            return {
                "name": entity.name,
                "morning_tendency": entity.constraints.morning_tendency.value,
                "max_per_day": entity.constraints.max_per_day,
                "min_per_day": entity.constraints.min_per_day,
                "max_per_week": entity.constraints.max_per_week,
                "min_per_week": entity.constraints.min_per_week,
                "max_consecutive": entity.constraints.max_consecutive,
                "min_consecutive": entity.constraints.min_consecutive,
                "fixed_slots": format_day_slots(entity.constraints.fixed_slots),
            }
        if kind == "assignment":
            return {
                "class_id": str(entity.class_id),
                "teacher_id": str(entity.teacher_id),
                "subject_id": str(entity.subject_id),
                "tag": entity.tag or "",
                "target_room_id": (
                    str(entity.target_room_id)
                    if entity.target_room_id is not None
                    else None
                ),
                "period_length": entity.constraints.period_length,
                "co_schedule_with": [str(uid) for uid in entity.constraints.co_schedule_with],
                "first_slot_days": [
                    day.value
                    for day in (entity.constraints.first_slot_days or [])
                ],
            }
        raise ValueError(f"unknown entity: {kind}")

    def _build(self, kind: str, values: dict[str, Any], existing: Any) -> Any:
        entity_id = existing.id if existing is not None else uuid4()
        if kind == "room":
            return RoomInput(
                id=entity_id,
                name=values["name"],
                is_lab=values["is_lab"],
                constraints=RoomConstraints(capacity=values["capacity"]),
            )
        if kind == "class":
            return ClassInput(
                id=entity_id,
                name=values["name"],
                room_id=UUID(values["room_id"]),
            )
        if kind == "teacher":
            unavail = parse_day_slots(values.get("unavailable_slots", ""))
            avoid = parse_day_slots(values.get("avoid_slots", ""))
            max_days_raw = values.get("max_days_per_week")
            max_days = int(max_days_raw) if max_days_raw not in ("", None) else None
            return TeacherInput(
                id=entity_id,
                name=values["name"],
                constraints=TeacherConstraints(
                    max_per_day=values["max_per_day"],
                    max_per_week=values["max_per_week"],
                    max_consecutive=values["max_consecutive"],
                    unavailable_slots=unavail,
                    avoid_slots=avoid,
                    max_days_per_week=max_days,
                ),
            )
        if kind == "subject":
            return SubjectInput(
                id=entity_id,
                name=values["name"],
                constraints=SubjectConstraints(
                    morning_tendency=Level(values["morning_tendency"]),
                    max_per_day=values["max_per_day"],
                    min_per_day=values["min_per_day"],
                    max_per_week=values["max_per_week"],
                    min_per_week=values["min_per_week"],
                    max_consecutive=values["max_consecutive"],
                    min_consecutive=values["min_consecutive"],
                    fixed_slots=parse_day_slots(values.get("fixed_slots", "")),
                ),
            )
        if kind == "assignment":
            target = values.get("target_room_id")
            tag_val = (values.get("tag") or "").strip() or None
            co_ids = [UUID(uid) for uid in values.get("co_schedule_with") or [] if uid != str(entity_id)]
            return TeacherAssignmentInput(
                id=entity_id,
                class_id=UUID(values["class_id"]),
                teacher_id=UUID(values["teacher_id"]),
                subject_id=UUID(values["subject_id"]),
                tag=tag_val,
                target_room_id=UUID(target) if target else None,
                constraints=TeacherAssignmentConstraint(
                    period_length=int(values.get("period_length", 1)),
                    co_schedule_with=co_ids,
                    first_slot_days=[
                        WeekDay(day) for day in values.get("first_slot_days") or []
                    ] or None,
                ),
            )
        raise ValueError(f"unknown entity: {kind}")

    # -- entity actions -----------------------------------------------------

    def _find_entity(self, kind: str, entity_id: UUID | None) -> Any | None:
        """Return the entity with this id, or None when it does not exist."""
        if entity_id is None:
            return None
        return next(
            (
                item
                for item in getattr(self.data, PLURALS[kind])
                if item.id == entity_id
            ),
            None,
        )

    @staticmethod
    def _sync_co_schedule_links(
        assignments: list[TeacherAssignmentInput],
        saved: TeacherAssignmentInput,
    ) -> None:
        """Keep co_schedule_with links symmetric across linked assignments."""
        linked = set(saved.constraints.co_schedule_with)
        for other in assignments:
            if other.id == saved.id:
                continue
            current = other.constraints.co_schedule_with
            if other.id in linked:
                if saved.id not in current:
                    current.append(saved.id)
            elif saved.id in current:
                other.constraints.co_schedule_with = [
                    uid for uid in current if uid != saved.id
                ]

    def _drop_assignment_references(self, removed_ids: set[UUID]) -> None:
        """Remove co-schedule links to assignments that are being deleted."""
        if not removed_ids:
            return
        for assignment in self.data.teacher_assignments:
            if assignment.constraints.co_schedule_with:
                assignment.constraints.co_schedule_with = [
                    other
                    for other in assignment.constraints.co_schedule_with
                    if other not in removed_ids
                ]

    def _add(self, kind: str) -> None:
        if kind == "class" and not self.data.rooms:
            self.notify("Add a room before adding a class.", severity="warning")
            return
        if kind == "assignment":
            missing = [
                label
                for label, items in (
                    ("classes", self.data.classes),
                    ("teachers", self.data.teachers),
                    ("subjects", self.data.subjects),
                )
                if not items
            ]
            if missing:
                self.notify(
                    "Add " + ", ".join(missing) + " before adding an assignment.",
                    severity="warning",
                )
                return

        validator = lambda vals: self._validate_entity_form(kind, vals, None)
        self.push_screen(
            FormScreen(f"Add {kind}", self._fields(kind), self._defaults(kind), validator=validator),
            lambda result: self._apply_form(kind, result, None),
        )

    def _selected(self, kind: str) -> Any | None:
        table = self.query_one(f"#table-{kind}", DataTable)
        if table.row_count == 0:
            return None
        index = table.cursor_row
        rows = self._rows.get(kind, [])
        if 0 <= index < len(rows):
            return rows[index]
        return None

    def _edit(self, kind: str) -> None:
        entity = self._selected(kind)
        if entity is None:
            self.notify("Select a row first.", severity="warning")
            return
        validator = lambda vals: self._validate_entity_form(kind, vals, entity.id)
        self.push_screen(
            FormScreen(
                f"Edit {kind}",
                self._fields(kind, exclude_id=entity.id),
                self._values(kind, entity),
                validator=validator,
            ),
            lambda result: self._apply_form(kind, result, entity),
        )

    def _duplicate(self, kind: str) -> None:
        entity = self._selected(kind)
        if entity is None:
            self.notify("Select a row first.", severity="warning")
            return
        values = self._values(kind, entity)
        if "name" in values:
            values["name"] = f"{values['name']} (copy)"
        validator = lambda vals: self._validate_entity_form(kind, vals, None)
        self.push_screen(
            FormScreen(f"Duplicate {kind}", self._fields(kind), values, validator=validator),
            lambda result: self._apply_form(kind, result, None),
        )

    def _apply_form(
        self, kind: str, values: dict[str, Any] | None, existing: Any | None
    ) -> None:
        if values is None:
            return

        def mutate() -> None:
            entity = self._build(kind, values, existing)
            items = getattr(self.data, PLURALS[kind])
            if existing is None:
                items.append(entity)
            else:
                for index, item in enumerate(items):
                    if item.id == existing.id:
                        items[index] = entity
                        break
                else:
                    items.append(entity)
            if kind == "assignment":
                self._sync_co_schedule_links(self.data.teacher_assignments, entity)

        self._commit(mutate)

    def _delete(self, kind: str) -> None:
        entity = self._selected(kind)
        if entity is None:
            self.notify("Select a row first.", severity="warning")
            return

        if kind == "room":
            used_by = [
                class_.name
                for class_ in self.data.classes
                if class_.room_id == entity.id
            ]
            if used_by:
                self.push_screen(
                    MessageScreen(
                        "Room is in use as Home Room",
                        "Reassign these classes before deleting the room:\n"
                        + "\n".join(f"• {name}" for name in used_by),
                    )
                )
                return
            targeted = [
                assignment
                for assignment in self.data.teacher_assignments
                if assignment.target_room_id == entity.id
            ]
            if targeted:
                self.push_screen(
                    ConfirmScreen(
                        f"Delete '{entity.name}' and clear it as the target room on {len(targeted)} assignment(s)?",
                        "Delete Room",
                    ),
                    lambda confirmed: self._delete_room(
                        entity, targeted if confirmed else []
                    ),
                )
                return
            self._remove(kind, entity)
            return

        if kind in ("class", "teacher", "subject"):
            field_name = {
                "class": "class_id",
                "teacher": "teacher_id",
                "subject": "subject_id",
            }[kind]
            related = [
                assignment
                for assignment in self.data.teacher_assignments
                if getattr(assignment, field_name) == entity.id
            ]
            if related:
                self.push_screen(
                    ConfirmScreen(
                        f"Delete '{entity.name}' and {len(related)} related assignment(s)?",
                        "Delete & Cascade",
                    ),
                    lambda confirmed: (
                        self._remove(kind, entity, related) if confirmed else None
                    ),
                )
                return
            self._remove(kind, entity)
            return

        self._remove(kind, entity)

    def _delete_room(self, room: RoomInput, targeted: list[Any]) -> None:
        if not targeted:
            return
        target_ids = {assignment.id for assignment in targeted}

        def mutate() -> None:
            for assignment in self.data.teacher_assignments:
                if assignment.id in target_ids:
                    assignment.target_room_id = None
            self.data.rooms = [
                item for item in self.data.rooms if item.id != room.id
            ]

        self._commit(mutate)

    def _remove(
        self,
        kind: str,
        entity: Any,
        related: list[Any] | None = None,
    ) -> None:
        related_ids = {assignment.id for assignment in (related or [])}
        removed_assignment_ids = set(related_ids)
        if kind == "assignment":
            removed_assignment_ids.add(entity.id)

        def mutate() -> None:
            self._drop_assignment_references(removed_assignment_ids)
            if related_ids:
                self.data.teacher_assignments = [
                    assignment
                    for assignment in self.data.teacher_assignments
                    if assignment.id not in related_ids
                ]
            items = getattr(self.data, PLURALS[kind])
            items[:] = [item for item in items if item.id != entity.id]

        self._commit(mutate)

    # -- project actions ----------------------------------------------------

    def _edit_project(self) -> None:
        fields = [
            Field("name", "Project name"),
            Field("slots", "Slots per day", "int", default=self.data.project.slots),
            Field(
                "break_slots",
                "Break slots (e.g. 4 or 4, 5)",
                "text",
                default=", ".join(str(s) for s in self.data.project.break_slots),
                required=False,
                help="Universal lunch/break periods across all days (comma-separated integers).",
            ),
            Field(
                "days",
                "Days",
                "multi",
                options=[(day.value, day.value) for day in WeekDay],
                required=True,
                help="Select at least one day.",
            ),
        ]
        values = {
            "name": self.data.project.name,
            "slots": self.data.project.slots,
            "break_slots": ", ".join(str(s) for s in self.data.project.break_slots),
            "days": [day.value for day in self.data.project.days],
        }

        def parse_breaks(raw: str, max_slots: int) -> list[int]:
            breaks = []
            for chunk in str(raw or "").split(","):
                chunk = chunk.strip()
                if not chunk:
                    continue
                try:
                    s_idx = int(chunk)
                except ValueError as exc:
                    raise ValueError(f"Invalid break slot '{chunk}', must be an integer.") from exc
                if s_idx < 1 or s_idx > max_slots:
                    raise ValueError(f"Break slot {s_idx} must be between 1 and total slots ({max_slots}).")
                breaks.append(s_idx)
            unique_breaks = sorted(set(breaks))
            if len(unique_breaks) >= max_slots:
                raise ValueError("Break slots cannot occupy all slots in a day.")
            return unique_breaks

        def build_project(vals: dict[str, Any]) -> ProjectInput:
            slots = vals.get("slots", 0)
            b_slots = parse_breaks(vals.get("break_slots", ""), slots)
            return ProjectInput(
                id=self.data.project.id,
                name=vals["name"],
                slots=slots,
                break_slots=b_slots,
                days=[WeekDay(day) for day in (vals.get("days") or [])],
            )

        def validator(vals: dict[str, Any]) -> str | None:
            if not vals.get("days"):
                return "At least one day must be selected."
            slots = vals.get("slots", 0)
            if slots < 1 or slots > 24:
                return "Slots per day must be between 1 and 24."
            try:
                candidate = build_project(vals)
                snapshot = self.data.model_copy(deep=True)
                snapshot.project = candidate
                TimeTableGenerationInput.model_validate(snapshot.model_dump())
            except ValidationError as err:
                return format_error(err)
            except ValueError as err:
                return str(err)
            return None

        def apply(result: dict[str, Any] | None) -> None:
            if result is None:
                return
            project = build_project(result)

            def mutate() -> None:
                self.data.project = project

            self._commit(mutate)

        self.push_screen(FormScreen("Edit project", fields, values, validator=validator), apply)

    def _new(self) -> None:
        def reset() -> None:
            self._abort_active_solve()
            self.data = TimeTableGenerationInput()
            self.path = None
            self.dirty = True
            self._revision += 1
            self.response = None
            self.response_stale = False
            self._table_search = {kind: "" for kind in self._table_search}
            self._clear_search_inputs()
            self._sync_timetable_selectors()
            self._refresh_all()
            self.notify("Started a new project.", timeout=3)

        if self.dirty:
            self.push_screen(
                ConfirmScreen("Discard unsaved changes and start a new project?", "New Project"),
                lambda confirmed: reset() if confirmed else None,
            )
        else:
            reset()

    def action_load_sample(self) -> None:
        def do_load() -> None:
            sample_candidates = [
                Path("test_json.json"),
                Path(__file__).resolve().parents[2] / "test_json.json",
            ]
            for p in sample_candidates:
                if p.exists():
                    if self._load_path(p):
                        self.notify("Loaded sample project.", timeout=4)
                    return
            self.notify("Sample file (test_json.json) not found.", severity="error")

        if self.dirty:
            self.push_screen(
                ConfirmScreen("Discard unsaved changes and load sample project?", "Load Sample"),
                lambda confirmed: do_load() if confirmed else None,
            )
        else:
            do_load()

    def action_open(self) -> None:
        if self.dirty:
            self.push_screen(
                ConfirmScreen("Discard unsaved changes and open another project?", "Open"),
                lambda confirmed: self._open_prompt() if confirmed else None,
            )
            return
        self._open_prompt()

    def _open_prompt(self) -> None:
        default_val = str(self.path) if self.path else "timetable.json"
        self.push_screen(
            PromptScreen("Open project", "Project file path:", default_val),
            lambda result: self._load_path(Path(result)) if result else None,
        )

    def action_save(self) -> None:
        if self.path is None:
            self.action_save_as()
            return
        self._save_path(self.path)

    def action_save_as(self) -> None:
        default_val = str(self.path) if self.path else "timetable.json"
        self.push_screen(
            PromptScreen("Save project as", "Save destination path:", default_val),
            lambda result: self._save_path(Path(result)) if result else None,
        )

    def action_validate(self) -> None:
        if self._validate_data():
            self.notify("Project is valid with all guard rails satisfied.", timeout=4)

    def action_new(self) -> None:
        self._new()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_quit_app(self) -> None:
        def do_quit() -> None:
            self._abort_active_solve()
            self.exit()

        if self.dirty:
            self.push_screen(
                ConfirmScreen("Discard unsaved changes and quit?", "Quit"),
                lambda confirmed: do_quit() if confirmed else None,
            )
        else:
            do_quit()

    def action_quit(self) -> None:
        self.action_quit_app()

    # -- tab context actions (a, e, c, d) -----------------------------------

    def _current_kind(self) -> str | None:
        active = self.query_one("#tabs", TabbedContent).active or ""
        if active.startswith("tab-"):
            kind = active[len("tab-") :]
            if kind in PLURALS:
                return kind
        return None

    def action_add_current(self) -> None:
        kind = self._current_kind()
        if kind:
            self._add(kind)

    def action_edit_current(self) -> None:
        active = self.query_one("#tabs", TabbedContent).active or ""
        if active == "tab-project":
            self._edit_project()
            return
        kind = self._current_kind()
        if kind:
            self._edit(kind)

    def action_duplicate_current(self) -> None:
        kind = self._current_kind()
        if kind:
            self._duplicate(kind)

    def action_delete_current(self) -> None:
        kind = self._current_kind()
        if kind:
            self._delete(kind)

    # -- generation & export ------------------------------------------------

    def action_generate(self) -> None:
        if self.query_one("#generate", Button).disabled:
            return
        if not self._validate_data():
            return
        if not self.data.teacher_assignments:
            self.notify("Add teacher assignments before generating.", severity="warning")
            return
        try:
            time_limit = float(self.query_one("#opt-time", Input).value)
            seed = int(self.query_one("#opt-seed", Input).value)
            workers = int(self.query_one("#opt-workers", Input).value)
            patience = float(self.query_one("#opt-patience", Input).value)
            gap_text = self.query_one("#opt-gap", Input).value.strip()
            gap_limit = float(gap_text) if gap_text else None
            fast = self.query_one("#opt-fast", Checkbox).value
        except ValueError:
            self.notify(
                "Time limit, seed, workers, patience and gap must be numbers.",
                severity="error",
            )
            return
        if not math.isfinite(time_limit) or time_limit <= 0:
            self.notify("Time limit must be a finite number greater than zero.", severity="error")
            return
        if workers < 1:
            self.notify("Workers must be at least 1.", severity="error")
            return
        if not math.isfinite(patience) or patience < 0:
            self.notify("Patience must be a finite number >= 0.", severity="error")
            return
        if gap_limit is not None and (not math.isfinite(gap_limit) or gap_limit < 0):
            self.notify("Gap limit must be a finite number >= 0.", severity="error")
            return

        hint = (
            self.response
            if self.query_one("#opt-hint", Checkbox).value and self.response
            else None
        )
        data = self.data.model_copy(deep=True)
        revision = self._revision
        self._solve_run_id += 1
        run_id = self._solve_run_id
        cancel_event = threading.Event()
        self._solve_cancel_event = cancel_event
        self._set_solving_ui(True)
        self.query_one("#status", Static).update(
            "[yellow]Phase 1/2: Solving with hard constraints…[/yellow]"
        )
        self.query_one("#tt-summary", Static).update(
            "[yellow]Phase 1/2: Solving with hard constraints…[/yellow]"
        )
        self._solve_worker(
            data,
            time_limit,
            seed,
            workers,
            hint,
            fast,
            gap_limit,
            patience,
            revision,
            run_id,
            cancel_event,
        )

    def _set_solving_ui(self, solving: bool) -> None:
        if not self.is_mounted:
            return
        gen_btn = self.query_one("#generate", Button)
        cancel_btn = self.query_one("#cancel-solve", Button)
        gen_btn.disabled = solving
        gen_btn.display = not solving
        cancel_btn.disabled = not solving
        cancel_btn.display = solving

    def _abort_active_solve(self) -> None:
        self._solve_run_id += 1
        if self._solve_cancel_event is not None:
            self._solve_cancel_event.set()
            self._solve_cancel_event = None
        self._set_solving_ui(False)

    def _cancel_solve(self) -> None:
        if self._solve_cancel_event is None or self._solve_cancel_event.is_set():
            return
        self._solve_cancel_event.set()
        self.query_one("#cancel-solve", Button).disabled = True
        self.query_one("#status", Static).update("[yellow]Cancelling solve…[/yellow]")
        self.query_one("#tt-summary", Static).update("[yellow]Cancelling solve…[/yellow]")

    @work(thread=True, exclusive=True, group="solve")
    def _solve_worker(
        self,
        data: TimeTableGenerationInput,
        time_limit: float,
        seed: int,
        workers: int,
        hint: GeneratedResponse | None,
        fast: bool,
        gap_limit: float | None,
        patience: float,
        revision: int,
        run_id: int,
        cancel_event: threading.Event,
    ) -> None:
        try:
            from .solver import TimeTableGenerator

            response = TimeTableGenerator(RawData=data, HintData=hint).solve(
                time_limit_sec=time_limit,
                seed=seed,
                workers=workers,
                fast=fast,
                gap_limit=gap_limit,
                patience=patience,
                on_phase=lambda phase, msg: self.call_from_thread(
                    self._on_solve_phase, phase, msg, run_id, cancel_event
                ),
                cancel_event=cancel_event,
            )
        except Exception as error:
            if cancel_event.is_set():
                self.call_from_thread(self._on_solve_cancelled, run_id)
            else:
                self.call_from_thread(self._on_solve_error, error, run_id)
            return
        if cancel_event.is_set():
            self.call_from_thread(self._on_solve_cancelled, run_id)
            return
        self.call_from_thread(self._on_solved, response, revision, run_id)

    def _on_solve_phase(
        self,
        phase: str,
        message: str,
        run_id: int | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        if not self.is_mounted:
            return
        if run_id is not None and run_id != self._solve_run_id:
            return
        if cancel_event is not None and cancel_event.is_set():
            return
        if phase == "soft":
            self.query_one("#status", Static).update(f"[bold yellow]{message}[/bold yellow]")
            self.query_one("#tt-summary", Static).update(f"[bold yellow]{message}[/bold yellow]")
            self.notify(
                "Hard constraints could not be satisfied; softening constraints (violations may appear).",
                title="Phase 2: Softening Constraints",
                severity="warning",
                timeout=6,
            )
        else:
            self.query_one("#status", Static).update(f"[yellow]{message}[/yellow]")
            self.query_one("#tt-summary", Static).update(f"[yellow]{message}[/yellow]")

    def _on_solve_cancelled(self, run_id: int | None = None) -> None:
        if run_id is not None and run_id != self._solve_run_id:
            return
        self._solve_cancel_event = None
        self._set_solving_ui(False)
        self._refresh_timetable()
        self._refresh_status()
        self.notify("Timetable generation cancelled.", timeout=4)

    def _on_solve_error(
        self, error: BaseException, run_id: int | None = None
    ) -> None:
        if run_id is not None and run_id != self._solve_run_id:
            return
        self._solve_cancel_event = None
        self._set_solving_ui(False)
        self._refresh_timetable()
        self._refresh_status()
        self.push_screen(MessageScreen("Solver failed", format_error(error)))

    def _on_solved(
        self,
        response: GeneratedResponse,
        revision: int | None = None,
        run_id: int | None = None,
    ) -> None:
        if run_id is not None and run_id != self._solve_run_id:
            return
        self._solve_cancel_event = None
        self.response = response
        self.response_stale = revision is not None and revision != self._revision
        self._set_solving_ui(False)
        self._sync_timetable_selectors()
        self._refresh_timetable()
        self._refresh_status()
        # Clear focus before switching tabs so a focused widget inside another
        # tab does not cause TabbedContent to snap back when hidden.
        self.set_focus(None)
        self.query_one("#tabs", TabbedContent).active = "tab-timetable"
        if response.success:
            if response.violations:
                self.notify(
                    f"Generated {len(response.entries)} entries with {len(response.violations)} softened violation(s).",
                    severity="warning",
                    timeout=6,
                )
            else:
                self.notify(
                    f"Generated {len(response.entries)} entries cleanly (0 violations).",
                    timeout=5,
                )
        else:
            self.notify(
                "No feasible timetable was found. Check the violations table.",
                severity="error",
                timeout=8,
            )

    def _sync_timetable_selectors(self) -> None:
        if not self.is_mounted:
            return
        selector = self.query_one("#tt-selector", Select)
        if self.response is None or not self.response.entries:
            selector.set_options([])
            return
        view_mode = self.query_one("#tt-view-mode", Select).value
        options: list[tuple[str, str]] = []
        if view_mode == "class_grid":
            options = [(c, c) for c in self.response.get_classes()]
        elif view_mode == "teacher_grid":
            options = [(t, t) for t in self.response.get_teachers()]
        elif view_mode == "room_grid":
            options = [(r, r) for r in self.response.get_rooms()]

        selector.set_options(options)
        if options and (selector.value is Select.NULL or selector.value not in [v for _, v in options]):
            selector.value = options[0][1]

    def _export_csv(self) -> None:
        if self.response is None or not self.response.entries:
            self.notify("Generate a timetable first before exporting.", severity="warning")
            return
        self.push_screen(
            PromptScreen("Export Timetable as CSV", "File destination path:", "timetable.csv"),
            lambda path: self._do_export_csv(Path(path)) if path else None,
        )

    def _do_export_csv(self, path: Path) -> None:
        path = path.expanduser()
        if not path.suffix:
            path = path.with_suffix(".csv")
        try:
            path.write_text(self.response.to_csv(), encoding="utf-8")
            self.notify(f"Exported CSV to {path}", timeout=4)
        except OSError as err:
            self.notify(f"Could not export CSV: {err}", severity="error")

    def _export_json(self) -> None:
        if self.response is None:
            self.notify("Generate a timetable first before exporting.", severity="warning")
            return
        self.push_screen(
            PromptScreen("Export Timetable as JSON", "File destination path:", "timetable_result.json"),
            lambda path: self._do_export_json(Path(path)) if path else None,
        )

    def _do_export_json(self, path: Path) -> None:
        path = path.expanduser()
        if not path.suffix:
            path = path.with_suffix(".json")
        try:
            path.write_text(self.response.model_dump_json(indent=2), encoding="utf-8")
            self.notify(f"Exported JSON to {path}", timeout=4)
        except OSError as err:
            self.notify(f"Could not export JSON: {err}", severity="error")

    # -- rendering ----------------------------------------------------------

    def _clear_search_inputs(self) -> None:
        if not self.is_mounted:
            return
        for kind in self._table_search:
            inp = self.query_one(f"#filter-{kind}", Input)
            if inp.value:
                inp.value = ""
        tt_filter = self.query_one("#tt-filter", Input)
        if tt_filter.value:
            tt_filter.value = ""

    def _refresh_all(self) -> None:
        self._refresh_project()
        for kind, _, _ in ENTITY_TABS:
            self._refresh_entity(kind)
        self._refresh_timetable()
        self._refresh_status()

    def _refresh_project(self) -> None:
        project = self.data.project
        days = ", ".join(day.value for day in project.days)
        breaks_str = ", ".join(str(s) for s in project.break_slots) if project.break_slots else "none"
        working_slots = project.slots - len(project.break_slots)
        total_slots = working_slots * len(project.days)

        summary = (
            f"[bold cyan]{project.name}[/bold cyan]\n"
            f"[bold]Configuration:[/bold] {project.slots} slots/day (breaks: {breaks_str}) · {len(project.days)} active days ({days}) = [bold]{total_slots}[/bold] working slots/week\n"
            f"[bold]Entities:[/bold] {len(self.data.rooms)} rooms · {len(self.data.classes)} classes · "
            f"{len(self.data.teachers)} teachers · {len(self.data.subjects)} subjects · "
            f"{len(self.data.teacher_assignments)} assignments\n"
            f"[bold]File:[/bold] {self.path or '[yellow]untitled (unsaved)[/yellow]'}"
        )
        self.query_one("#project-summary", Static).update(summary)

        # Capacity & Workload Feasibility metrics (co-scheduled splits share slots)
        total_student_slots = total_slots * len(self.data.classes)
        req_by_class = class_weekly_requirements(
            self.data.teacher_assignments, self.data.subjects
        )
        required_periods = sum(req_by_class.values())

        teacher_capacity = sum(t.constraints.max_per_week for t in self.data.teachers)

        per_class_feasible = all(req <= total_slots for req in req_by_class.values())
        status_text = "[green]Feasible[/green]" if per_class_feasible else "[red]Over-allocated[/red]"
        feasibility = (
            f"[bold]Workload & Capacity Analysis:[/bold]\n"
            f"• Total Class Slot Capacity: [bold]{total_student_slots}[/bold] periods\n"
            f"• Minimum Required Class Periods: [bold]{required_periods}[/bold] periods ({status_text})\n"
            f"• Total Teacher Capacity: [bold]{teacher_capacity}[/bold] weekly periods"
        )
        self.query_one("#project-feasibility", Static).update(feasibility)

    def _refresh_entity(self, kind: str) -> None:
        table = self.query_one(f"#table-{kind}", DataTable)
        prev_selected = self._selected(kind)
        prev_id = getattr(prev_selected, "id", None)
        prev_row = table.cursor_row
        table.clear()
        filter_text = self._table_search.get(kind, "").lower()
        rows: list[Any] = []

        if kind == "room":
            items = sorted(self.data.rooms, key=lambda item: item.name.lower())
            for room in items:
                row_data = [
                    room.name,
                    "yes" if room.is_lab else "no",
                    str(room.constraints.capacity),
                ]
                if filter_text and not any(filter_text in cell.lower() for cell in row_data):
                    continue
                table.add_row(*row_data, key=str(room.id))
                rows.append(room)
        elif kind == "class":
            room_names = {room.id: room.name for room in self.data.rooms}
            items = sorted(self.data.classes, key=lambda item: item.name.lower())
            for class_ in items:
                row_data = [
                    class_.name,
                    room_names.get(class_.room_id, "[red]missing[/red]"),
                ]
                if filter_text and not any(filter_text in cell.lower() for cell in row_data):
                    continue
                table.add_row(*row_data, key=str(class_.id))
                rows.append(class_)
        elif kind == "teacher":
            items = sorted(self.data.teachers, key=lambda item: item.name.lower())
            for teacher in items:
                max_days = str(teacher.constraints.max_days_per_week or "all")
                unavail = format_day_slots(teacher.constraints.unavailable_slots) or "none"
                avoid = format_day_slots(teacher.constraints.avoid_slots) or "none"
                row_data = [
                    teacher.name,
                    str(teacher.constraints.max_per_day),
                    str(teacher.constraints.max_per_week),
                    str(teacher.constraints.max_consecutive),
                    max_days,
                    unavail,
                    avoid,
                ]
                if filter_text and not any(filter_text in cell.lower() for cell in row_data):
                    continue
                table.add_row(*row_data, key=str(teacher.id))
                rows.append(teacher)
        elif kind == "subject":
            items = sorted(self.data.subjects, key=lambda item: item.name.lower())
            for subject in items:
                constraints = subject.constraints
                row_data = [
                    subject.name,
                    constraints.morning_tendency.value,
                    str(constraints.max_per_day),
                    str(constraints.min_per_day),
                    str(constraints.max_per_week),
                    str(constraints.min_per_week),
                    str(constraints.max_consecutive),
                    str(constraints.min_consecutive),
                    format_day_slots(constraints.fixed_slots) or "none",
                ]
                if filter_text and not any(filter_text in cell.lower() for cell in row_data):
                    continue
                table.add_row(*row_data, key=str(subject.id))
                rows.append(subject)
        elif kind == "assignment":
            classes = {item.id: item for item in self.data.classes}
            teachers = {item.id: item.name for item in self.data.teachers}
            subjects = {item.id: item.name for item in self.data.subjects}
            rooms = {item.id: item.name for item in self.data.rooms}
            items = sorted(
                self.data.teacher_assignments,
                key=lambda item: (
                    classes[item.class_id].name.lower() if item.class_id in classes else "",
                    subjects.get(item.subject_id, "").lower(),
                    (item.tag or "").lower(),
                    teachers.get(item.teacher_id, "").lower(),
                ),
            )
            for assignment in items:
                class_ = classes.get(assignment.class_id)
                class_name = class_.name if class_ else "[red]missing[/red]"
                home_room = rooms.get(class_.room_id, "missing") if class_ else "missing"
                room = (
                    rooms.get(assignment.target_room_id, "missing")
                    if assignment.target_room_id is not None
                    else f"Home: {home_room}"
                )
                first_slots = ", ".join(
                    day.value for day in (assignment.constraints.first_slot_days or [])
                )
                length_str = f"{assignment.constraints.period_length} slot(s)"
                co_sched_str = f"{len(assignment.constraints.co_schedule_with)} linked" if assignment.constraints.co_schedule_with else "none"
                row_data = [
                    class_name,
                    teachers.get(assignment.teacher_id, "missing"),
                    subjects.get(assignment.subject_id, "missing"),
                    assignment.tag or "—",
                    room,
                    length_str,
                    co_sched_str,
                    first_slots or "none",
                ]
                if filter_text and not any(filter_text in cell.lower() for cell in row_data):
                    continue
                table.add_row(*row_data, key=str(assignment.id))
                rows.append(assignment)

        self._rows[kind] = rows
        if rows:
            target_row = next(
                (idx for idx, item in enumerate(rows) if getattr(item, "id", None) == prev_id),
                min(max(prev_row, 0), len(rows) - 1) if prev_row >= 0 else 0,
            )
            table.move_cursor(row=target_row)

    @staticmethod
    def _populate_grid_columns(table: DataTable, slots: list[int], breaks_set: set[int]) -> None:
        table.clear(columns=True)
        table.add_column("Day")
        for s in slots:
            table.add_column(f"Slot {s} [dim](Break)[/dim]" if s in breaks_set else f"Slot {s}")

    def _refresh_timetable(self) -> None:
        table = self.query_one("#table-timetable", DataTable)
        violations_title = self.query_one("#violations-title", Static)
        violations = self.query_one("#table-violations", DataTable)
        violations.clear()

        try:
            view_mode = self.query_one("#tt-view-mode", Select).value
            selected_entity = self.query_one("#tt-selector", Select).value
            needle = self.query_one("#tt-filter", Input).value.strip().lower()
        except Exception:
            view_mode = "class_grid"
            selected_entity = Select.NULL
            needle = ""

        is_list = view_mode == "list"
        self.query_one("#tt-selector", Select).display = not is_list
        self.query_one("#tt-selector-label", Label).display = not is_list
        self.query_one("#tt-filter-label", Label).display = is_list
        self.query_one("#tt-filter", Input).display = is_list

        if self.response is None:
            table.clear(columns=True)
            violations_title.display = False
            violations.display = False
            self.query_one("#tt-summary", Static).update(
                "No timetable generated yet. Press [bold]Generate[/bold] or press [bold]Ctrl+G[/bold]."
            )
            return

        total_violations = len(self.response.violations)
        has_violations = total_violations > 0
        violations_title.display = has_violations
        violations.display = has_violations
        if has_violations:
            violations_title.update(f"Violations ({total_violations})")
            wrap_width = max(36, self.size.width - 14)
            for index, violation in enumerate(self.response.violations):
                wrapped = textwrap.fill(violation.error_msg, width=wrap_width)
                violations.add_row(wrapped, str(violation.val), height=None, key=f"v-{index}")

        total_entries = len(self.response.entries)
        days_order = [d.value for d in self.data.project.days]
        slots = list(range(1, self.data.project.slots + 1))
        breaks_set = set(self.data.project.break_slots)

        if is_list:
            table.clear(columns=True)
            for col in ("Day", "Slot", "Class", "Subject", "Teacher", "Room"):
                table.add_column(col)
            shown = 0
            for index, entry in enumerate(self.response.entries):
                tag_badge = f" [{entry.tag}]" if entry.tag else ""
                subj_tag = (
                    f"{entry.subject_name}{tag_badge} ({entry.period_length}h)"
                    if entry.period_length > 1
                    else f"{entry.subject_name}{tag_badge}"
                )
                cells = [
                    entry.day.value,
                    str(entry.slot),
                    entry.class_name,
                    subj_tag,
                    entry.teacher_name,
                    entry.room_name,
                ]
                if needle and not any(needle in cell.lower() for cell in cells):
                    continue
                table.add_row(*cells, key=f"tt-{index}")
                shown += 1
            summary = (
                f"[green]Solved.[/green] {total_entries} entries"
                f"{f' · showing {shown}' if needle else ''} · {total_violations} violation(s)"
            )
        elif view_mode == "class_grid":
            self._populate_grid_columns(table, slots, breaks_set)
            class_name = selected_entity if selected_entity is not Select.NULL else (
                self.response.get_classes()[0] if self.response.get_classes() else ""
            )
            if class_name:
                grid = self.response.get_class_grid(class_name, total_slots=self.data.project.slots)
                for d in days_order:
                    row = [d]
                    for s in slots:
                        entries = grid.get(d, {}).get(s, [])
                        if entries:
                            parts = []
                            for entry in entries:
                                tag_badge = f" [{entry.tag}]" if entry.tag else ""
                                dur = f" [{entry.period_length}h]" if entry.period_length > 1 else ""
                                parts.append(f"{entry.subject_name}{tag_badge}{dur}\n{entry.teacher_name} ({entry.room_name})")
                            row.append("\n---\n".join(parts))
                        elif s in breaks_set:
                            row.append("[dim italic]BREAK[/dim italic]")
                        else:
                            row.append("—")
                    # Auto-height so concurrent split/merged entries are all visible
                    # instead of being clipped to the first line.
                    table.add_row(*row, height=None, key=f"grid-{d}")
            summary = f"[green]Solved.[/green] Weekly schedule for [bold cyan]{class_name or 'None'}[/bold cyan] · {total_violations} violation(s)"
        elif view_mode == "teacher_grid":
            self._populate_grid_columns(table, slots, breaks_set)
            teacher_name = selected_entity if selected_entity is not Select.NULL else (
                self.response.get_teachers()[0] if self.response.get_teachers() else ""
            )
            if teacher_name:
                grid = self.response.get_teacher_grid(teacher_name, total_slots=self.data.project.slots)
                for d in days_order:
                    row = [d]
                    for s in slots:
                        entries = grid.get(d, {}).get(s, [])
                        if entries:
                            parts = []
                            for entry in entries:
                                tag_badge = f" [{entry.tag}]" if entry.tag else ""
                                dur = f" [{entry.period_length}h]" if entry.period_length > 1 else ""
                                parts.append(f"{entry.class_name}{tag_badge}\n{entry.subject_name}{dur} ({entry.room_name})")
                            row.append("\n---\n".join(parts))
                        elif s in breaks_set:
                            row.append("[dim italic]BREAK[/dim italic]")
                        else:
                            row.append("—")
                    table.add_row(*row, height=None, key=f"grid-{d}")
            summary = f"[green]Solved.[/green] Weekly teaching schedule for [bold cyan]{teacher_name or 'None'}[/bold cyan] · {total_violations} violation(s)"
        elif view_mode == "room_grid":
            self._populate_grid_columns(table, slots, breaks_set)
            room_name = selected_entity if selected_entity is not Select.NULL else (
                self.response.get_rooms()[0] if self.response.get_rooms() else ""
            )
            if room_name:
                grid = self.response.get_room_grid(room_name, total_slots=self.data.project.slots)
                for d in days_order:
                    row = [d]
                    for s in slots:
                        entries = grid.get(d, {}).get(s, [])
                        if entries:
                            parts = []
                            for e in entries:
                                tag_badge = f" [{e.tag}]" if e.tag else ""
                                dur = f" [{e.period_length}h]" if e.period_length > 1 else ""
                                parts.append(f"{e.class_name}{tag_badge}: {e.subject_name}{dur} ({e.teacher_name})")
                            row.append("\n---\n".join(parts))
                        elif s in breaks_set:
                            row.append("[dim italic]BREAK[/dim italic]")
                        else:
                            row.append("—")
                    table.add_row(*row, height=None, key=f"grid-{d}")
            summary = f"[green]Solved.[/green] Room utilization for [bold cyan]{room_name or 'None'}[/bold cyan] · {total_violations} violation(s)"
        else:
            summary = ""

        if not self.response.success:
            entry_info = f" ({total_entries} entries)" if total_entries else ""
            summary = f"[red]No feasible timetable found.[/red]{entry_info} · {total_violations} violation(s)"
        if self.response_stale:
            summary = (
                "[yellow]Results are from an earlier project state — "
                "regenerate to refresh.[/yellow] " + summary
            )

        self.query_one("#tt-summary", Static).update(summary)

    def _refresh_status(self) -> None:
        state = "saved" if self.dirty is False and self.path is not None else "[yellow]unsaved changes[/yellow]"
        if self.response_stale:
            state += " · [yellow]stale timetable[/yellow]"
        file_part = str(self.path) if self.path else "untitled"
        counts = (
            f"{len(self.data.rooms)}R · {len(self.data.classes)}C · "
            f"{len(self.data.teachers)}T · {len(self.data.subjects)}S · "
            f"{len(self.data.teacher_assignments)}A"
        )
        self.query_one("#status", Static).update(f"{file_part}  ·  {counts}  ·  {state}")

    # -- events -------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "edit-project":
            self._edit_project()
            return
        if button_id == "validate":
            self.action_validate()
            return
        if button_id == "load-sample":
            self.action_load_sample()
            return
        if button_id == "open":
            self.action_open()
            return
        if button_id == "save":
            self.action_save()
            return
        if button_id == "save-as":
            self.action_save_as()
            return
        if button_id == "new":
            self.action_new()
            return
        if button_id == "help":
            self.action_help()
            return
        if button_id == "generate":
            self.action_generate()
            return
        if button_id == "cancel-solve":
            self._cancel_solve()
            return
        if button_id == "export-csv":
            self._export_csv()
            return
        if button_id == "export-json":
            self._export_json()
            return

        for prefix, action in (
            ("add-", self._add),
            ("edit-", self._edit),
            ("dup-", self._duplicate),
            ("del-", self._delete),
        ):
            if button_id.startswith(prefix):
                action(button_id[len(prefix) :])
                return

    def on_input_changed(self, event: Input.Changed) -> None:
        input_id = event.input.id or ""
        if input_id == "tt-filter":
            self._refresh_timetable()
        elif input_id.startswith("filter-"):
            kind = input_id.replace("filter-", "")
            if kind in PLURALS:
                self._table_search[kind] = event.value.strip()
                self._refresh_entity(kind)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if (event.input.id or "").startswith("opt-"):
            self.action_generate()

    def on_select_changed(self, event: Select.Changed) -> None:
        select_id = event.select.id or ""
        if select_id == "tt-view-mode":
            self._sync_timetable_selectors()
            self._refresh_timetable()
        elif select_id == "tt-selector":
            self._refresh_timetable()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        table_id = event.data_table.id or ""
        if table_id.startswith("table-"):
            kind = table_id[len("table-") :]
            if kind in PLURALS:
                self._edit(kind)


def run_tui(file: Path | None = None) -> None:
    TimetableApp(file).run()
