"""MakeTable: school timetable generation with OR-Tools CP-SAT.

Embed it in another Python application:

    from maketable import TimeTableGenerationInput, generate

    data = TimeTableGenerationInput.model_validate_json(open("input.json").read())
    response = generate(data, time_limit_sec=60)

Or use the CLI / TUI:

    timetable generate input.json
    timetable tui input.json
"""

from __future__ import annotations

from .models import (
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
    ViolationOut,
    WeekDay,
    format_day_slots,
    parse_day_slots,
)
from .solver import TimeTableGenerator

__version__ = "0.1.0"

__all__ = [
    "ClassConstraints",
    "ClassInput",
    "DaySlot",
    "GeneratedResponse",
    "Level",
    "ProjectInput",
    "RoomConstraints",
    "RoomInput",
    "SubjectConstraints",
    "SubjectInput",
    "TeacherAssignmentConstraint",
    "TeacherAssignmentInput",
    "TeacherConstraints",
    "TeacherInput",
    "TimeTableEntryOutput",
    "TimeTableGenerationInput",
    "TimeTableGenerator",
    "ViolationOut",
    "WeekDay",
    "format_day_slots",
    "generate",
    "parse_day_slots",
    "validate_project",
]


def validate_project(
    data: TimeTableGenerationInput | dict | str | Path,
) -> list[str]:
    """Validate a project and return a list of error messages (empty if valid).

    Accepts a TimeTableGenerationInput instance, a dictionary, a JSON string,
    or a Path to a JSON file.
    """
    from pathlib import Path
    from pydantic import ValidationError

    try:
        if isinstance(data, Path):
            text = data.read_text(encoding="utf-8")
            TimeTableGenerationInput.model_validate_json(text)
        elif isinstance(data, str):
            TimeTableGenerationInput.model_validate_json(data)
        elif isinstance(data, dict):
            TimeTableGenerationInput.model_validate(data)
        elif isinstance(data, TimeTableGenerationInput):
            TimeTableGenerationInput.model_validate(data.model_dump())
        else:
            return [f"Unsupported input type for validation: {type(data)}"]
        return []
    except ValidationError as err:
        messages = []
        for e in err.errors():
            loc = " -> ".join(str(p) for p in e["loc"])
            messages.append(f"{loc}: {e['msg']}" if loc else e["msg"])
        return messages
    except Exception as err:
        return [str(err)]


def generate(
    data: TimeTableGenerationInput,
    *,
    time_limit_sec: float = 60.0,
    seed: int = 42,
    workers: int = 8,
    hint: GeneratedResponse | None = None,
    fast: bool = False,
    gap_limit: float | None = None,
    patience: float = 5.0,
) -> GeneratedResponse:
    """Validate and solve a timetable in one call."""
    data = TimeTableGenerationInput.model_validate(data.model_dump())
    return TimeTableGenerator(RawData=data, HintData=hint).solve(
        time_limit_sec=time_limit_sec,
        seed=seed,
        workers=workers,
        fast=fast,
        gap_limit=gap_limit,
        patience=patience,
    )
