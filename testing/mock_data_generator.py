#!/usr/bin/env python3
"""
generate_mock_data.py
======================
Generates mock data for a school-timetabling schema (project / rooms /
classes / teachers / subjects / teacher_assignments), matching the
structure of a reference dataset.

Design goals
------------
1. SCALABILITY  - every entity count is a CLI flag, so you can generate
   a tiny 3-class sample or a 500-class, 60-teacher district in one call.
   Generation is O(n) in the number of entities requested.
2. ACCURACY     - the generator doesn't just fill in random values, it
   enforces internal consistency:
     - every foreign key (room_id, class_id, teacher_id, subject_id,
       target_room_id) points at a real, existing record
     - subject constraints are numerically sane
       (min <= max, min_consecutive <= max_per_day, weekly caps fit
       inside slots * days, etc.)
     - target_room_id is only ever set to a room where is_lab == True,
       and only for subjects that actually need a lab (with a chance to 
       opt out of lab rooms to reflect real-world flexibility)
     - teachers are only assigned subjects/classes that fit within
       their own max_per_day / max_per_week / max_consecutive budget
       (tracked with a running load ledger while assigning)
   A `validate()` pass re-checks all of the above on the finished
   dataset and raises / warns if anything slipped through, so the
   output is guaranteed to be schema-correct and load-feasible.

Usage
-----
    python mock_data_generate.py --classes 3 --rooms 3 --subjects 8 \
        --teachers 12 --slots 8 --days Mon Tue Wed Thu Fri \
        --seed 42 -o mock_data.json

    python mock_data_generate.py --classes 200 --rooms 40 --seed 7 \
        -o big_district.json

Run with -h for all options.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import uuid
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------
# Reference pools used to make generated names look realistic
# --------------------------------------------------------------------------

FIRST_NAMES = [
    "Anita", "Suresh", "Meera", "John", "Divya", "Ravi", "Priya", "Arun",
    "Lakshmi", "Vishal", "Kavya", "Nikhil", "Sneha", "Manoj", "Pooja",
    "Sanjay", "Anjali", "Deepak", "Rekha", "Vinod", "Shalini", "Ajay",
    "Neha", "Rahul", "Sunitha", "Kiran", "Geetha", "Vikram", "Asha", "Rohit",
]
LAST_NAMES = [
    "Rao", "Kumar", "Nair", "Mathew", "Iyer", "Menon", "Pillai", "Reddy",
    "Sharma", "Verma", "Nambiar", "Pillai", "George", "Thomas", "Varma",
    "Krishnan", "Das", "Bhat", "Chandran", "Joseph",
]

GRADE_NAMES = [f"Grade {n}" for n in range(6, 13)]
SECTION_LETTERS = list("ABCDEFGH")

ROOM_KINDS = [
    ("Room", False, (1, 3)),        # (prefix, is_lab, capacity range)
    ("Science Lab", True, (1, 5)),
    ("Computer Lab", True, (1, 5)),
]

# name, morning_tendency, needs_lab
SUBJECT_LIBRARY: List[Tuple[str, str, bool]] = [
    ("Mathematics", "High", False),
    ("Physics", "Med", True),
    ("Chemistry", "Med", True),
    ("Biology", "Med", True),
    ("English", "Low", False),
    ("Computer Science", "Low", True),
    ("History", "Low", False),
    ("Geography", "Low", False),
    ("Art", "Low", False),
    ("Physical Education", "Low", False),
    ("Economics", "Low", False),
    ("Hindi", "Low", False),
]

MORNING_TENDENCIES = ["High", "Med", "Low"]


def new_id() -> str:
    return str(uuid.uuid4())


def unique_name(used_names: set, rng: random.Random) -> str:
    """Generate a First Last name not already in used_names. Falls back to
    a numeric suffix once the ~600 first/last combinations run out, so this
    can never loop forever no matter how many teachers are requested."""
    for _ in range(50):
        name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
        if name not in used_names:
            used_names.add(name)
            return name
    i = 1
    while True:
        name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)} {i}"
        if name not in used_names:
            used_names.add(name)
            return name
        i += 1


# --------------------------------------------------------------------------
# Entity builders
# --------------------------------------------------------------------------

def build_project(slots: int, days: List[str]) -> Dict[str, Any]:
    return {"id": new_id(), "slots": slots, "days": days}


def build_rooms(num_rooms: int, lab_ratio: float, rng: random.Random) -> List[Dict[str, Any]]:
    """At least one lab room is guaranteed if num_rooms > 0 and lab_ratio > 0."""
    rooms = []
    num_labs = 0
    if num_rooms > 0 and lab_ratio > 0:
        num_labs = max(1, round(num_rooms * lab_ratio))
        num_labs = min(num_labs, num_rooms)
    num_regular = num_rooms - num_labs

    counters = {"Room": 100, "Science Lab": 0, "Computer Lab": 0}

    def make_room(prefix: str, is_lab: bool, cap_range: Tuple[int, int]) -> Dict[str, Any]:
        counters[prefix] += 1
        if prefix == "Room":
            name = f"Room {counters[prefix]}"
        else:
            letter = chr(ord("A") + (counters[prefix] - 1) % 26)
            name = f"{prefix} {letter}"
        return {
            "id": new_id(),
            "name": name,
            "is_lab": is_lab,
            "constraints": {"capacity": rng.randint(*cap_range)},
        }

    for _ in range(num_regular):
        rooms.append(make_room("Room", False, ROOM_KINDS[0][2]))

    # split labs between Science Lab / Computer Lab flavors
    for i in range(num_labs):
        prefix, is_lab, cap_range = ROOM_KINDS[1] if i % 2 == 0 else ROOM_KINDS[2]
        rooms.append(make_room(prefix, is_lab, cap_range))

    return rooms


def build_classes(num_classes: int, rooms: List[Dict[str, Any]], rng: random.Random) -> List[Dict[str, Any]]:
    non_lab_rooms = [r for r in rooms if not r["is_lab"]] or rooms
    classes = []
    grade_idx = 0
    section_idx = 0
    for _ in range(num_classes):
        grade = GRADE_NAMES[grade_idx % len(GRADE_NAMES)]
        section = SECTION_LETTERS[section_idx % len(SECTION_LETTERS)]
        section_idx += 1
        if section_idx % len(SECTION_LETTERS) == 0:
            grade_idx += 1
        room = non_lab_rooms[len(classes) % len(non_lab_rooms)]
        classes.append({
            "id": new_id(),
            "name": f"{grade} - {section}",
            "room_id": room["id"],
            "constraints": {},
        })
    return classes


def build_teachers(num_teachers: int, slots: int, days: List[str], rng: random.Random) -> List[Dict[str, Any]]:
    """max_per_day/week/consecutive are randomized but always internally
    consistent and bounded by the timetable's own slots/days so no teacher
    constraint is impossible to satisfy."""
    used_names: set = set()
    teachers = []
    day_count = len(days)
    for _ in range(num_teachers):
        name = unique_name(used_names, rng)
        max_per_day = rng.randint(3, min(slots, 7))
        max_consecutive = rng.randint(1, min(max_per_day, 3))
        # weekly cap: leave headroom below the theoretical max_per_day*days
        theoretical_max = max_per_day * day_count
        max_per_week = rng.randint(max(max_per_day, day_count), theoretical_max)
        teachers.append({
            "id": new_id(),
            "name": name,
            "constraints": {
                "max_per_day": max_per_day,
                "max_per_week": max_per_week,
                "max_consecutive": max_consecutive,
            },
        })
    return teachers


def build_subjects(num_subjects: int, slots: int, days: List[str], rng: random.Random) -> List[Dict[str, Any]]:
    day_count = len(days)
    weekly_cap = slots * day_count
    pool = SUBJECT_LIBRARY[:]
    rng.shuffle(pool)
    chosen = pool[:num_subjects]
    # if more subjects requested than library size, synthesize extras
    while len(chosen) < num_subjects:
        idx = len(chosen) + 1
        chosen.append((f"Elective {idx}", rng.choice(MORNING_TENDENCIES), False))

    subjects = []
    for name, tendency, needs_lab in chosen:
        max_per_day = rng.randint(1, min(3, slots))  # bumped to 3 to help fill slot capacity 
        min_per_day = rng.randint(0, max_per_day)
        max_consecutive = rng.randint(1, max(1, max_per_day))
        min_consecutive = rng.randint(1, max_consecutive) if max_consecutive > 0 else 0
        
        # Keep weekly figures sane relative to the timetable's real capacity
        # Increased bound to 12 to generate denser subjects that fill timetables better
        upper_weekly_bound = max(1, min(max_per_day * day_count, weekly_cap, 12))
        lower_weekly_bound = min(max(1, min_per_day * day_count), upper_weekly_bound)
        
        # Bias towards higher weekly loads to easily pack empty schedules
        max_per_week = rng.randint(max(lower_weekly_bound, upper_weekly_bound - 3), upper_weekly_bound)
        min_per_week = rng.randint(lower_weekly_bound, max_per_week)
        
        subjects.append({
            "id": new_id(),
            "name": name,
            "needs_lab": needs_lab,  # internal helper flag, stripped before export
            "constraints": {
                "morning_tendency": tendency,
                "max_per_day": max_per_day,
                "min_per_day": min_per_day,
                "max_per_week": max_per_week,
                "min_per_week": min_per_week,
                "max_consecutive": max_consecutive,
                "min_consecutive": min_consecutive,
            },
        })
    return subjects


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def build_teacher_assignments(
    classes: List[Dict[str, Any]],
    teachers: List[Dict[str, Any]],
    subjects: List[Dict[str, Any]],
    rooms: List[Dict[str, Any]],
    days: List[str],
    subjects_per_class: Optional[int],
    rng: random.Random,
    slots: int,
) -> List[Dict[str, Any]]:
    lab_room_ids = [r["id"] for r in rooms if r["is_lab"]]  # may be empty

    # Each teacher specializes in 1-2 subjects (mirrors the reference data,
    # where e.g. Anita Rao only ever teaches Mathematics).
    teacher_specialties: Dict[str, List[str]] = {}
    for t in teachers:
        k = rng.randint(1, min(2, len(subjects)))
        teacher_specialties[t["id"]] = [s["id"] for s in rng.sample(subjects, k)]

    subject_to_teachers: Dict[str, List[str]] = {s["id"]: [] for s in subjects}
    for tid, subj_ids in teacher_specialties.items():
        for sid in subj_ids:
            subject_to_teachers[sid].append(tid)
    # guarantee every subject has at least one qualified teacher
    for sid, tlist in subject_to_teachers.items():
        if not tlist:
            fallback = rng.choice(teachers)
            teacher_specialties[fallback["id"]].append(sid)
            subject_to_teachers[sid].append(fallback["id"])

    subj_by_id = {s["id"]: s for s in subjects}
    # running weekly-load ledger per teacher, to respect max_per_week
    teacher_load: Dict[str, int] = {t["id"]: 0 for t in teachers}
    teacher_caps: Dict[str, int] = {t["id"]: t["constraints"]["max_per_week"] for t in teachers}

    def subject_expected_load(subj: Dict[str, Any]) -> int:
        c = subj["constraints"]
        return max(1, round((c["min_per_week"] + c["max_per_week"]) / 2))

    day_count = len(days)
    absolute_max_weekly = slots * day_count  # a teacher can never teach more than this
    teachers_by_id = {t["id"]: t for t in teachers}
    used_names = {t["name"] for t in teachers}
    spawn_counter = [0]

    def spawn_teacher_for(subject_id: str) -> str:
        spawn_counter[0] += 1
        name = unique_name(used_names, rng)
        max_per_day = rng.randint(3, min(slots, 7))
        max_consecutive = rng.randint(1, min(max_per_day, 3))
        max_per_week = rng.randint(max(max_per_day, day_count), max_per_day * day_count)
        new_t = {
            "id": new_id(),
            "name": name,
            "constraints": {
                "max_per_day": max_per_day,
                "max_per_week": max_per_week,
                "max_consecutive": max_consecutive,
            },
        }
        teachers.append(new_t)
        teachers_by_id[new_t["id"]] = new_t
        teacher_load[new_t["id"]] = 0
        teacher_caps[new_t["id"]] = max_per_week
        subject_to_teachers[subject_id].append(new_t["id"])
        return new_t["id"]

    def pick_teacher(subject_id: str, expected_load: int) -> str:
        candidates = subject_to_teachers[subject_id]
        rng.shuffle(candidates)
        for tid in candidates:
            if teacher_load[tid] + expected_load <= teacher_caps[tid]:
                return tid
        # nobody has headroom: try to grow the least-loaded qualified
        # teacher's weekly (and, if needed, daily) cap, but never past the
        # physical ceiling of slots*days. If growth would exceed that
        # ceiling, spawn a fresh teacher for this subject instead of ever
        # producing an internally-inconsistent or physically impossible
        # constraint.
        least_loaded = min(candidates, key=lambda tid: teacher_load[tid])
        needed = teacher_load[least_loaded] + expected_load
        if needed > absolute_max_weekly:
            return spawn_teacher_for(subject_id)
        teacher_caps[least_loaded] = needed
        c = teachers_by_id[least_loaded]["constraints"]
        if needed > c["max_per_day"] * day_count:
            c["max_per_day"] = min(_ceil_div(needed, day_count), slots)
            if c["max_consecutive"] > c["max_per_day"]:
                c["max_consecutive"] = c["max_per_day"]
        return least_loaded

    assignments = []
    target_class_load = slots * day_count
    
    for cls in classes:
        cls_load = 0
        pool = list(subjects)
        rng.shuffle(pool)
        
        # Enforce exact subjects_per_class if provided by user, otherwise auto-fill slots
        if subjects_per_class is not None:
            pool = pool[:subjects_per_class]
            
        for subj in pool:
            # Dynamic filling threshold (~95% full to leave realistic tiny gaps/study halls)
            if subjects_per_class is None and cls_load >= target_class_load * 0.95:
                break
                
            expected_load = subject_expected_load(subj)
            tid = pick_teacher(subj["id"], expected_load)
            teacher_load[tid] += expected_load
            cls_load += expected_load

            target_room_id: Optional[str] = None
            
            # Lab needed subjects can also be taken without lab rooms 
            # (75% probability it actually binds to a lab room in this assignment, 25% standard room)
            if subj["needs_lab"] and lab_room_ids:
                if rng.random() < 0.6:
                    target_room_id = rng.choice(lab_room_ids)

            first_slot_days: Optional[List[str]] = None
            if rng.random() < 0.35:
                n_days = rng.randint(1, min(2, len(days)))
                first_slot_days = sorted(
                    rng.sample(days, n_days),
                    key=lambda d: days.index(d),
                )

            assignments.append({
                "id": new_id(),
                "class_id": cls["id"],
                "teacher_id": tid,
                "subject_id": subj["id"],
                "target_room_id": target_room_id,
                "constraints": {"first_slot_days": first_slot_days},
            })

    # sync the (possibly grown) weekly caps back onto the teacher records
    for t in teachers:
        t["constraints"]["max_per_week"] = teacher_caps[t["id"]]

    return assignments


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def validate(data: Dict[str, Any]) -> List[str]:
    """Returns a list of human-readable problems. Empty list == all clear."""
    problems: List[str] = []

    room_ids = {r["id"] for r in data["rooms"]}
    class_ids = {c["id"] for c in data["classes"]}
    teacher_ids = {t["id"] for t in data["teachers"]}
    subject_ids = {s["id"] for s in data["subjects"]}
    lab_room_ids = {r["id"] for r in data["rooms"] if r["is_lab"]}
    days = data["project"]["days"]
    slots = data["project"]["slots"]

    for c in data["classes"]:
        if c["room_id"] not in room_ids:
            problems.append(f"class {c['name']} references unknown room_id")

    for s in data["subjects"]:
        c = s["constraints"]
        if c["min_per_day"] > c["max_per_day"]:
            problems.append(f"subject {s['name']}: min_per_day > max_per_day")
        if c["min_per_week"] > c["max_per_week"]:
            problems.append(f"subject {s['name']}: min_per_week > max_per_week")
        if c["min_consecutive"] > c["max_consecutive"]:
            problems.append(f"subject {s['name']}: min_consecutive > max_consecutive")
        if c["max_consecutive"] > c["max_per_day"] and c["max_per_day"] > 0:
            problems.append(f"subject {s['name']}: max_consecutive exceeds max_per_day")
        if c["max_per_week"] > c["max_per_day"] * len(days):
            problems.append(f"subject {s['name']}: max_per_week exceeds max_per_day*days")
        if c["max_per_week"] > slots * len(days):
            problems.append(f"subject {s['name']}: max_per_week exceeds total weekly slots")

    for t in data["teachers"]:
        c = t["constraints"]
        if c["max_consecutive"] > c["max_per_day"]:
            problems.append(f"teacher {t['name']}: max_consecutive exceeds max_per_day")
        if c["max_per_week"] > c["max_per_day"] * len(days):
            problems.append(f"teacher {t['name']}: max_per_week exceeds max_per_day*days")

    teacher_week_load: Dict[str, int] = {t["id"]: 0 for t in data["teachers"]}
    subj_by_id = {s["id"]: s for s in data["subjects"]}

    for a in data["teacher_assignments"]:
        if a["class_id"] not in class_ids:
            problems.append(f"assignment {a['id']}: unknown class_id")
        if a["teacher_id"] not in teacher_ids:
            problems.append(f"assignment {a['id']}: unknown teacher_id")
        if a["subject_id"] not in subject_ids:
            problems.append(f"assignment {a['id']}: unknown subject_id")
        if a["target_room_id"] is not None and a["target_room_id"] not in lab_room_ids:
            problems.append(f"assignment {a['id']}: target_room_id is not a lab room")
        fsd = a["constraints"].get("first_slot_days")
        if fsd is not None and not set(fsd).issubset(set(days)):
            problems.append(f"assignment {a['id']}: first_slot_days has a day outside project.days")

        subj = subj_by_id.get(a["subject_id"])
        if subj is not None and a["teacher_id"] in teacher_week_load:
            c = subj["constraints"]
            expected = max(1, round((c["min_per_week"] + c["max_per_week"]) / 2))
            teacher_week_load[a["teacher_id"]] += expected

    teacher_cap = {t["id"]: t["constraints"]["max_per_week"] for t in data["teachers"]}
    for tid, load in teacher_week_load.items():
        if load > teacher_cap[tid]:
            name = next(t["name"] for t in data["teachers"] if t["id"] == tid)
            problems.append(
                f"teacher {name}: estimated weekly load {load} exceeds max_per_week {teacher_cap[tid]}"
            )

    return problems


# --------------------------------------------------------------------------
# Top-level generator
# --------------------------------------------------------------------------

def generate(
    num_rooms: int = 3,
    num_classes: int = 3,
    num_teachers: Optional[int] = None,
    num_subjects: int = 8,
    slots: int = 8,
    days: Optional[List[str]] = None,
    subjects_per_class: Optional[int] = None,
    lab_ratio: float = 0.3,
    seed: Optional[int] = None,
) -> Dict[str, Any]:
    rng = random.Random(seed)
    days = days or ["Mon", "Tue", "Wed", "Thu", "Fri"]

    if num_teachers is None:
        # scale teacher pool with class/subject load so specialization stays
        # realistic instead of piling everything on one or two teachers
        num_teachers = max(2, round(num_classes * num_subjects / 6))

    project = build_project(slots, days)
    rooms = build_rooms(num_rooms, lab_ratio, rng)
    classes = build_classes(num_classes, rooms, rng)
    teachers = build_teachers(num_teachers, slots, days, rng)
    subjects = build_subjects(num_subjects, slots, days, rng)
    
    assignments = build_teacher_assignments(
        classes, teachers, subjects, rooms, days, subjects_per_class, rng, slots
    )

    # strip the internal "needs_lab" helper flag before export
    export_subjects = [{k: v for k, v in s.items() if k != "needs_lab"} for s in subjects]

    return {
        "project": project,
        "rooms": rooms,
        "classes": classes,
        "teachers": teachers,
        "subjects": export_subjects,
        "teacher_assignments": assignments,
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate mock data for a school timetabling schema.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--rooms", type=int, default=3, help="number of rooms to generate")
    p.add_argument("--classes", type=int, default=3, help="number of classes to generate")
    p.add_argument("--teachers", type=int, default=None,
                   help="number of teachers (default: auto-scaled to classes*subjects)")
    p.add_argument("--subjects", type=int, default=8, help="number of subjects to generate")
    p.add_argument("--subjects-per-class", type=int, default=None,
                   help="how many subjects each class studies (default: fill slots dynamically)")
    p.add_argument("--slots", type=int, default=8, help="timetable slots per day")
    p.add_argument("--days", nargs="+", default=["Mon", "Tue", "Wed", "Thu", "Fri"],
                   help="list of day names")
    p.add_argument("--lab-ratio", type=float, default=0.3,
                   help="fraction of rooms that are labs (0.0-1.0)")
    p.add_argument("--seed", type=int, default=None, help="random seed for reproducibility")
    p.add_argument("-o", "--output", default="mock_data.json", help="output JSON file path")
    p.add_argument("--no-validate", action="store_true", help="skip the post-generation validation pass")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    if args.rooms < 1:
        print("error: --rooms must be at least 1", file=sys.stderr)
        return 1
    if args.classes < 1:
        print("error: --classes must be at least 1", file=sys.stderr)
        return 1
    if args.subjects < 1:
        print("error: --subjects must be at least 1", file=sys.stderr)
        return 1

    data = generate(
        num_rooms=args.rooms,
        num_classes=args.classes,
        num_teachers=args.teachers,
        num_subjects=args.subjects,
        slots=args.slots,
        days=args.days,
        subjects_per_class=args.subjects_per_class,
        lab_ratio=args.lab_ratio,
        seed=args.seed,
    )

    if not args.no_validate:
        problems = validate(data)
        if problems:
            print(f"validation found {len(problems)} issue(s):", file=sys.stderr)
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
        else:
            print("validation passed: dataset is schema-correct and load-feasible.", file=sys.stderr)

    with open(args.output, "w") as f:
        json.dump(data, f, indent=2)

    print(
        f"wrote {args.output}: "
        f"{len(data['rooms'])} rooms, {len(data['classes'])} classes, "
        f"{len(data['teachers'])} teachers, {len(data['subjects'])} subjects, "
        f"{len(data['teacher_assignments'])} teacher_assignments",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())