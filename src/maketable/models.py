from __future__ import annotations

import collections
import csv
import io
from enum import Enum
from typing import Dict, List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


# Upper bound for a single day; keeps generated CP-SAT models and parsed
# day-slot ranges bounded. The TUI already enforces the same limit.
MAX_SLOTS_PER_DAY = 24


class WeekDay(str, Enum):
    Mon = "Mon"
    Tue = "Tue"
    Wed = "Wed"
    Thu = "Thu"
    Fri = "Fri"
    Sat = "Sat"
    Sun = "Sun"


class Level(str, Enum):
    Low = "Low"
    Med = "Med"
    High = "High"


class RoomConstraints(BaseModel):
    # How many classes may use this room at the same time.
    capacity: int = Field(default=1, ge=1)


class ClassConstraints(BaseModel):
    pass


class DaySlot(BaseModel):
    day: WeekDay
    slot: int = Field(ge=1)

    def __str__(self) -> str:
        return f"{self.day.value}:{self.slot}"


def parse_day_slots(text: str) -> List[DaySlot]:
    """Parse string representations like 'Mon:1, Wed:4, Fri:2-3' into DaySlot items."""
    results: List[DaySlot] = []
    if not text or not text.strip():
        return results
    for chunk in text.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            raise ValueError(f"Invalid slot format '{chunk}', expected 'Day:Slot' (e.g. 'Mon:1')")
        day_str, slot_part = chunk.split(":", 1)
        day_str = day_str.strip()
        matching_day = next((d for d in WeekDay if d.value.casefold() == day_str.casefold()), None)
        if matching_day is None:
            raise ValueError(f"Unknown weekday '{day_str}' in '{chunk}'")

        slot_part = slot_part.strip()
        if "-" in slot_part:
            start_s, end_s = slot_part.split("-", 1)
            start_i, end_i = int(start_s), int(end_s)
            if start_i < 1 or end_i < start_i or end_i > MAX_SLOTS_PER_DAY:
                raise ValueError(
                    f"Slot range in '{chunk}' must be within 1..{MAX_SLOTS_PER_DAY}"
                )
            for s in range(start_i, end_i + 1):
                results.append(DaySlot(day=matching_day, slot=s))
        else:
            slot_number = int(slot_part)
            if not 1 <= slot_number <= MAX_SLOTS_PER_DAY:
                raise ValueError(
                    f"Slot in '{chunk}' must be within 1..{MAX_SLOTS_PER_DAY}"
                )
            results.append(DaySlot(day=matching_day, slot=slot_number))
    return results


def format_day_slots(slots: List[DaySlot]) -> str:
    """Format list of DaySlot into readable string like 'Mon:1, Wed:4'."""
    return ", ".join(f"{s.day.value}:{s.slot}" for s in slots)


def valid_block_starts(
    slots: int, break_slots: List[int], period_length: int
) -> List[int]:
    """Return zero-based start slots where a block of ``period_length``
    consecutive working slots fits inside the day without crossing a break.

    Shared by the model validator and the solver so validation and generation
    agree on which atomic blocks are schedulable.
    """
    if period_length < 1:
        return []
    break_indices = {s - 1 for s in break_slots}
    return [
        start
        for start in range(slots - period_length + 1)
        if all(
            start + offset not in break_indices
            for offset in range(period_length)
        )
    ]


_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@")


def csv_safe(value: object) -> str:
    """Neutralize spreadsheet formula injection in exported text cells."""
    text = "" if value is None else str(value)
    if text.startswith(_CSV_FORMULA_PREFIXES):
        return "'" + text
    return text


class TeacherConstraints(BaseModel):
    max_per_day: int = Field(ge=1)
    max_per_week: int = Field(ge=1)
    max_consecutive: int = Field(ge=1)
    unavailable_slots: List[DaySlot] = Field(default_factory=list)
    avoid_slots: List[DaySlot] = Field(default_factory=list)
    max_days_per_week: Optional[int] = Field(default=None, ge=1)


class SubjectConstraints(BaseModel):
    morning_tendency: Level = Level.Low
    max_per_day: int = Field(default=2, ge=1)
    min_per_day: int = Field(default=0, ge=0)
    max_per_week: int = Field(default=5, ge=1)
    min_per_week: int = Field(default=0, ge=0)
    max_consecutive: int = Field(default=2, ge=1)
    min_consecutive: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _check_ranges(self) -> "SubjectConstraints":
        for minimum, maximum, label in (
            (self.min_per_day, self.max_per_day, "per day"),
            (self.min_per_week, self.max_per_week, "per week"),
            (self.min_consecutive, self.max_consecutive, "consecutive"),
        ):
            if minimum > maximum:
                raise ValueError(f"min {label} cannot be greater than max {label}")
        return self


class TeacherAssignmentConstraint(BaseModel):
    first_slot_days: Optional[List[WeekDay]] = None
    period_length: int = Field(default=1, ge=1)
    co_schedule_with: List[UUID] = Field(default_factory=list)

    @field_validator("first_slot_days")
    @classmethod
    def _no_duplicate_first_slot_days(
        cls, value: Optional[List[WeekDay]]
    ) -> Optional[List[WeekDay]]:
        if value is not None and len(set(value)) != len(value):
            raise ValueError("first_slot_days cannot contain duplicate days")
        return value

    @field_validator("co_schedule_with")
    @classmethod
    def _no_duplicate_co_schedule(
        cls, value: List[UUID]
    ) -> List[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("co_schedule_with cannot contain duplicates")
        return value


class ProjectInput(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str = "Untitled project"
    slots: int = Field(default=8, ge=1, le=MAX_SLOTS_PER_DAY)
    days: List[WeekDay] = Field(
        default_factory=lambda: [
            WeekDay.Mon,
            WeekDay.Tue,
            WeekDay.Wed,
            WeekDay.Thu,
            WeekDay.Fri,
        ]
    )
    break_slots: List[int] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name cannot be blank")
        return value

    @field_validator("days")
    @classmethod
    def _days_unique(cls, value: List[WeekDay]) -> List[WeekDay]:
        if not value:
            raise ValueError("at least one day is required")
        if len(set(value)) != len(value):
            raise ValueError("days cannot contain duplicates")
        return value

    @field_validator("break_slots")
    @classmethod
    def _break_slots_valid(cls, value: List[int]) -> List[int]:
        if len(set(value)) != len(value):
            raise ValueError("break_slots cannot contain duplicates")
        for s in value:
            if s < 1:
                raise ValueError("break_slots indices must be >= 1")
        return sorted(value)


class RoomInput(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str
    is_lab: bool = False
    constraints: RoomConstraints = Field(default_factory=RoomConstraints)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name cannot be blank")
        return value


class ClassInput(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str
    room_id: UUID
    constraints: ClassConstraints = Field(default_factory=ClassConstraints)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name cannot be blank")
        return value


class TeacherInput(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str
    constraints: TeacherConstraints

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name cannot be blank")
        return value


class SubjectInput(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str
    constraints: SubjectConstraints

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name cannot be blank")
        return value


class TeacherAssignmentInput(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    class_id: UUID
    teacher_id: UUID
    subject_id: UUID
    target_room_id: Optional[UUID] = None
    tag: Optional[str] = None
    constraints: TeacherAssignmentConstraint = Field(
        default_factory=TeacherAssignmentConstraint
    )

    @field_validator("tag")
    @classmethod
    def _clean_tag(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            value = value.strip()
            return value if value else None
        return None


def _co_schedule_roots(
    assignments: List["TeacherAssignmentInput"],
) -> Dict[UUID, UUID]:
    """Map each assignment id to the representative root of its co-schedule group."""
    parent = {a.id: a.id for a in assignments}

    def find(u: UUID) -> UUID:
        while parent[u] != u:
            parent[u] = parent[parent[u]]
            u = parent[u]
        return u

    for a in assignments:
        ra = find(a.id)
        for other_id in a.constraints.co_schedule_with:
            if other_id in parent:
                rb = find(other_id)
                if ra != rb:
                    parent[rb] = ra

    return {a.id: find(a.id) for a in assignments}


def class_weekly_requirements(
    teacher_assignments: List["TeacherAssignmentInput"],
    subjects: List["SubjectInput"],
) -> Dict[UUID, int]:
    """Return minimum weekly slots required per class, counting co-scheduled tracks once."""
    subjects_by_id = {s.id: s for s in subjects}
    roots = _co_schedule_roots(teacher_assignments)
    class_groups: dict[UUID, dict[UUID, list[TeacherAssignmentInput]]] = collections.defaultdict(
        lambda: collections.defaultdict(list)
    )
    for assignment in teacher_assignments:
        class_groups[assignment.class_id][roots[assignment.id]].append(assignment)

    return {
        class_id: sum(
            max(
                (
                    subjects_by_id[a.subject_id].constraints.min_per_week
                    for a in group_assignments
                    if a.subject_id in subjects_by_id
                ),
                default=0,
            )
            for group_assignments in groups.values()
        )
        for class_id, groups in class_groups.items()
    }


class TimeTableGenerationInput(BaseModel):
    project: ProjectInput = Field(default_factory=ProjectInput)
    rooms: List[RoomInput] = Field(default_factory=list)
    classes: List[ClassInput] = Field(default_factory=list)
    teachers: List[TeacherInput] = Field(default_factory=list)
    subjects: List[SubjectInput] = Field(default_factory=list)
    teacher_assignments: List[TeacherAssignmentInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_references(self) -> "TimeTableGenerationInput":
        groups = {
            "room": self.rooms,
            "class": self.classes,
            "teacher": self.teachers,
            "subject": self.subjects,
            "teacher assignment": self.teacher_assignments,
        }
        ids = {}
        for label, items in groups.items():
            item_ids = [item.id for item in items]
            if len(set(item_ids)) != len(item_ids):
                raise ValueError(f"duplicate {label} ids")
            ids[label] = set(item_ids)

        for label in ("room", "class", "teacher", "subject"):
            names = [item.name.strip().casefold() for item in groups[label]]
            if len(set(names)) != len(names):
                raise ValueError(
                    f"duplicate {label} names (names must be unique case-insensitively)"
                )

        for s in self.project.break_slots:
            if s > self.project.slots:
                raise ValueError(
                    f"break slot {s} exceeds project total slots ({self.project.slots})"
                )
        if len(self.project.break_slots) >= self.project.slots:
            raise ValueError("break_slots cannot occupy all slots in a day")

        working_slots_per_day = self.project.slots - len(self.project.break_slots)
        total_slots_per_week = working_slots_per_day * len(self.project.days)

        for teacher in self.teachers:
            if teacher.constraints.max_per_day > self.project.slots:
                raise ValueError(
                    f"teacher '{teacher.name}' has max_per_day ({teacher.constraints.max_per_day}) "
                    f"exceeding project slots per day ({self.project.slots})"
                )
            if teacher.constraints.max_per_week > total_slots_per_week:
                raise ValueError(
                    f"teacher '{teacher.name}' has max_per_week ({teacher.constraints.max_per_week}) "
                    f"exceeding total weekly slots ({total_slots_per_week})"
                )
            if teacher.constraints.max_consecutive > self.project.slots:
                raise ValueError(
                    f"teacher '{teacher.name}' has max_consecutive ({teacher.constraints.max_consecutive}) "
                    f"exceeding project slots per day ({self.project.slots})"
                )
            if (
                teacher.constraints.max_days_per_week is not None
                and teacher.constraints.max_days_per_week > len(self.project.days)
            ):
                raise ValueError(
                    f"teacher '{teacher.name}' has max_days_per_week ({teacher.constraints.max_days_per_week}) "
                    f"exceeding project days count ({len(self.project.days)})"
                )

            for slot_item in teacher.constraints.unavailable_slots + teacher.constraints.avoid_slots:
                if slot_item.day not in self.project.days:
                    raise ValueError(
                        f"teacher '{teacher.name}' references day '{slot_item.day.value}', which is not a project day"
                    )
                if not 1 <= slot_item.slot <= self.project.slots:
                    raise ValueError(
                        f"teacher '{teacher.name}' references slot {slot_item.slot}, outside valid project slots 1..{self.project.slots}"
                    )

        subjects_by_id = {s.id: s for s in self.subjects}
        for subject in self.subjects:
            c = subject.constraints
            if c.max_per_day > self.project.slots:
                raise ValueError(
                    f"subject '{subject.name}' has max_per_day ({c.max_per_day}) "
                    f"exceeding project slots per day ({self.project.slots})"
                )
            if c.min_per_day > self.project.slots:
                raise ValueError(
                    f"subject '{subject.name}' has min_per_day ({c.min_per_day}) "
                    f"exceeding project slots per day ({self.project.slots})"
                )
            if c.min_per_week > total_slots_per_week:
                raise ValueError(
                    f"subject '{subject.name}' has min_per_week ({c.min_per_week}) "
                    f"exceeding total weekly slots ({total_slots_per_week})"
                )
            if c.max_consecutive > self.project.slots:
                raise ValueError(
                    f"subject '{subject.name}' has max_consecutive ({c.max_consecutive}) "
                    f"exceeding project slots per day ({self.project.slots})"
                )
            if c.min_consecutive > self.project.slots:
                raise ValueError(
                    f"subject '{subject.name}' has min_consecutive ({c.min_consecutive}) "
                    f"exceeding project slots per day ({self.project.slots})"
                )

        room_ids = ids["room"]
        classes_dict = {c.id: c for c in self.classes}
        for class_ in self.classes:
            if class_.room_id not in room_ids:
                raise ValueError(
                    f"class '{class_.name}' references an unknown room"
                )

        class_ids = ids["class"]
        teacher_ids = ids["teacher"]
        subject_ids = ids["subject"]
        assignment_ids = ids["teacher assignment"]
        assignment_by_id = {a.id: a for a in self.teacher_assignments}
        seen = set()
        project_days = set(self.project.days)
        for assignment in self.teacher_assignments:
            if assignment.class_id not in class_ids:
                raise ValueError("teacher assignment references an unknown class")
            if assignment.teacher_id not in teacher_ids:
                raise ValueError("teacher assignment references an unknown teacher")
            if assignment.subject_id not in subject_ids:
                raise ValueError("teacher assignment references an unknown subject")
            if (
                assignment.target_room_id is not None
                and assignment.target_room_id not in room_ids
            ):
                raise ValueError(
                    "teacher assignment references an unknown target room"
                )

            period_length = assignment.constraints.period_length
            if period_length > self.project.slots:
                raise ValueError(
                    f"assignment period_length ({period_length}) "
                    f"exceeds project slots ({self.project.slots})"
                )
            if not valid_block_starts(
                self.project.slots, self.project.break_slots, period_length
            ):
                raise ValueError(
                    f"assignment period_length ({period_length}) does not fit "
                    f"between the project's break slots "
                    f"({self.project.break_slots})"
                )

            for other_id in assignment.constraints.co_schedule_with:
                if other_id not in assignment_ids:
                    raise ValueError(
                        f"assignment references unknown co_schedule_with assignment id {other_id}"
                    )
                if other_id == assignment.id:
                    raise ValueError("assignment cannot co-schedule with itself")
                other_assignment = assignment_by_id[other_id]
                if (
                    other_assignment.constraints.period_length
                    != assignment.constraints.period_length
                ):
                    raise ValueError(
                        "co-scheduled assignments must have identical period_length"
                    )

            key = (
                assignment.class_id,
                assignment.teacher_id,
                assignment.subject_id,
                assignment.tag,
            )
            if key in seen:
                tag_suffix = f" with tag '{assignment.tag}'" if assignment.tag else ""
                raise ValueError(
                    f"duplicate teacher assignment (same class, teacher and subject{tag_suffix})"
                )
            seen.add(key)

            for day in assignment.constraints.first_slot_days or []:
                if day not in project_days:
                    raise ValueError(
                        f"teacher assignment uses '{day.value}', which is not a project day"
                    )
                if 1 in self.project.break_slots:
                    raise ValueError(
                        "teacher assignment requires the first slot, "
                        "but slot 1 is a project break slot"
                    )

        roots = _co_schedule_roots(self.teacher_assignments)

        # Co-scheduled classes share a slot, so every class in the group needs
        # a place in the room it uses at that moment. `capacity` counts classes,
        # and a single teacher cannot teach in two different rooms at once.
        teachers_by_id = {t.id: t for t in self.teachers}
        rooms_by_id = {r.id: r for r in self.rooms}
        group_room_classes = collections.defaultdict(
            lambda: collections.defaultdict(set)
        )
        group_teacher_rooms = collections.defaultdict(
            lambda: collections.defaultdict(set)
        )
        for a in self.teacher_assignments:
            class_ = classes_dict[a.class_id]
            room_id = (
                a.target_room_id
                if a.target_room_id is not None
                else class_.room_id
            )
            root = roots[a.id]
            group_room_classes[root][room_id].add(a.class_id)
            group_teacher_rooms[root][a.teacher_id].add(room_id)

        for rooms_used in group_room_classes.values():
            for room_id, class_ids in rooms_used.items():
                room = rooms_by_id[room_id]
                if len(class_ids) > room.constraints.capacity:
                    names = ", ".join(
                        sorted(classes_dict[cid].name for cid in class_ids)
                    )
                    raise ValueError(
                        f"co-scheduled classes ({names}) share room "
                        f"'{room.name}' with capacity {room.constraints.capacity}, "
                        f"but {len(class_ids)} classes use it at the same time"
                    )

        for teacher_rooms in group_teacher_rooms.values():
            for teacher_id, used_room_ids in teacher_rooms.items():
                if len(used_room_ids) > 1:
                    teacher = teachers_by_id[teacher_id]
                    room_names = ", ".join(
                        sorted(rooms_by_id[rid].name for rid in used_room_ids)
                    )
                    raise ValueError(
                        f"co-scheduled assignments place teacher '{teacher.name}' "
                        f"in multiple rooms ({room_names}) at the same time"
                    )

        for class_id, class_req in class_weekly_requirements(
            self.teacher_assignments, self.subjects
        ).items():
            if class_req > total_slots_per_week:
                class_name = classes_dict[class_id].name
                raise ValueError(
                    f"class '{class_name}' requires at least {class_req} subject periods per week, "
                    f"which exceeds the total available weekly slots ({total_slots_per_week})"
                )

        return self


class TimeTableEntryOutput(BaseModel):
    assignment_id: UUID
    class_name: str
    room_name: str
    subject_name: str
    teacher_name: str
    day: WeekDay
    slot: int
    period_length: int = 1
    tag: Optional[str] = None


class ViolationOut(BaseModel):
    error_msg: str
    val: int


class GeneratedResponse(BaseModel):
    entries: List[TimeTableEntryOutput]
    violations: List[ViolationOut]
    success: bool
    break_slots: List[int] = Field(default_factory=list)

    def get_classes(self) -> list[str]:
        return sorted({e.class_name for e in self.entries})

    def get_teachers(self) -> list[str]:
        return sorted({e.teacher_name for e in self.entries})

    def get_rooms(self) -> list[str]:
        return sorted({e.room_name for e in self.entries})

    def _build_grid(
        self, predicate, total_slots: int | None = None
    ) -> dict[str, dict[int, list[TimeTableEntryOutput]]]:
        day_order = [d.value for d in WeekDay]
        grid: dict[str, dict[int, list[TimeTableEntryOutput]]] = (
            collections.defaultdict(lambda: collections.defaultdict(list))
        )
        if total_slots is not None:
            slots = list(range(1, total_slots + 1))
        else:
            max_s = max((e.slot for e in self.entries), default=8)
            slots = list(range(1, max(max_s, 1) + 1))

        for entry in self.entries:
            if predicate(entry):
                grid[entry.day.value][entry.slot].append(entry)

        active_days = sorted(
            grid.keys(),
            key=lambda d: day_order.index(d) if d in day_order else 99,
        )
        for day in active_days:
            for s in slots:
                if s not in grid[day]:
                    grid[day][s] = []
        return dict(grid)

    def get_class_grid(
        self, class_name: str, total_slots: int | None = None
    ) -> dict[str, dict[int, list[TimeTableEntryOutput]]]:
        return self._build_grid(lambda e: e.class_name == class_name, total_slots)

    def get_teacher_grid(
        self, teacher_name: str, total_slots: int | None = None
    ) -> dict[str, dict[int, list[TimeTableEntryOutput]]]:
        return self._build_grid(lambda e: e.teacher_name == teacher_name, total_slots)

    def get_room_grid(
        self, room_name: str, total_slots: int | None = None
    ) -> dict[str, dict[int, list[TimeTableEntryOutput]]]:
        return self._build_grid(lambda e: e.room_name == room_name, total_slots)

    def to_csv(self) -> str:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "day",
                "slot",
                "class_name",
                "subject_name",
                "teacher_name",
                "room_name",
                "tag",
                "period_length",
                "assignment_id",
            ]
        )
        for entry in self.entries:
            writer.writerow(
                [
                    entry.day.value,
                    entry.slot,
                    csv_safe(entry.class_name),
                    csv_safe(entry.subject_name),
                    csv_safe(entry.teacher_name),
                    csv_safe(entry.room_name),
                    csv_safe(entry.tag or ""),
                    entry.period_length,
                    str(entry.assignment_id),
                ]
            )
        return output.getvalue()
