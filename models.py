from pydantic import BaseModel, UUID4
from typing import List, Optional
from enum import Enum

class WeekDay(Enum, str):
    Mon = "Mon"
    Tue = "Tue"
    Wed = "Wed"
    Thu = "Thu"
    Fri = "Fri"
    Sat = "Sat"
    Sun = "Sun"

class Level(Enum, str):
    Low = "Low"
    Med = "Med"
    High = "High"

class RoomConstraints(BaseModel):
    capacity: int

class ClassConstraints(BaseModel):
    pass

class TeacherConstraints(BaseModel):
    max_per_day: int
    max_per_week: int
    max_consecutive: int

class SubjectConstraints(BaseModel):
    morning_tendency: Level
    max_per_day: int
    min_per_day: int
    max_per_week: int
    min_per_week: int
    max_consecutive: int
    min_consecutive: int

class TeacherAssignmentConstraint(BaseModel):
    is_class_teacher: bool
    first_slot_days: Optional[List[WeekDay]]

class ProjectInput(BaseModel):
    id: UUID4
    slots: int
    days: List[WeekDay]

class RoomInput(BaseModel):
    id: UUID4
    name: str
    is_lab: bool
    constraints: RoomConstraints

class ClassInput(BaseModel):
    id: UUID4
    name: str
    room_id: UUID4
    constraints: ClassConstraints
    

class TeacherInput(BaseModel):
    id: UUID4
    name: str
    constraints: TeacherConstraints


class SubjectInput(BaseModel):
    id: UUID4
    name: str
    constraints: SubjectConstraints


class TeacherAssignmentInput(BaseModel):
    id: UUID4
    class_id: UUID4
    teacher_id: UUID4
    subject_id: UUID4
    target_room_id: Optional[UUID4]
    constraints: TeacherAssignmentConstraint


class TimeTableGenerationInput(BaseModel):
    project: ProjectInput
    rooms: List[RoomInput]
    classes: List[ClassInput]
    teachers: List[TeacherInput]
    subjects: List[SubjectInput]
    teacher_assignments: List[TeacherAssignmentInput]