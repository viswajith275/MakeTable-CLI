#!/usr/bin/env python3
"""
mock_data_generator.py
======================
Generates realistic, internally consistent timetable datasets for functional
and pressure testing across MakeTable (schema, solver, CLI, and TUI).

Features Exercised
------------------
- Universal mid-day break slots (project.break_slots)
- Home classrooms (with capacity matching shared classes), multi-capacity lecture
  halls, and dedicated lab rooms
- Multi-slot atomic lab blocks (period_length >= 2) avoiding break boundaries
- Split-class concurrent lab batches & elective/minor tracks (tag + mutual
  co_schedule_with links across distinct rooms/teachers)
- Merged common lectures across multiple classes sharing one room & teacher
- Teacher workload caps (max_per_day, max_per_week, max_consecutive,
  max_days_per_week), hard unavailability (unavailable_slots), and soft
  preferences (avoid_slots)
- Conflict-free morning first-slot requests (first_slot_days)

Usage
-----
    python testing/mock_data_generator.py --classes 6 --preset college -o mock_data.json
    python testing/mock_data_generator.py --classes 20 --preset pressure --solve 10
"""

from __future__ import annotations

import argparse
import collections
import json
import random
import sys
import time
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

FIRST_NAMES = [
    "Anita", "Suresh", "Meera", "John", "Divya", "Ravi", "Priya", "Arun",
    "Lakshmi", "Vishal", "Kavya", "Nikhil", "Sneha", "Manoj", "Pooja",
    "Sanjay", "Anjali", "Deepak", "Rekha", "Vinod", "Shalini", "Ajay",
    "Neha", "Rahul", "Sunitha", "Kiran", "Geetha", "Vikram", "Asha", "Rohit",
]
LAST_NAMES = [
    "Rao", "Kumar", "Nair", "Mathew", "Iyer", "Menon", "Pillai", "Reddy",
    "Sharma", "Verma", "Nambiar", "George", "Thomas", "Varma", "Krishnan",
    "Das", "Bhat", "Chandran", "Joseph", "Kulkarni",
]

DEPT_PREFIXES = ["CSE", "ECE", "ME", "CE", "EEE", "AI", "IT", "BIO"]
SECTION_LETTERS = list("ABCDEFGH")

THEORY_SUBJECTS: List[Tuple[str, str]] = [
    ("Mathematics", "High"),
    ("Data Structures", "High"),
    ("Operating Systems", "Med"),
    ("Algorithms", "High"),
    ("Database Systems", "Med"),
    ("Computer Networks", "Med"),
    ("Software Engineering", "Low"),
    ("Discrete Mathematics", "High"),
    ("Digital Logic", "Med"),
    ("Computer Architecture", "Med"),
    ("Technical Communication", "Low"),
    ("Economics & Ethics", "Low"),
]

LAB_SUBJECTS: List[Tuple[str, str]] = [
    ("Programming Lab", "Low"),
    ("Systems Lab", "Low"),
    ("Networks Lab", "Low"),
    ("Hardware Lab", "Med"),
    ("Database Lab", "Low"),
    ("Simulation Lab", "Low"),
]

ELECTIVE_SUBJECTS: List[Tuple[str, str]] = [
    ("AI Elective", "Low"),
    ("Cloud Computing", "Low"),
    ("Cybersecurity", "Low"),
    ("Robotics Minor", "Low"),
    ("Data Mining", "Low"),
    ("Embedded Systems", "Low"),
]

MORNING_TENDENCIES = ["High", "Med", "Low"]


def new_id() -> str:
    return str(uuid.uuid4())


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def _valid_block_starts(slots: int, break_slots: List[int], period_length: int) -> List[int]:
    breaks = {b - 1 for b in break_slots}
    if period_length <= 1:
        return [s for s in range(slots) if s not in breaks]
    return [
        start
        for start in range(slots - period_length + 1)
        if all((start + offset) not in breaks for offset in range(period_length))
    ]


def unique_name(used_names: set, rng: random.Random) -> str:
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


def build_project(
    slots: int,
    days: List[str],
    break_slots: List[int],
    name: str = "Department Timetable",
) -> Dict[str, Any]:
    return {
        "id": new_id(),
        "name": name,
        "slots": slots,
        "days": days,
        "break_slots": break_slots,
    }


def build_rooms(
    num_rooms: int,
    num_classes: int,
    lab_ratio: float,
    include_merged_hall: bool,
    rng: random.Random,
) -> List[Dict[str, Any]]:
    """Build home classrooms, lab rooms, and optional seminar hall."""
    num_labs = 0
    if num_rooms >= 2 and lab_ratio > 0:
        min_labs = 2 if num_rooms >= 4 else 1
        num_labs = max(min_labs, min(num_rooms - 1, round(num_rooms * lab_ratio)))

    reserve_halls = 0
    if include_merged_hall and num_rooms - num_labs >= 2:
        reserve_halls = max(1, min(num_classes // 6 + 1, max(1, (num_rooms - num_labs) // 4)))
    num_regular = max(1, num_rooms - num_labs - reserve_halls)

    rooms: List[Dict[str, Any]] = []
    # Guarantee shared home rooms have enough capacity for classes sharing them.
    base_share = num_classes // num_regular
    remainder = num_classes % num_regular
    for i in range(num_regular):
        cap = max(1, base_share + (1 if i < remainder else 0))
        rooms.append({
            "id": new_id(),
            "name": f"Room {101 + i}",
            "is_lab": False,
            "constraints": {"capacity": cap},
        })

    for h in range(reserve_halls):
        suffix = chr(ord("A") + h % 26)
        rooms.append({
            "id": new_id(),
            "name": f"Lecture Hall {suffix}",
            "is_lab": False,
            "constraints": {"capacity": max(2, min(4, num_classes))},
        })

    for i in range(num_labs):
        prefix = "Science Lab" if i % 2 == 0 else "Computer Lab"
        suffix = chr(ord("A") + (i // 2) % 26)
        rooms.append({
            "id": new_id(),
            "name": f"{prefix} {suffix}",
            "is_lab": True,
            "constraints": {"capacity": rng.choice([1, 2]) if num_labs == 1 else 1},
        })

    return rooms


def build_classes(num_classes: int, rooms: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    home_pool = [r for r in rooms if not r["is_lab"] and not r["name"].startswith("Lecture Hall")]
    if not home_pool:
        home_pool = [r for r in rooms if not r["is_lab"]] or rooms

    classes: List[Dict[str, Any]] = []
    for idx in range(num_classes):
        dept = DEPT_PREFIXES[(idx // len(SECTION_LETTERS)) % len(DEPT_PREFIXES)]
        sec = SECTION_LETTERS[idx % len(SECTION_LETTERS)]
        cohort = idx // (len(DEPT_PREFIXES) * len(SECTION_LETTERS))
        suffix = f" {cohort + 1}" if cohort else ""
        classes.append({
            "id": new_id(),
            "name": f"{dept}-{sec}{suffix}",
            "room_id": home_pool[idx % len(home_pool)]["id"],
            "constraints": {},
        })
    return classes


def _make_teacher_record(
    name: str,
    slots: int,
    day_count: int,
    rng: random.Random,
) -> Dict[str, Any]:
    working_est = max(1, slots - 1)
    return {
        "id": new_id(),
        "name": name,
        "constraints": {
            "max_per_day": min(working_est, rng.randint(4, 6)),
            "max_per_week": working_est * day_count,
            "max_consecutive": rng.choice([2, 3]),
            "max_days_per_week": None,
            "unavailable_slots": [],
            "avoid_slots": [],
        },
    }


def build_teachers(
    num_teachers: int,
    slots: int,
    days: List[str],
    rng: random.Random,
) -> List[Dict[str, Any]]:
    used: set = set()
    return [
        _make_teacher_record(unique_name(used, rng), slots, len(days), rng)
        for _ in range(num_teachers)
    ]


def build_subjects(
    num_subjects: int,
    slots: int,
    days: List[str],
    break_slots: List[int],
    preset: str,
    rng: random.Random,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return (theory_subjects, lab_subjects, elective_subjects)."""
    day_count = len(days)
    working_slots = slots - len(break_slots)
    weekly_cap = working_slots * day_count
    allow_labs = preset != "school" and bool(_valid_block_starts(slots, break_slots, 2))

    # Theory subjects
    t_pool = THEORY_SUBJECTS[:]
    rng.shuffle(t_pool)
    while len(t_pool) < num_subjects:
        idx = len(t_pool) + 1
        t_pool.append((f"Core Subject {idx}", rng.choice(MORNING_TENDENCIES)))
    chosen_theory = t_pool[:num_subjects]

    theory_subjects: List[Dict[str, Any]] = []
    for idx, (name, tendency) in enumerate(chosen_theory):
        max_per_day = min(working_slots, 2 if idx < 4 else 1)
        min_per_week = min(weekly_cap, rng.choice([3, 4]) if idx < 3 else rng.choice([2, 3]))
        max_per_week = min(weekly_cap, max_per_day * day_count, min_per_week + rng.choice([0, 1]))
        min_per_day = 1 if (preset == "pressure" and idx == 0 and min_per_week >= day_count) else 0
        theory_subjects.append({
            "id": new_id(),
            "name": name,
            "constraints": {
                "morning_tendency": tendency,
                "max_per_day": max_per_day,
                "min_per_day": min_per_day,
                "max_per_week": max_per_week,
                "min_per_week": min_per_week,
                "max_consecutive": min(max_per_day, 2),
                "min_consecutive": 1,
            },
        })

    # Lab subjects (2h atomic blocks)
    lab_subjects: List[Dict[str, Any]] = []
    if allow_labs:
        for name, tendency in LAB_SUBJECTS[: max(2, min(len(LAB_SUBJECTS), 4))]:
            lab_subjects.append({
                "id": new_id(),
                "name": name,
                "constraints": {
                    "morning_tendency": tendency,
                    "max_per_day": 2,
                    "min_per_day": 0,
                    "max_per_week": min(weekly_cap, 2),
                    "min_per_week": min(weekly_cap, 2),
                    "max_consecutive": 2,
                    "min_consecutive": 2,
                },
            })

    # Elective / track subjects (1h split concurrent sessions)
    elective_subjects: List[Dict[str, Any]] = []
    if preset in ("college", "pressure"):
        for name, tendency in ELECTIVE_SUBJECTS[:4]:
            hours = min(weekly_cap, day_count, 2 if preset == "college" else 3)
            elective_subjects.append({
                "id": new_id(),
                "name": name,
                "constraints": {
                    "morning_tendency": tendency,
                    "max_per_day": 1,
                    "min_per_day": 0,
                    "max_per_week": hours,
                    "min_per_week": hours,
                    "max_consecutive": 1,
                    "min_consecutive": 1,
                },
            })

    return theory_subjects, lab_subjects, elective_subjects


def _co_schedule_roots(assignments: List[Dict[str, Any]]) -> Dict[str, str]:
    parent = {a["id"]: a["id"] for a in assignments}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for a in assignments:
        for peer in a["constraints"].get("co_schedule_with") or []:
            if peer in parent:
                union(a["id"], peer)
    return {aid: find(aid) for aid in parent}


def class_effective_min_load(
    assignments: List[Dict[str, Any]],
    subj_by_id: Dict[str, Dict[str, Any]],
) -> Dict[str, int]:
    """Compute minimum required slots per class counting split co-groups once."""
    roots = _co_schedule_roots(assignments)
    seen: Dict[Tuple[str, str], int] = {}
    for a in assignments:
        req = subj_by_id[a["subject_id"]]["constraints"]["min_per_week"]
        key = (a["class_id"], roots[a["id"]])
        seen[key] = max(seen.get(key, 0), req)
    totals: Dict[str, int] = collections.Counter()
    for (cid, _root), val in seen.items():
        totals[cid] += val
    return dict(totals)


def _finalize_teachers(
    teachers: List[Dict[str, Any]],
    teacher_load: Dict[str, int],
    teacher_has_2h_lab: Set[str],
    first_slot_busy: Set[Tuple[str, str]],
    slots: int,
    days: List[str],
    break_slots: List[int],
    preset: str,
    rng: random.Random,
) -> None:
    """Tighten teacher limits safely after workloads are known and add realistic requests."""
    working = [s + 1 for s in range(slots) if (s + 1) not in set(break_slots)]
    day_count = len(days)
    weekly_working = len(working) * day_count

    for idx, teacher in enumerate(teachers):
        tid = teacher["id"]
        load = teacher_load.get(tid, 0)
        c = teacher["constraints"]
        min_consec = 2 if tid in teacher_has_2h_lab else 1
        max_per_day = max(c["max_per_day"], min_consec, _ceil_div(load, day_count) + 1)
        max_per_day = min(len(working), max_per_day)
        c["max_per_day"] = max_per_day
        c["max_consecutive"] = max(c["max_consecutive"], min_consec)
        if c["max_consecutive"] > max_per_day:
            c["max_consecutive"] = max_per_day

        headroom = 1 if preset == "pressure" else rng.choice([2, 3])
        c["max_per_week"] = min(weekly_working, max(load + headroom, max_per_day))
        c["max_per_week"] = min(c["max_per_week"], max_per_day * day_count)

        # Realistic day-off caps when load comfortably fits in day_count - 1 days.
        day_off_chance = 0.35 if preset == "pressure" else 0.25
        if (
            day_count >= 4
            and load > 0
            and load <= (day_count - 1) * max(1, max_per_day - 1) * 0.75
            and all((tid, d) not in first_slot_busy for d in [days[-1]])
            and rng.random() < day_off_chance
        ):
            c["max_days_per_week"] = day_count - 1
            active_days = days[:-1]
        else:
            active_days = days

        # Add non-breaking unavailable slots leaving >= load + 4 open slots.
        avail_cap = len(active_days) * len(working)
        unavail_chance = 0.45 if preset == "pressure" else (0.30 if preset == "college" else 0.20)
        chosen_unavail: Set[Tuple[str, int]] = set()
        if load > 0 and avail_cap - load >= 6 and rng.random() < unavail_chance:
            candidates = [
                (d, s)
                for d in active_days
                for s in working
                if not (s == 1 and (tid, d) in first_slot_busy)
            ]
            # Avoid taking all late/consecutive options needed by a 2h lab
            if candidates:
                count = min(2 if preset == "pressure" else 1, avail_cap - load - 5, len(candidates))
                for d, s in rng.sample(candidates, max(0, count)):
                    chosen_unavail.add((d, s))
                    c["unavailable_slots"].append({"day": d, "slot": s})

        # Soft avoid preferences
        avoid_chance = 0.50 if preset == "pressure" else 0.35
        if rng.random() < avoid_chance and idx % 2 == 0:
            avoid_candidates = [
                (d, s)
                for d in active_days
                for s in (working[0], working[-1])
                if (d, s) not in chosen_unavail and not (s == 1 and (tid, d) in first_slot_busy)
            ]
            if avoid_candidates:
                for d, s in rng.sample(avoid_candidates, min(2, len(avoid_candidates))):
                    c["avoid_slots"].append({"day": d, "slot": s})


def generate(
    num_rooms: Optional[int] = None,
    num_classes: int = 3,
    num_teachers: Optional[int] = None,
    num_subjects: int = 8,
    slots: int = 7,
    days: Optional[List[str]] = None,
    subjects_per_class: Optional[int] = None,
    lab_ratio: float = 0.3,
    seed: Optional[int] = None,
    *,
    preset: str = "college",
    break_slots: Optional[List[int]] = None,
    utilization: Optional[float] = None,
) -> Dict[str, Any]:
    """Generate a rich, schema-valid, pressure-testable timetable input dict."""
    if preset not in ("college", "pressure", "school"):
        raise ValueError(f"unknown preset {preset!r}: choose college, pressure, or school")

    rng = random.Random(seed)
    days = days or ["Mon", "Tue", "Wed", "Thu", "Fri"]
    day_count = len(days)

    if break_slots is None:
        break_slots = [slots // 2 + 1] if slots >= 6 else []
    break_slots = sorted({b for b in break_slots if 1 <= b <= slots})
    working_slots = slots - len(break_slots)
    weekly_working = working_slots * day_count

    if num_rooms is None:
        num_rooms = max(6, round(num_classes * 1.8) + 2)
    if num_teachers is None:
        num_teachers = max(6, round(num_classes * (3.4 if preset == "pressure" else 2.9)))
    if utilization is None:
        utilization = {"college": 0.85, "pressure": 0.94, "school": 0.80}[preset]

    include_merged = preset in ("college", "pressure") and num_classes >= 2
    project = build_project(slots, days, break_slots, name=f"Dept Schedule ({preset})")
    rooms = build_rooms(num_rooms, num_classes, lab_ratio, include_merged, rng)
    classes = build_classes(num_classes, rooms)
    teachers = build_teachers(num_teachers, slots, days, rng)
    theory_subjs, lab_subjs, elective_subjs = build_subjects(
        num_subjects, slots, days, break_slots, preset, rng
    )
    all_subjects = theory_subjs + lab_subjs + elective_subjs
    subj_by_id = {s["id"]: s for s in all_subjects}

    lab_rooms = [r for r in rooms if r["is_lab"]]
    hall_rooms = [r for r in rooms if not r["is_lab"] and r["constraints"]["capacity"] >= 2]
    regular_rooms = [r for r in rooms if not r["is_lab"]]

    # Assign teacher specialties so specialists cluster naturally like real departments.
    teachers_for_subject: Dict[str, List[str]] = {s["id"]: [] for s in all_subjects}
    for idx, teacher in enumerate(teachers):
        primary = all_subjects[idx % len(all_subjects)]
        teachers_for_subject[primary["id"]].append(teacher["id"])
        extras = rng.sample(all_subjects, k=min(2, len(all_subjects)))
        for s in extras:
            if teacher["id"] not in teachers_for_subject[s["id"]]:
                teachers_for_subject[s["id"]].append(teacher["id"])
    for sid, tids in teachers_for_subject.items():
        if not tids:
            teachers_for_subject[sid].append(rng.choice(teachers)["id"])

    used_names = {t["name"] for t in teachers}
    teacher_load: Dict[str, int] = collections.Counter()
    teacher_has_2h_lab: Set[str] = set()
    room_demand: Dict[str, int] = collections.Counter()
    room_caps = {r["id"]: r["constraints"]["capacity"] for r in rooms}
    home_classes: Dict[str, List[str]] = collections.defaultdict(list)
    for cls in classes:
        home_classes[cls["room_id"]].append(cls["id"])
    max_teacher_week = int(weekly_working * (0.85 if preset == "pressure" else 0.78))
    target_per_class = min(weekly_working, max(2, round(weekly_working * utilization)))
    class_load: Dict[str, int] = collections.Counter()

    def pending_home_demand(room_id: str) -> int:
        return sum(max(0, target_per_class - class_load[cid]) for cid in home_classes.get(room_id, ()))

    def room_has_headroom(room_id: str, extra: int) -> bool:
        return (
            room_demand[room_id] + pending_home_demand(room_id) + extra
            <= room_caps[room_id] * weekly_working
        )

    def pick_teacher(subject_id: str, extra: int, exclude: Set[str] = frozenset()) -> str:
        pool = [t for t in teachers_for_subject[subject_id] if t not in exclude]
        pool.sort(key=lambda tid: teacher_load[tid])
        for tid in pool:
            if teacher_load[tid] + extra <= max_teacher_week:
                return tid
        new_t = _make_teacher_record(unique_name(used_names, rng), slots, day_count, rng)
        teachers.append(new_t)
        teachers_for_subject[subject_id].append(new_t["id"])
        return new_t["id"]

    assignments: List[Dict[str, Any]] = []
    assigned_keys: Set[Tuple[str, str, str, Optional[str]]] = set()
    # Track first-slot exclusivity so multiple courses never ask for slot 1 same day.
    class_first_busy: Set[Tuple[str, str]] = set()
    teacher_first_busy: Set[Tuple[str, str]] = set()

    def add_assignment(
        class_id: str,
        teacher_id: str,
        subject_id: str,
        *,
        target_room_id: Optional[str] = None,
        tag: Optional[str] = None,
        period_length: int = 1,
        co_schedule_with: Optional[List[str]] = None,
        first_slot_days: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        rec = {
            "id": new_id(),
            "class_id": class_id,
            "teacher_id": teacher_id,
            "subject_id": subject_id,
            "target_room_id": target_room_id,
            "tag": tag,
            "constraints": {
                "period_length": period_length,
                "first_slot_days": first_slot_days,
                "co_schedule_with": co_schedule_with or [],
            },
        }
        assignments.append(rec)
        assigned_keys.add((class_id, teacher_id, subject_id, tag))
        teacher_load[teacher_id] += subj_by_id[subject_id]["constraints"]["min_per_week"]
        if period_length >= 2:
            teacher_has_2h_lab.add(teacher_id)
        return rec

    # 1. Merged lectures (pairs of classes sharing a hall & teacher at the same slot).
    if include_merged and hall_rooms:
        merged_subj = theory_subjs[-1]
        req = merged_subj["constraints"]["min_per_week"]
        for i in range(0, len(classes) - 1, 2):
            c1, c2 = classes[i], classes[i + 1]
            hall = hall_rooms[(i // 2) % len(hall_rooms)]
            if (
                class_load[c1["id"]] + req > target_per_class
                or class_load[c2["id"]] + req > target_per_class
                or not room_has_headroom(hall["id"], 2 * req)
            ):
                continue
            tid = pick_teacher(merged_subj["id"], req)
            a1 = add_assignment(c1["id"], tid, merged_subj["id"], target_room_id=hall["id"], tag="Merged")
            a2 = add_assignment(
                c2["id"],
                tid,
                merged_subj["id"],
                target_room_id=hall["id"],
                tag="Merged",
                co_schedule_with=[a1["id"]],
            )
            a1["constraints"]["co_schedule_with"] = [a2["id"]]
            # Teacher teaches both classes simultaneously (count load once).
            teacher_load[tid] -= req
            class_load[c1["id"]] += req
            class_load[c2["id"]] += req
            room_demand[hall["id"]] += 2 * req

    # 2. Split-class concurrent lab batches (2 teachers in 2 labs at identical slots).
    if lab_subjs and len(lab_rooms) >= 2:
        for idx, cls in enumerate(classes):
            if subjects_per_class is not None and subjects_per_class < 2:
                break
            s1, s2 = lab_subjs[0], lab_subjs[1 % len(lab_subjs)]
            req = max(s1["constraints"]["min_per_week"], s2["constraints"]["min_per_week"])
            if class_load[cls["id"]] + req > target_per_class:
                continue
            sorted_labs = sorted(lab_rooms, key=lambda r: room_demand[r["id"]])
            r1, r2 = sorted_labs[0], sorted_labs[1]
            if not (room_has_headroom(r1["id"], req) and room_has_headroom(r2["id"], req)):
                continue
            t1 = pick_teacher(s1["id"], req)
            t2 = pick_teacher(s2["id"], req, exclude={t1})
            b1 = add_assignment(
                cls["id"], t1, s1["id"], target_room_id=r1["id"], tag="Batch A", period_length=2
            )
            b2 = add_assignment(
                cls["id"],
                t2,
                s2["id"],
                target_room_id=r2["id"],
                tag="Batch B",
                period_length=2,
                co_schedule_with=[b1["id"]],
            )
            b1["constraints"]["co_schedule_with"] = [b2["id"]]
            class_load[cls["id"]] += req
            room_demand[r1["id"]] += req
            room_demand[r2["id"]] += req

    # 3. Split-class concurrent elective / minor tracks.
    if elective_subjs and len(regular_rooms) >= 2 and preset in ("college", "pressure"):
        for idx, cls in enumerate(classes):
            if idx % 2 != 0 and preset != "pressure":
                continue
            e1, e2 = elective_subjs[0], elective_subjs[1 % len(elective_subjs)]
            req = e1["constraints"]["min_per_week"]
            if class_load[cls["id"]] + req > target_per_class:
                continue
            candidates = sorted(
                [r for r in regular_rooms if r["id"] != cls["room_id"]] or regular_rooms,
                key=lambda r: room_demand[r["id"]] + pending_home_demand(r["id"]),
            )
            if len(candidates) < 2:
                continue
            r1, r2 = candidates[0], candidates[1]
            if not (room_has_headroom(r1["id"], req) and room_has_headroom(r2["id"], req)):
                continue
            t1 = pick_teacher(e1["id"], req)
            t2 = pick_teacher(e2["id"], req, exclude={t1})
            x1 = add_assignment(cls["id"], t1, e1["id"], target_room_id=r1["id"], tag="Track A")
            x2 = add_assignment(
                cls["id"],
                t2,
                e2["id"],
                target_room_id=r2["id"],
                tag="Track B",
                co_schedule_with=[x1["id"]],
            )
            x1["constraints"]["co_schedule_with"] = [x2["id"]]
            class_load[cls["id"]] += req
            room_demand[r1["id"]] += req
            room_demand[r2["id"]] += req

    # 4. Core theory assignments filling up to target_per_class.
    for cls in classes:
        pool = theory_subjs[:-1] if (include_merged and len(theory_subjs) > 3) else list(theory_subjs)
        rng.shuffle(pool)
        added = 0
        for subj in pool:
            if subjects_per_class is not None and added >= subjects_per_class:
                break
            req = subj["constraints"]["min_per_week"]
            if class_load[cls["id"]] + req > target_per_class:
                continue
            if any(k[0] == cls["id"] and k[2] == subj["id"] for k in assigned_keys):
                continue
            tid = pick_teacher(subj["id"], req)
            if (cls["id"], tid, subj["id"], None) in assigned_keys:
                continue
            add_assignment(cls["id"], tid, subj["id"])
            class_load[cls["id"]] += req
            room_demand[cls["room_id"]] += req
            added += 1

    # 5. Add non-conflicting first_slot_days requests on ~25% of single-period theory.
    slot1_ok = 1 not in set(break_slots)
    if slot1_ok:
        req_rate = 0.28 if preset == "pressure" else 0.18
        for a in assignments:
            if (
                a["constraints"]["co_schedule_with"]
                or a["constraints"]["period_length"] > 1
                or rng.random() >= req_rate
            ):
                continue
            free_days = [
                d
                for d in days
                if (a["class_id"], d) not in class_first_busy
                and (a["teacher_id"], d) not in teacher_first_busy
            ]
            if free_days:
                picked = rng.choice(free_days)
                a["constraints"]["first_slot_days"] = [picked]
                class_first_busy.add((a["class_id"], picked))
                teacher_first_busy.add((a["teacher_id"], picked))

    _finalize_teachers(
        teachers,
        teacher_load,
        teacher_has_2h_lab,
        teacher_first_busy,
        slots,
        days,
        break_slots,
        preset,
        rng,
    )

    return {
        "project": project,
        "rooms": rooms,
        "classes": classes,
        "teachers": teachers,
        "subjects": all_subjects,
        "teacher_assignments": assignments,
    }


def validate(data: Dict[str, Any]) -> List[str]:
    """Validate schema, co-schedule consistency, and resource feasibility."""
    problems: List[str] = []

    try:
        from maketable.models import TimeTableGenerationInput
    except ImportError:
        TimeTableGenerationInput = None

    if TimeTableGenerationInput is not None:
        try:
            TimeTableGenerationInput.model_validate(data)
        except Exception as err:
            problems.append(f"schema validation failed: {err}")

    days = data["project"]["days"]
    slots = data["project"]["slots"]
    break_slots = set(data["project"].get("break_slots") or [])
    working_slots = slots - len(break_slots)
    weekly_working = working_slots * len(days)

    subj_by_id = {s["id"]: s for s in data["subjects"]}
    teacher_by_id = {t["id"]: t for t in data["teachers"]}
    room_by_id = {r["id"]: r for r in data["rooms"]}
    class_by_id = {c["id"]: c for c in data["classes"]}

    class_mins = class_effective_min_load(data["teacher_assignments"], subj_by_id)
    for cid, load in class_mins.items():
        if load > weekly_working:
            cname = class_by_id[cid]["name"]
            problems.append(f"class {cname}: min weekly periods {load} > {weekly_working}")

    teacher_load: Dict[str, int] = collections.Counter()
    seen_teacher_co: Set[Tuple[str, str]] = set()
    roots = _co_schedule_roots(data["teacher_assignments"])
    first_by_class: Set[Tuple[str, str]] = set()
    first_by_teacher: Set[Tuple[str, str]] = set()

    for a in data["teacher_assignments"]:
        tid = a["teacher_id"]
        req = subj_by_id[a["subject_id"]]["constraints"]["min_per_week"]
        t_key = (tid, roots[a["id"]])
        if t_key not in seen_teacher_co:
            seen_teacher_co.add(t_key)
            teacher_load[tid] += req

        k = a["constraints"].get("period_length", 1)
        if not _valid_block_starts(slots, list(break_slots), k):
            problems.append(f"assignment {a['id']}: period_length {k} cannot fit between breaks")

        fsd = a["constraints"].get("first_slot_days") or []
        for d in fsd:
            if (a["class_id"], d) in first_by_class:
                problems.append(f"first_slot_days conflict for class {a['class_id']} on {d}")
            first_by_class.add((a["class_id"], d))
            if (tid, d) in first_by_teacher:
                problems.append(f"first_slot_days conflict for teacher {tid} on {d}")
            first_by_teacher.add((tid, d))

    for tid, load in teacher_load.items():
        tc = teacher_by_id[tid]["constraints"]
        tname = teacher_by_id[tid]["name"]
        if load > tc["max_per_week"]:
            problems.append(f"teacher {tname}: min load {load} > max_per_week {tc['max_per_week']}")
        avail_days = tc.get("max_days_per_week") or len(days)
        if load > avail_days * tc["max_per_day"]:
            problems.append(f"teacher {tname}: min load {load} > {avail_days}*{tc['max_per_day']}")

    room_load: Dict[str, int] = collections.Counter()
    for a in data["teacher_assignments"]:
        rid = a.get("target_room_id") or class_by_id[a["class_id"]]["room_id"]
        room_load[rid] += subj_by_id[a["subject_id"]]["constraints"]["min_per_week"]
    for rid, demand in room_load.items():
        cap = room_by_id[rid]["constraints"]["capacity"]
        if demand > cap * weekly_working:
            problems.append(f"room {room_by_id[rid]['name']}: min demand {demand} > {cap * weekly_working}")

    return problems


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate realistic testable datasets for MakeTable.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--preset", choices=["college", "pressure", "school"], default="college",
                   help="dataset profile (college includes splits/merges/breaks/requests)")
    p.add_argument("--rooms", type=int, default=None, help="total rooms (auto-scaled if omitted)")
    p.add_argument("--classes", type=int, default=3, help="number of classes")
    p.add_argument("--teachers", type=int, default=None, help="number of teachers (auto if omitted)")
    p.add_argument("--subjects", type=int, default=8, help="number of core theory subjects")
    p.add_argument("--subjects-per-class", type=int, default=None, help="optional cap per class")
    p.add_argument("--slots", type=int, default=7, help="slots per day")
    p.add_argument("--days", nargs="+", default=["Mon", "Tue", "Wed", "Thu", "Fri"], help="active days")
    p.add_argument("--breaks", type=int, nargs="*", default=None, help="break slot numbers (1-indexed)")
    p.add_argument("--no-breaks", action="store_true", help="disable break slots")
    p.add_argument("--lab-ratio", type=float, default=0.3, help="fraction of rooms that are labs")
    p.add_argument("--utilization", type=float, default=None, help="target class load ratio (0.1-1.0)")
    p.add_argument("--seed", type=int, default=None, help="random seed")
    p.add_argument("-o", "--output", default="mock_data.json", help="output JSON path")
    p.add_argument("--no-validate", action="store_true", help="skip validation pass")
    p.add_argument("--solve", type=float, default=None, metavar="SEC",
                   help="immediately pressure-test solver for SEC seconds")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.classes < 1 or args.subjects < 1 or (args.rooms is not None and args.rooms < 1):
        print("error: classes, subjects, and rooms must be >= 1", file=sys.stderr)
        return 1

    breaks = [] if args.no_breaks else args.breaks
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
        preset=args.preset,
        break_slots=breaks,
        utilization=args.utilization,
    )

    if not args.no_validate:
        problems = validate(data)
        if problems:
            print(f"validation found {len(problems)} issue(s):", file=sys.stderr)
            for prob in problems:
                print(f"  - {prob}", file=sys.stderr)
            return 1
        print("validation passed: dataset is schema-correct and resource-feasible.", file=sys.stderr)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    co_cnt = sum(1 for a in data["teacher_assignments"] if a["constraints"]["co_schedule_with"])
    lab_cnt = sum(1 for a in data["teacher_assignments"] if a["constraints"]["period_length"] > 1)
    print(
        f"wrote {args.output} ({args.preset}): "
        f"{len(data['rooms'])} rooms, {len(data['classes'])} classes, "
        f"{len(data['teachers'])} teachers, {len(data['subjects'])} subjects, "
        f"{len(data['teacher_assignments'])} assignments "
        f"({co_cnt} co-scheduled, {lab_cnt} multi-slot, breaks={data['project']['break_slots']})",
        file=sys.stderr,
    )

    if args.solve is not None:
        from maketable import generate as solve_timetable
        from maketable.models import TimeTableGenerationInput

        t0 = time.perf_counter()
        res = solve_timetable(TimeTableGenerationInput(**data), time_limit_sec=args.solve, seed=args.seed or 42)
        elapsed = time.perf_counter() - t0
        viol_sum = sum(v.val for v in res.violations)
        print(
            f"solve check ({elapsed:.2f}s): success={res.success} "
            f"entries={len(res.entries)} violations={len(res.violations)} (sum={viol_sum})",
            file=sys.stderr,
        )
        return 0 if res.success else 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
