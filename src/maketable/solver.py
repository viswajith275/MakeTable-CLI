from ortools.sat.python import cp_model
from dataclasses import dataclass
from .models import (
    TimeTableGenerationInput,
    WeekDay,
    TimeTableEntryOutput,
    ViolationOut,
    GeneratedResponse,
    _co_schedule_roots,
    valid_block_starts,
)
import collections
import threading
import time
from typing import List, Dict

@dataclass
class SlackTracker:
    variable: cp_model.IntVar
    error_msg: str
    weight: int


class _PlateauStopper(cp_model.CpSolverSolutionCallback):
    """Stop the search once no better timetable has appeared for a while.

    CP-SAT's relative gap limit rarely fires on this model because the objective
    mixes violation slacks that give a weak bound, so a stagnation stop is what
    actually ends the search early without giving up quality.
    """

    def __init__(self, solver: cp_model.CpSolver, patience: float) -> None:
        super().__init__()
        self._solver = solver
        self._patience = patience
        self._best: float | None = None
        self._last_improvement = time.monotonic()
        self._finished = threading.Event()
        threading.Thread(target=self._watch, daemon=True).start()

    def _watch(self) -> None:
        while not self._finished.wait(0.2):
            if (
                self._best is not None
                and time.monotonic() - self._last_improvement >= self._patience
            ):
                self._solver.stop_search()
                return

    def on_solution_callback(self) -> None:
        objective = self.objective_value
        if self._best is None or objective < self._best:
            self._best = objective
            self._last_improvement = time.monotonic()

    def close(self) -> None:
        self._finished.set()


class TimeTableGenerator:
    def __init__(self, RawData: TimeTableGenerationInput, HintData: GeneratedResponse | None = None) -> None:

        # The solver is a public entry point, so validate again instead of
        # trusting a model that may have been mutated by the caller.
        RawData = TimeTableGenerationInput.model_validate(RawData.model_dump())

        self.ASSIGNMENT_WEIGHT = 10

        self.teachers_dict = {t.id: t for t in RawData.teachers}
        self.subjects_dict = {s.id: s for s in RawData.subjects}
        self.rooms_dict    = {r.id: r for r in RawData.rooms}
        self.classes_dict  = {c.id: c for c in RawData.classes}

        self.model = cp_model.CpModel()
        self.slots = RawData.project.slots

        self.teacher_schedule = collections.defaultdict(lambda: collections.defaultdict(lambda: collections.defaultdict(list)))
        self.subject_schedule = collections.defaultdict(lambda: collections.defaultdict(lambda: collections.defaultdict(list)))
        self.room_schedule    = collections.defaultdict(lambda: collections.defaultdict(lambda: collections.defaultdict(list)))
        self.class_schedule   = collections.defaultdict(lambda: collections.defaultdict(lambda: collections.defaultdict(list)))
        self.class_subject_schedule = collections.defaultdict(lambda: collections.defaultdict(lambda: collections.defaultdict(lambda: collections.defaultdict(list))))
        self.assignment_schedule = collections.defaultdict(lambda: collections.defaultdict(dict))

        self.assignments = RawData.teacher_assignments

        self.MorningTendencyValues = {
            'Low': 0,
            'Med': 1,
            'High': 2
        }

        self.index_to_day: dict[int, WeekDay] = {
            i: d for i, d in enumerate(WeekDay)
        }
        self.day_to_index: dict[WeekDay, int] = {
            d: i for i, d in enumerate(WeekDay)
        }
        self.days = [self.day_to_index[d] for d in RawData.project.days]

        self.break_slots = sorted(RawData.project.break_slots)
        self.break_slot_indices = {s - 1 for s in self.break_slots if 0 <= s - 1 < self.slots}
        self.assignment_co_group = _co_schedule_roots(self.assignments)

        self.hint_vars = HintData

        self.error_slacks: Dict[str, SlackTracker] = {}
        self.slack_counter: int = 0
        self.silent_minimization: list = []
        self._built = False

    def _minimize_gaps(self, label: str, weight: int = 3) -> None:
        # Linear prefix/suffix occupancy formulation avoids O(S^2) LinMax and
        # enforced BoolAnd/BoolOr clauses while keeping identical gap penalties.
        schedule_dict = (
            self.teacher_schedule if label == "teachers" else self.class_schedule
        )

        for entity_id, vars_for_entity in schedule_dict.items():
            for day in self.days:
                working_slots = [
                    s for s in range(self.slots) if s not in self.break_slot_indices
                ]
                while working_slots and not vars_for_entity[day][working_slots[0]]:
                    working_slots.pop(0)
                while working_slots and not vars_for_entity[day][working_slots[-1]]:
                    working_slots.pop()

                n = len(working_slots)
                if n < 3:
                    continue

                slot_occ = []
                for s in working_slots:
                    vlist = vars_for_entity[day][s]
                    if not vlist:
                        slot_occ.append(0)
                    elif len(vlist) == 1:
                        slot_occ.append(vlist[0])
                    else:
                        # Materialize a single binary occupancy proxy so before/after/gap
                        # constraints do not re-expand sum(vlist) 3x in the LP matrix.
                        occ = self.model.new_bool_var(
                            f"occ_{label}_{entity_id}_{day}_{s}"
                        )
                        self.model.add(occ == sum(vlist))
                        slot_occ.append(occ)

                before_vars: dict = {1: slot_occ[0]}
                for i in range(2, n - 1):
                    bv = self.model.new_bool_var(f"before_{label}_{entity_id}_{day}_{i}")
                    self.model.add(bv >= before_vars[i - 1])
                    if not isinstance(slot_occ[i - 1], int):
                        self.model.add(bv >= slot_occ[i - 1])
                    before_vars[i] = bv

                after_vars: dict = {n - 2: slot_occ[n - 1]}
                for i in range(n - 3, 0, -1):
                    av = self.model.new_bool_var(f"after_{label}_{entity_id}_{day}_{i}")
                    self.model.add(av >= after_vars[i + 1])
                    if not isinstance(slot_occ[i + 1], int):
                        self.model.add(av >= slot_occ[i + 1])
                    after_vars[i] = av

                for i in range(1, n - 1):
                    is_gap = self.model.new_bool_var(f"gap_{label}_{entity_id}_{day}_{i}")
                    self.model.add(is_gap >= before_vars[i] + after_vars[i] - slot_occ[i] - 1)
                    self.silent_minimization.append(weight * is_gap)

    def _create_slack(self, name: str, weight: int, error_msg: str, upper_bound: int) -> cp_model.IntVar:
        self.slack_counter += 1
        unique_key = f"{name}_{self.slack_counter}"
        if upper_bound == 1:
            slack_var = self.model.new_bool_var(f"slack_{unique_key}")
        else:
            slack_var = self.model.new_int_var(
                lb=0, ub=upper_bound, name=f"slack_{unique_key}"
            )

        self.error_slacks[unique_key] = SlackTracker(
            variable=slack_var, weight=weight, error_msg=error_msg
        )

        return slack_var

    def _build_group_day_vars(self, co_group, period_length: int) -> dict[int, dict[int, cp_model.IntVar]]:
        """Create one shared variable grid per co-schedule group instead of duplicating variables."""
        group_vars: dict[int, dict[int, cp_model.IntVar]] = collections.defaultdict(dict)
        k = period_length
        for day in self.days:
            if k <= 1:
                for slot in range(self.slots):
                    var = self.model.new_bool_var(f"assign_{co_group}_d{day}_s{slot}")
                    if slot in self.break_slot_indices:
                        self.model.add(var == 0)
                    group_vars[day][slot] = var
            else:
                start_vars = {
                    s: self.model.new_bool_var(f"start_{co_group}_d{day}_s{s}")
                    for s in valid_block_starts(self.slots, self.break_slots, k)
                }
                for s in start_vars:
                    conflicts = [start_vars[s2] for s2 in range(s, min(self.slots, s + k)) if s2 in start_vars]
                    if len(conflicts) > 1:
                        self.model.add_at_most_one(conflicts)
                for slot in range(self.slots):
                    if slot in self.break_slot_indices:
                        var = self.model.new_bool_var(f"assign_{co_group}_d{day}_s{slot}")
                        self.model.add(var == 0)
                    else:
                        covering = [
                            start_vars[s]
                            for s in range(max(0, slot - k + 1), slot + 1)
                            if s in start_vars
                        ]
                        if not covering:
                            var = self.model.new_bool_var(f"assign_{co_group}_d{day}_s{slot}")
                            self.model.add(var == 0)
                        elif len(covering) == 1:
                            var = covering[0]
                        else:
                            var = self.model.new_bool_var(f"assign_{co_group}_d{day}_s{slot}")
                            self.model.add(var == sum(covering))
                    group_vars[day][slot] = var
        return group_vars

    def _create_and_apply_variables(self) -> None:
        teacher_slot_groups: dict = collections.defaultdict(lambda: collections.defaultdict(set))
        class_slot_groups: dict = collections.defaultdict(lambda: collections.defaultdict(set))
        room_slot_groups: dict = collections.defaultdict(lambda: collections.defaultdict(set))
        class_subject_slot_groups: dict = collections.defaultdict(lambda: collections.defaultdict(set))
        co_group_vars: dict = {}

        for assignment in self.assignments:
            class_ = self.classes_dict[assignment.class_id]
            room = self.rooms_dict[class_.room_id]
            if assignment.target_room_id is not None:
                room = self.rooms_dict[assignment.target_room_id]

            co_group = self.assignment_co_group[assignment.id]
            if co_group not in co_group_vars:
                co_group_vars[co_group] = self._build_group_day_vars(
                    co_group, assignment.constraints.period_length
                )
            group_grid = co_group_vars[co_group]

            for day in self.days:
                for slot in range(self.slots):
                    var = group_grid[day][slot]
                    if slot not in self.break_slot_indices:
                        if (assignment.teacher_id, co_group) not in teacher_slot_groups[day][slot]:
                            teacher_slot_groups[day][slot].add((assignment.teacher_id, co_group))
                            self.teacher_schedule[assignment.teacher_id][day][slot].append(var)

                        # Capacity counts classes, so merged classes sharing
                        # a room each consume a place even when co-scheduled.
                        room_key = (room.id, class_.id, co_group)
                        if room_key not in room_slot_groups[day][slot]:
                            room_slot_groups[day][slot].add(room_key)
                            self.room_schedule[room.id][day][slot].append(var)

                        if (class_.id, co_group) not in class_slot_groups[day][slot]:
                            class_slot_groups[day][slot].add((class_.id, co_group))
                            self.class_schedule[class_.id][day][slot].append(var)

                        if (class_.id, assignment.subject_id, co_group) not in class_subject_slot_groups[day][slot]:
                            class_subject_slot_groups[day][slot].add((class_.id, assignment.subject_id, co_group))
                            self.class_subject_schedule[class_.id][assignment.subject_id][day][slot].append(var)

                    self.assignment_schedule[assignment.id][day][slot] = var

    def _apply_hints_vars(self) -> None:
        if self.hint_vars is None:
            self._apply_greedy_hints()
            return

        hinted: set = set()
        for entry in self.hint_vars.entries:
            day = self.day_to_index.get(entry.day)
            slot = entry.slot - 1
            # Hints may come from an older or different input file; ignore
            # anything that has no matching variable instead of crashing.
            if day is None or day not in self.days:
                continue
            if not 0 <= slot < self.slots:
                continue

            var = self.assignment_schedule.get(entry.assignment_id, {}).get(day, {}).get(slot)
            if var is not None and id(var) not in hinted:
                hinted.add(id(var))
                self.model.add_hint(var, 1)

    def _apply_greedy_hints(self) -> None:
        """Supply a lightweight conflict-free initial hint for large schedules."""
        if not self.assignments or not self.days:
            return

        groups: dict = collections.defaultdict(list)
        for a in self.assignments:
            groups[self.assignment_co_group[a.id]].append(a)

        unavail_by_teacher: dict = {
            t_id: {
                (self.day_to_index[u.day], u.slot - 1)
                for u in teacher.constraints.unavailable_slots
                if u.day in self.day_to_index
            }
            for t_id, teacher in self.teachers_dict.items()
        }

        group_meta = []
        for g_root, members in groups.items():
            rep = members[0]
            k = rep.constraints.period_length
            valid_starts = valid_block_starts(self.slots, self.break_slots, k)
            if not valid_starts:
                continue
            target_slots = max(
                max(1, self.subjects_dict[a.subject_id].constraints.min_per_week or 1)
                for a in members
            )
            max_caps = [
                self.subjects_dict[a.subject_id].constraints.max_per_week
                for a in members
                if (self.subjects_dict[a.subject_id].constraints.max_per_week or 0) > 0
            ]
            if max_caps:
                target_slots = min(target_slots, min(max_caps))
            blocks_needed = max(1, (target_slots + k - 1) // k)
            first_days = {
                self.day_to_index[d]
                for a in members
                for d in (a.constraints.first_slot_days or [])
                if d in self.day_to_index
            }
            morning_score = max(
                self.MorningTendencyValues.get(
                    self.subjects_dict[a.subject_id].constraints.morning_tendency.value, 0
                )
                for a in members
            )
            rooms = [
                (a.class_id, a.target_room_id or self.classes_dict[a.class_id].room_id)
                for a in members
            ]
            teachers = {a.teacher_id for a in members}
            classes = {a.class_id for a in members}
            group_meta.append(
                (rep.id, members, k, valid_starts, blocks_needed, first_days, morning_score, rooms, teachers, classes)
            )

        # Hardest-to-place blocks go first: pinned first-slot, multi-slot labs, multi-resource groups.
        group_meta.sort(
            key=lambda item: (
                -int(bool(item[5])),
                -item[2],
                -(len(item[8]) + len(item[9])),
                -item[4],
                -item[6],
            )
        )

        teacher_busy: set = set()
        class_busy: set = set()
        room_load: dict = collections.Counter()
        teacher_day_cnt: dict = collections.Counter()
        teacher_week_cnt: dict = collections.Counter()
        class_subj_day_cnt: dict = collections.Counter()
        hinted_vars: set = set()

        def can_place(day_idx: int, start_slot: int, k: int, rooms, teachers, classes, members) -> bool:
            for t_id in teachers:
                t_c = self.teachers_dict[t_id].constraints
                if t_c.max_per_day is not None and teacher_day_cnt[(t_id, day_idx)] + k > t_c.max_per_day:
                    return False
                if t_c.max_per_week is not None and teacher_week_cnt[t_id] + k > t_c.max_per_week:
                    return False
            for a in members:
                s_max = self.subjects_dict[a.subject_id].constraints.max_per_day
                if s_max is not None and s_max > 0 and class_subj_day_cnt[(a.class_id, a.subject_id, day_idx)] + k > s_max:
                    return False
            for slot in range(start_slot, start_slot + k):
                for t_id in teachers:
                    if (t_id, day_idx, slot) in teacher_busy or (day_idx, slot) in unavail_by_teacher.get(t_id, ()):
                        return False
                for c_id in classes:
                    if (c_id, day_idx, slot) in class_busy:
                        return False
                room_delta: dict = collections.Counter()
                for _c_id, r_id in rooms:
                    room_delta[r_id] += 1
                for r_id, delta in room_delta.items():
                    cap = self.rooms_dict[r_id].constraints.capacity
                    if room_load[(r_id, day_idx, slot)] + delta > cap:
                        return False
            return True

        def commit(rep_id, day_idx: int, start_slot: int, k: int, rooms, teachers, classes, members) -> None:
            for t_id in teachers:
                teacher_day_cnt[(t_id, day_idx)] += k
                teacher_week_cnt[t_id] += k
            for a in members:
                class_subj_day_cnt[(a.class_id, a.subject_id, day_idx)] += k
            day_vars = self.assignment_schedule.get(rep_id, {}).get(day_idx, {})
            for slot in range(start_slot, start_slot + k):
                for t_id in teachers:
                    teacher_busy.add((t_id, day_idx, slot))
                for c_id in classes:
                    class_busy.add((c_id, day_idx, slot))
                for _c_id, r_id in rooms:
                    room_load[(r_id, day_idx, slot)] += 1
                var = day_vars.get(slot)
                if var is not None and id(var) not in hinted_vars:
                    hinted_vars.add(id(var))
                    self.model.add_hint(var, 1)

        for rep_id, members, k, valid_starts, blocks_needed, first_days, _morning, rooms, teachers, classes in group_meta:
            placed = 0
            for d_idx in sorted(first_days):
                if d_idx in self.days and 0 in valid_starts and placed < blocks_needed:
                    if can_place(d_idx, 0, k, rooms, teachers, classes, members):
                        commit(rep_id, d_idx, 0, k, rooms, teachers, classes, members)
                        placed += 1

            while placed < blocks_needed:
                best_choice = None
                ordered_days = sorted(
                    self.days,
                    key=lambda d_idx: (
                        max(class_subj_day_cnt[(a.class_id, a.subject_id, d_idx)] for a in members),
                        sum(teacher_day_cnt[(t_id, d_idx)] for t_id in teachers),
                        d_idx,
                    ),
                )
                for d_idx in ordered_days:
                    for s in valid_starts:
                        if can_place(d_idx, s, k, rooms, teachers, classes, members):
                            best_choice = (d_idx, s)
                            break
                    if best_choice is not None:
                        break
                if best_choice is None:
                    break
                commit(rep_id, best_choice[0], best_choice[1], k, rooms, teachers, classes, members)
                placed += 1

    def _apply_generic_conditions(self) -> None:
        for day in self.days:
            for slot in range(self.slots):
                if slot in self.break_slot_indices:
                    continue

                for schedule_grid in self.teacher_schedule.values():
                    variables_at_this_time = schedule_grid[day][slot]
                    if len(variables_at_this_time) > 1:
                        self.model.add_at_most_one(variables_at_this_time)

                for schedule_grid in self.class_schedule.values():
                    variables_at_this_time = schedule_grid[day][slot]
                    if len(variables_at_this_time) > 1:
                        self.model.add_at_most_one(variables_at_this_time)

                for r_id, schedule_grid in self.room_schedule.items():
                    variables_at_this_time = schedule_grid[day][slot]
                    room_capacity = self.rooms_dict[r_id].constraints.capacity
                    if len(variables_at_this_time) > room_capacity:
                        if room_capacity == 1:
                            self.model.add_at_most_one(variables_at_this_time)
                        else:
                            self.model.add(sum(variables_at_this_time) <= room_capacity)

    def _teacher_max_per_day(self) -> None:
        for t_id, schedule_grid in self.teacher_schedule.items():
            teacher = self.teachers_dict[t_id]
            max_limit = teacher.constraints.max_per_day
            if max_limit is not None and max_limit > 0:
                for day in self.days:
                    active_slots = 0
                    vars_for_this_day = []
                    for slot in range(self.slots):
                        if schedule_grid[day][slot]:
                            active_slots += 1
                            vars_for_this_day.extend(schedule_grid[day][slot])
                    if active_slots <= max_limit:
                        continue

                    error_msg = f"Max daily classes exceeded for {teacher.name} (limit: {max_limit})"
                    slack = self._create_slack(
                        name="teacher daily limit",
                        error_msg=error_msg,
                        weight=250,
                        upper_bound=active_slots - max_limit,
                    )
                    self.model.add(sum(vars_for_this_day) <= max_limit + slack)

    def _teacher_max_per_week(self) -> None:
        for t_id, schedule_grid in self.teacher_schedule.items():
            teacher = self.teachers_dict[t_id]
            max_limit = teacher.constraints.max_per_week
            if max_limit is not None and max_limit > 0:
                active_slots = 0
                vars_for_week = []
                for day in self.days:
                    for slot in range(self.slots):
                        if schedule_grid[day][slot]:
                            active_slots += 1
                            vars_for_week.extend(schedule_grid[day][slot])
                if active_slots <= max_limit:
                    continue

                error_msg = f"Max weekly classes exceeded for {teacher.name} (limit: {max_limit})"
                slack = self._create_slack(
                    name="teacher weekly limit",
                    error_msg=error_msg,
                    weight=250,
                    upper_bound=active_slots - max_limit,
                )
                self.model.add(sum(vars_for_week) <= max_limit + slack)

    def _teacher_max_consecutive(self) -> None:
        for t_id, schedule_grid in self.teacher_schedule.items():
            teacher = self.teachers_dict[t_id]
            max_limit = teacher.constraints.max_consecutive
            if max_limit is not None and max_limit > 0:
                if self.slots < max_limit + 1:
                    continue
                max_window_size = max_limit + 1
                error_msg = f"Max consecutive classes exceeded {teacher.name} (limit: {max_limit})"

                for day in self.days:
                    for start_slot in range(self.slots - max_window_size + 1):
                        active_slots = 0
                        vars_for_consecutive = []
                        for slot in range(start_slot, start_slot + max_window_size):
                            if schedule_grid[day][slot]:
                                active_slots += 1
                                vars_for_consecutive.extend(schedule_grid[day][slot])
                        if active_slots <= max_limit:
                            continue

                        slack = self._create_slack(
                            name="teacher consecutive limit",
                            error_msg=error_msg,
                            weight=200,
                            upper_bound=1,
                        )
                        self.model.add(sum(vars_for_consecutive) <= max_limit + slack)

    def _teacher_balance_daily_load(self, weight: int = 5):
        max_day_slots = self.slots - len(self.break_slot_indices)
        for teacher_id, vars_for_teacher in self.teacher_schedule.items():
            teacher = self.teachers_dict.get(teacher_id)
            if (
                teacher
                and teacher.constraints.max_days_per_week is not None
                and teacher.constraints.max_days_per_week < len(self.days)
            ):
                continue

            daily_exprs = []
            for day in self.days:
                active_slots = 0
                vars_for_day = []
                for slot in range(self.slots):
                    if vars_for_teacher[day][slot]:
                        active_slots += 1
                        vars_for_day.extend(vars_for_teacher[day][slot])
                if not vars_for_day:
                    continue
                if active_slots == 1 and len(vars_for_day) == 1:
                    daily_exprs.append(vars_for_day[0])
                else:
                    day_load = self.model.new_int_var(
                        0, min(max_day_slots, active_slots), f"dload_{teacher_id}_{day}"
                    )
                    self.model.add(day_load == sum(vars_for_day))
                    daily_exprs.append(day_load)

            if len(daily_exprs) < 2:
                continue

            max_load = self.model.new_int_var(0, max_day_slots, f"maxload_{teacher_id}")
            min_load = self.model.new_int_var(0, max_day_slots, f"minload_{teacher_id}")
            for expr in daily_exprs:
                self.model.add(max_load >= expr)
                self.model.add(min_load <= expr)

            self.silent_minimization.append(weight * (max_load - min_load))

    def _teacher_unavailable_slots(self) -> None:
        for t_id, teacher in self.teachers_dict.items():
            for slot_item in teacher.constraints.unavailable_slots:
                day_idx = self.day_to_index.get(slot_item.day)
                slot_idx = slot_item.slot - 1
                if (
                    day_idx is not None
                    and day_idx in self.days
                    and 0 <= slot_idx < self.slots
                    and slot_idx not in self.break_slot_indices
                ):
                    for var in self.teacher_schedule[t_id][day_idx][slot_idx]:
                        self.model.add(var == 0)

    def _teacher_avoid_slots(self) -> None:
        for t_id, teacher in self.teachers_dict.items():
            for slot_item in teacher.constraints.avoid_slots:
                day_idx = self.day_to_index.get(slot_item.day)
                slot_idx = slot_item.slot - 1
                if (
                    day_idx is not None
                    and day_idx in self.days
                    and 0 <= slot_idx < self.slots
                    and slot_idx not in self.break_slot_indices
                ):
                    vars_at_time = self.teacher_schedule[t_id][day_idx][slot_idx]
                    if vars_at_time:
                        error_msg = f"{teacher.name}: Assigned during avoided slot ({slot_item.day.value} Slot {slot_item.slot})"
                        slack = self._create_slack(
                            name="teacher avoid slot",
                            error_msg=error_msg,
                            weight=180,
                            upper_bound=1,
                        )
                        self.model.add(sum(vars_at_time) <= slack)

    def _teacher_max_days_per_week(self) -> None:
        for t_id, teacher in self.teachers_dict.items():
            max_days = teacher.constraints.max_days_per_week
            if max_days is not None and 0 < max_days < len(self.days):
                day_worked_vars = []
                for day in self.days:
                    vars_for_day = [
                        v for slot in range(self.slots) for v in self.teacher_schedule[t_id][day][slot]
                    ]
                    if not vars_for_day:
                        continue
                    if len(vars_for_day) == 1:
                        day_worked_vars.append(vars_for_day[0])
                    else:
                        worked = self.model.new_bool_var(f"t_{t_id}_worked_d{day}")
                        for v in vars_for_day:
                            self.model.add(worked >= v)
                        day_worked_vars.append(worked)

                if len(day_worked_vars) > max_days:
                    error_msg = f"Max working days exceeded for {teacher.name} (limit: {max_days})"
                    slack = self._create_slack(
                        name="teacher max days",
                        error_msg=error_msg,
                        weight=220,
                        upper_bound=len(day_worked_vars) - max_days,
                    )
                    self.model.add(sum(day_worked_vars) <= max_days + slack)

    def _subject_max_per_day(self) -> None:
        for c_id, subjects in self.class_subject_schedule.items():
            class_name = self.classes_dict[c_id].name
            for sub_id, days_dict in subjects.items():
                subject = self.subjects_dict[sub_id]
                max_limit = subject.constraints.max_per_day
                if max_limit is not None and max_limit >= 0:
                    error_msg = f"{class_name}: Max daily classes exceeded for {subject.name} (limit: {max_limit})"
                    for day in self.days:
                        active_slots = 0
                        vars_for_day = []
                        for slot in range(self.slots):
                            if days_dict[day][slot]:
                                active_slots += 1
                                vars_for_day.extend(days_dict[day][slot])
                        if active_slots <= max_limit:
                            continue

                        slack = self._create_slack(
                            name="subject daily limit",
                            error_msg=error_msg,
                            weight=200,
                            upper_bound=active_slots - max_limit,
                        )
                        self.model.add(sum(vars_for_day) <= max_limit + slack)

    def _subject_min_per_day(self) -> None:
        for c_id, subjects in self.class_subject_schedule.items():
            class_name = self.classes_dict[c_id].name
            for sub_id, days_dict in subjects.items():
                subject = self.subjects_dict[sub_id]
                min_limit = subject.constraints.min_per_day
                if min_limit is not None and min_limit > 0:
                    error_msg = f"{class_name}: Min daily classes didn't meet for {subject.name} (required: {min_limit})"
                    for day in self.days:
                        vars_for_day = [
                            v for slot in range(self.slots) for v in days_dict[day][slot]
                        ]
                        if not vars_for_day:
                            continue

                        slack = self._create_slack(
                            name="subject daily required",
                            error_msg=error_msg,
                            weight=200,
                            upper_bound=min_limit,
                        )
                        self.model.add(sum(vars_for_day) >= min_limit - slack)

    def _subject_max_per_week(self) -> None:
        for c_id, subjects in self.class_subject_schedule.items():
            class_name = self.classes_dict[c_id].name
            for sub_id, days_dict in subjects.items():
                subject = self.subjects_dict[sub_id]
                max_limit = subject.constraints.max_per_week
                if max_limit is not None and max_limit >= 0:
                    active_slots = 0
                    vars_for_week = []
                    for day in self.days:
                        for slot in range(self.slots):
                            if days_dict[day][slot]:
                                active_slots += 1
                                vars_for_week.extend(days_dict[day][slot])
                    if active_slots <= max_limit:
                        continue

                    error_msg = f"{class_name}: Max weekly classes exceeded for {subject.name} (limit: {max_limit})"
                    slack = self._create_slack(
                        name="subject weekly limit",
                        error_msg=error_msg,
                        weight=200,
                        upper_bound=active_slots - max_limit,
                    )
                    self.model.add(sum(vars_for_week) <= max_limit + slack)

    def _subject_min_per_week(self) -> None:
        for c_id, subjects in self.class_subject_schedule.items():
            class_name = self.classes_dict[c_id].name
            for sub_id, days_dict in subjects.items():
                subject = self.subjects_dict[sub_id]
                min_limit = subject.constraints.min_per_week
                if min_limit is not None and min_limit > 0:
                    vars_for_week = [
                        v
                        for day in self.days
                        for slot in range(self.slots)
                        for v in days_dict[day][slot]
                    ]
                    if not vars_for_week:
                        continue

                    error_msg = f"{class_name}: Min weekly classes didn't meet for {subject.name} (required: {min_limit})"
                    slack = self._create_slack(
                        name="subject weekly required",
                        error_msg=error_msg,
                        weight=200,
                        upper_bound=min_limit,
                    )
                    self.model.add(sum(vars_for_week) >= min_limit - slack)

    def _subject_max_consecutive(self) -> None:
        for c_id, subjects in self.class_subject_schedule.items():
            class_name = self.classes_dict[c_id].name
            for sub_id, days_dict in subjects.items():
                subject = self.subjects_dict[sub_id]
                max_limit = subject.constraints.max_consecutive
                if max_limit is not None and max_limit > 0 and self.slots > max_limit:
                    error_msg = f"{class_name}: Max consecutive classes exceeded for {subject.name} (limit: {max_limit})"
                    for day in self.days:
                        for i in range(self.slots - max_limit):
                            active_slots = 0
                            window_vars = []
                            for slot in range(i, i + max_limit + 1):
                                if days_dict[day][slot]:
                                    active_slots += 1
                                    window_vars.extend(days_dict[day][slot])
                            if active_slots <= max_limit:
                                continue

                            slack = self._create_slack(
                                name="subject maximum consecutive class",
                                error_msg=error_msg,
                                weight=250,
                                upper_bound=1,
                            )
                            self.model.add(sum(window_vars) <= max_limit + slack)

    def _subject_min_consecutive(self) -> None:
        for c_id, subjects in self.class_subject_schedule.items():
            class_name = self.classes_dict[c_id].name
            for sub_id, days_dict in subjects.items():
                subject = self.subjects_dict[sub_id]
                min_limit = subject.constraints.min_consecutive
                if min_limit is not None and min_limit > 1 and self.slots >= min_limit:
                    error_msg = (
                        f"{class_name}: Min consecutive classes didn't meet for {subject.name} (required: {min_limit})"
                    )
                    for day in self.days:
                        if not any(days_dict[day][s] for s in range(self.slots)):
                            continue

                        slot_occ = []
                        for slot in range(self.slots):
                            v_list = days_dict[day][slot]
                            if not v_list:
                                slot_occ.append(0)
                            elif len(v_list) == 1:
                                slot_occ.append(v_list[0])
                            else:
                                occ = self.model.new_bool_var(
                                    f"socc_{c_id}_{sub_id}_{day}_{slot}"
                                )
                                self.model.add(occ == sum(v_list))
                                slot_occ.append(occ)

                        for i in range(self.slots):
                            if not days_dict[day][i]:
                                continue
                            curr = slot_occ[i]
                            if i == 0 or not days_dict[day][i - 1]:
                                is_start = curr
                            else:
                                prev = slot_occ[i - 1]
                                is_start = self.model.new_bool_var(
                                    f"blk_start_{c_id}_{sub_id}_{day}_{i}"
                                )
                                self.model.add(is_start >= curr - prev)

                            for k in range(1, min_limit):
                                j = i + k
                                slack = self._create_slack(
                                    name="subject min consecutive",
                                    error_msg=error_msg,
                                    weight=270,
                                    upper_bound=1,
                                )
                                if j < self.slots and days_dict[day][j]:
                                    self.model.add(slot_occ[j] + slack >= is_start)
                                else:
                                    self.model.add(slack >= is_start)

    def _subject_morning_tendency(self) -> None:
        for c_id, subjects in self.class_subject_schedule.items():
            for sub_id, days_dict in subjects.items():
                subject = self.subjects_dict[sub_id]
                tendency = subject.constraints.morning_tendency
                if tendency is not None:
                    multiplier = self.MorningTendencyValues.get(tendency.value, 0)
                    if multiplier == 0:
                        continue

                    for day in self.days:
                        for slot_index in range(self.slots):
                            if slot_index in self.break_slot_indices or slot_index == 0:
                                continue
                            lateness = slot_index / (self.slots - 1) if self.slots > 1 else 0
                            scaled_cost = round(multiplier * lateness * 10)
                            if scaled_cost == 0:
                                continue
                            for var in days_dict[day][slot_index]:
                                self.silent_minimization.append(scaled_cost * var)

    def _subject_slot_variety(self, weight: int = 2):
        for c_id, subjects in self.class_subject_schedule.items():
            for sub_id, days_dict in subjects.items():
                for slot_index in range(self.slots):
                    if slot_index in self.break_slot_indices:
                        continue
                    same_slot_flags = [
                        v for day in self.days for v in days_dict[day][slot_index]
                    ]
                    if len(same_slot_flags) < 2:
                        continue

                    excess = self.model.new_int_var(
                        0, len(same_slot_flags) - 1, f"excess_{c_id}_{sub_id}_{slot_index}"
                    )
                    self.model.add(excess >= sum(same_slot_flags) - 1)
                    self.silent_minimization.append(weight * excess)

    def _teacher_assignment_first_slot_days(self) -> None:
        FIRST_SLOT_INDEX = 0
        for assignment in self.assignments:
            first_slot_days = assignment.constraints.first_slot_days
            if not first_slot_days:
                continue

            vars_for_assignment = self.assignment_schedule.get(assignment.id)
            if vars_for_assignment is None:
                continue

            class_name = self.classes_dict[assignment.class_id].name
            subject_name = self.subjects_dict[assignment.subject_id].name
            teacher_name = self.teachers_dict[assignment.teacher_id].name

            for day_obj in first_slot_days:
                day_idx = self.day_to_index.get(day_obj)
                if day_idx is None or day_idx not in self.days:
                    continue

                vars_for_day = vars_for_assignment.get(day_idx)
                if not vars_for_day or FIRST_SLOT_INDEX not in vars_for_day:
                    continue

                first_slot_var = vars_for_day[FIRST_SLOT_INDEX]
                error_msg = f"First slot required on {day_obj.value} for {class_name} - {subject_name} ({teacher_name})"
                slack = self._create_slack(
                    name="assignment first slot required",
                    error_msg=error_msg,
                    weight=250,
                    upper_bound=1,
                )
                self.model.add(first_slot_var + slack >= 1)

    def _apply_class_constraints(self) -> None:
        self._minimize_gaps(label="classes", weight=3)

    def _apply_teacher_constraints(self) -> None:
        self._teacher_max_per_day()
        self._teacher_max_per_week()
        self._teacher_max_consecutive()
        self._teacher_balance_daily_load(weight=5)
        self._teacher_unavailable_slots()
        self._teacher_avoid_slots()
        self._teacher_max_days_per_week()
        self._minimize_gaps(label="teachers", weight=5)

    def _apply_subject_constraints(self) -> None:
        self._subject_max_per_day()
        self._subject_min_per_day()
        self._subject_max_per_week()
        self._subject_min_per_week()
        self._subject_max_consecutive()
        self._subject_min_consecutive()
        self._subject_morning_tendency()
        self._subject_slot_variety(weight=2)

    def _apply_teacher_assignment_constraints(self) -> None:
        self._teacher_assignment_first_slot_days()

    def _apply_minimization(self):
        objective_terms = []

        for slack in self.error_slacks.values():
            objective_terms.append(slack.weight * slack.variable)

        objective_terms.extend(self.silent_minimization)

        all_assignment_schedule = [
            var
            for day_vars in self.assignment_schedule.values()
            for slot_vars in day_vars.values()
            for var in slot_vars.values()
        ]

        if all_assignment_schedule:
            objective_terms.append(
                -self.ASSIGNMENT_WEIGHT * sum(all_assignment_schedule)
            )

        if not objective_terms:
            return

        self.model.minimize(sum(objective_terms))

    def _build_and_apply_all_constraints(self) -> None:
        if self._built:
            return

        self._create_and_apply_variables()
        self._apply_hints_vars()
        self._apply_generic_conditions()
        self._apply_class_constraints()
        self._apply_teacher_constraints()
        self._apply_subject_constraints()
        self._apply_teacher_assignment_constraints()
        self._apply_minimization()
        self._built = True

    
    def _fetch_error_slacks(self, solver: cp_model.CpSolver) -> List[ViolationOut]:

        # Several windows can report the same soft constraint; show it once.
        slack_errors: dict[str, ViolationOut] = {}
        for slack in self.error_slacks.values():

            value = solver.value(slack.variable)

            if value > 0:
                existing = slack_errors.get(slack.error_msg)
                if existing is None:
                    slack_errors[slack.error_msg] = ViolationOut(
                        error_msg=slack.error_msg, val=value
                    )
                else:
                    existing.val += value

        return list(slack_errors.values())
    
    
    def _fetch_timetable_entries(self, solver: cp_model.CpSolver) -> List[TimeTableEntryOutput]: # Also for now return the list of timetable after schema/model definition
        
        timetable_entries = []

        for assignment in self.assignments:

            assignment_schedule = self.assignment_schedule.get(assignment.id)

            if assignment_schedule is None:
                continue

            for day, day_vars in assignment_schedule.items():
                for slot, slot_var in day_vars.items():

                    if solver.value(slot_var):
                        class_ = self.classes_dict[assignment.class_id]
                        room_id = class_.room_id
                        if assignment.target_room_id is not None:
                            room_id = assignment.target_room_id

                        timetable_entries.append(
                            TimeTableEntryOutput(
                                assignment_id=assignment.id,
                                class_name=class_.name,
                                teacher_name=self.teachers_dict[assignment.teacher_id].name,
                                subject_name=self.subjects_dict[assignment.subject_id].name,
                                room_name=self.rooms_dict[room_id].name,
                                day=self.index_to_day[day],
                                slot=slot + 1,
                                period_length=assignment.constraints.period_length,
                                tag=assignment.tag,
                            )
                        )

        timetable_entries.sort(
            key=lambda entry: (
                self.day_to_index[entry.day],
                entry.slot,
                entry.class_name,
                entry.subject_name,
                entry.tag or "",
            )
        )
        return timetable_entries
    
    def solve(
        self,
        time_limit_sec: float = 60,
        seed: int = 42,
        workers: int = 8,
        *,
        fast: bool = False,
        gap_limit: float | None = None,
        patience: float = 5,
    ) -> GeneratedResponse:
        if not self.assignments:
            return GeneratedResponse(
                success=True,
                entries=[],
                violations=[],
                break_slots=self.break_slots,
            )

        if not self._built:
            self._build_and_apply_all_constraints()
            self._built = True

        solver = cp_model.CpSolver()
        if fast:
            # A short, bounded polish finds a usable timetable in seconds;
            # stopping at the literal first feasible assignment would leave
            # most periods empty, so cap the budget instead.
            time_limit_sec = min(float(time_limit_sec), 5.0)
        solver.parameters.max_time_in_seconds = max(0.01, float(time_limit_sec))
        # CP-SAT is only deterministic with a single worker; more workers trade
        # exact reproducibility for speed on large problems.
        solver.parameters.num_search_workers = max(1, workers)
        solver.parameters.random_seed = seed
        solver.parameters.symmetry_level = 2
        if gap_limit is not None:
            solver.parameters.relative_gap_limit = max(0.0, float(gap_limit))

        stopper = None
        if not fast and patience > 0:
            stopper = _PlateauStopper(solver, float(patience))

        try:
            status = solver.solve(self.model, stopper)
        finally:
            if stopper is not None:
                stopper.close()

        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return GeneratedResponse(
                success=False,
                entries=[],
                violations=[
                    ViolationOut(
                        error_msg=(
                            "Solver did not find a feasible timetable "
                            f"({solver.status_name(status)})"
                        ),
                        val=1,
                    )
                ],
                break_slots=self.break_slots,
            )
        
        timetable_entries = self._fetch_timetable_entries(solver=solver)
        error_slacks = self._fetch_error_slacks(solver=solver)

        # CP-SAT can be feasible with every period forced to zero (for example a
        # teacher unavailable all week). Report that as failure instead of an
        # empty timetable with success=True.
        if not timetable_entries:
            return GeneratedResponse(
                success=False,
                entries=[],
                violations=[
                    ViolationOut(
                        error_msg=(
                            "No periods could be scheduled with the current "
                            "constraints (check unavailable slots, break slots "
                            "and period lengths)"
                        ),
                        val=1,
                    )
                ],
                break_slots=self.break_slots,
            )

        return GeneratedResponse(
            success=True,
            entries=timetable_entries,
            violations=error_slacks,
            break_slots=self.break_slots,
        )
        


