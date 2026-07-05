from ortools.sat.python import cp_model
from dataclasses import dataclass
from src.models import TimeTableGenerationInput, WeekDay, TimeTableEntryOutput, ViolationOut, GeneratedResponse
import collections
from typing import List, Dict

@dataclass
class SlackTracker:
    variable: cp_model.IntVar
    error_msg: str
    weight: int


class TimeTableGenerator:
    def __init__(self, RawData: TimeTableGenerationInput) -> None:

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
        self.class_subject_schedule = collections.defaultdict(lambda: collections.defaultdict(lambda: collections.defaultdict(list)))
        self.assignment_schedule = collections.defaultdict(lambda: collections.defaultdict(dict))

        self.assignments = RawData.teacher_assignments

        self.MorningTendencyValues = {
            'Low':-1,
            'Med':1,
            'High':2
        }

        self.index_to_day: dict[int, WeekDay] = {
            i: d for i, d in enumerate(WeekDay)
        }
        self.day_to_index: dict[WeekDay, int] = {
            d: i for i, d in enumerate(WeekDay)
        }
        self.days = [self.day_to_index[d] for d in RawData.project.days]

        # Change values accordingly for better performaces (Dont forget)

        self.error_slacks: Dict[str, SlackTracker] = {}
        self.slack_counter: int = 0
        self.silent_minimization: list = []

    
    def _minimize_gaps(self, label: str, weight: int = 3) -> None:
        # minimize gap in teachers/classes

        if label == "teachers":
            schedule_dict = self.teacher_schedule
        elif label == "classes":
            schedule_dict = self.class_schedule

        for entity_id, vars_for_entity in schedule_dict.items(): # Either classes/teachers
            for day in self.days:
                slot_occupied = {}
                for slot, var_list in vars_for_entity[day].items():
                    if len(var_list) == 1:
                        slot_occupied[slot] = var_list[0]
                    else:
                        occ = self.model.new_bool_var(f"occ_{label}_{entity_id}_{day}_{slot}")
                        self.model.add_max_equality(occ, *var_list)
                        slot_occupied[slot] = occ

                slot_vars = [slot_occupied[s] for s in sorted(slot_occupied)]
                n = len(slot_vars)
                if n < 3:
                    continue

                for i in range(1, n - 1):
                    is_gap = self.model.new_bool_var(f"gap_{label}_{entity_id}_{day}_{i}")
                    occupied_before = self.model.new_bool_var(f"before_{label}_{entity_id}_{day}_{i}")
                    occupied_after = self.model.new_bool_var(f"after_{label}_{entity_id}_{day}_{i}")

                    if slot_vars[:i]:
                        self.model.add_max_equality(occupied_before, *slot_vars[:i])
                    else:
                        self.model.add(occupied_before == 0)

                    if slot_vars[i + 1:]:
                        self.model.add_max_equality(occupied_after, *slot_vars[i + 1:])
                    else:
                        self.model.add(occupied_after == 0)

                    self.model.add_bool_and([slot_vars[i].Not(), occupied_before, occupied_after]).OnlyEnforceIf(is_gap)
                    self.model.add_bool_or([slot_vars[i], occupied_before.Not(), occupied_after.Not()]).OnlyEnforceIf(is_gap.Not())
                    self.silent_minimization.append(weight * is_gap)


    def _create_slack(self, name: str, weight: int, error_msg: str, upper_bound: int, ) -> cp_model.IntVar:
        self.slack_counter += 1

        unique_key = f"{name}_{self.slack_counter}"

        slack_var: cp_model.IntVar = self.model.new_int_var(
            lb=0, ub=upper_bound, name=f"slack_{unique_key}"
        )

        self.error_slacks[unique_key] = SlackTracker(
            variable=slack_var, weight=weight, error_msg=error_msg
        )

        return slack_var

    def _create_and_apply_variables(self) -> None:

        for assignment in self.assignments:
            
            class_ = self.classes_dict[assignment.class_id]
            room = self.rooms_dict[class_.room_id]

            if assignment.target_room_id != None:
                room = self.rooms_dict[assignment.target_room_id]

            for day in self.days:
                for slot in range(self.slots):

                    var = self.model.new_bool_var(f"assign_{assignment.id}_d{day}_s{slot}")
                        
                    self.teacher_schedule[assignment.teacher_id][day][slot].append(var)
                    self.room_schedule[room.id][day][slot].append(var)
                    self.class_schedule[class_.id][day][slot].append(var)
                    self.class_subject_schedule[class_.id][assignment.subject_id][day].append(var)
                    self.assignment_schedule[assignment.id][day][slot] = var
    

    def _apply_generic_conditions(self) -> None:

        for day in self.days:
            for slot in range(self.slots):
                
                # MAIN HARD CONSTRAINTS
                for t_id, schedule_grid in self.teacher_schedule.items():

                    variables_at_this_time = schedule_grid[day][slot]
                    self.model.add_at_most_one(variables_at_this_time)
                    
                for c_id, schedule_grid in self.class_schedule.items():

                    variables_at_this_time = schedule_grid[day][slot]
                    self.model.add_at_most_one(variables_at_this_time)
                    
                for r_id, schedule_grid in self.room_schedule.items():

                    variables_at_this_time = schedule_grid[day][slot]
                    
                    room_capacity = self.rooms_dict[r_id].constraints.capacity 
                    
                    if room_capacity == 1:
                        self.model.add_at_most_one(variables_at_this_time)
                    else:
                        self.model.add(sum(variables_at_this_time) <= room_capacity)


    def _teacher_max_per_day(self) -> None:
        # max_per_day teacher
        for t_id, schedule_grid in self.teacher_schedule.items():
            teacher = self.teachers_dict[t_id]
            max_limit = teacher.constraints.max_per_day
            
            if max_limit is not None and max_limit > 0:

                for day in self.days:

                    vars_for_this_day = []

                    for slot in range(self.slots):
                        vars_for_this_day.extend(schedule_grid[day][slot])
                

                    error_msg = (
                                f"Max daily classes exceeded {teacher.name} (limit: {max_limit})"
                            )
                    slack = self._create_slack(
                                name="teacher daily limit", error_msg=error_msg, weight=250, upper_bound=self.slots
                            )
                    self.model.add(sum(vars_for_this_day) <= max_limit + slack)


    def _teacher_max_per_week(self) -> None:
        # max_per_week teacher
        for t_id, schedule_grid in self.teacher_schedule.items():

            teacher = self.teachers_dict[t_id]
            max_limit = teacher.constraints.max_per_week
            
            if max_limit is not None and max_limit > 0:
                
                vars_for_week = []

                for day in self.days:
                    for slot in range(self.slots):
                        vars_for_week.extend(schedule_grid[day][slot])
                
                error_msg = (
                            f"Max weekly classes exceeded {teacher.name} (limit: {max_limit})"
                        )
                slack = self._create_slack(
                            name="teacher weekly limit", error_msg=error_msg, weight=250, upper_bound=self.slots * len(self.days)
                        )
                    
                self.model.add(sum(vars_for_week) <= max_limit + slack)
    

    def _teacher_max_consecutive(self) -> None:
        # max_consecutive teacher
        for t_id, schedule_grid in self.teacher_schedule.items():

            teacher = self.teachers_dict[t_id]
            max_limit = teacher.constraints.max_consecutive

            if max_limit is not None and max_limit > 0:

                if self.slots < max_limit:
                    continue

                max_window_size = max_limit + 1

                for day in self.days:
                    for start_slot in range(self.slots - max_window_size + 1):

                        vars_for_consecutive = []
                    
                        for slot in range(start_slot, start_slot + max_window_size):

                            vars_for_consecutive.extend(schedule_grid[day][slot])
                        
                        error_msg =(
                            f"Max consecutive classes exceeded {teacher.name} (limit: {max_limit})"
                        )
                        slack = self._create_slack(
                            name="teacher consecutive limit", error_msg=error_msg, weight=200, upper_bound=1
                        )

                        self.model.add(sum(vars_for_consecutive) <= max_limit + slack)


    def _teacher_balance_daily_load(self, weight: int = 5):
        # balance_periods teacher
        for teacher_id, vars_for_teacher in self.teacher_schedule.items():
            daily_counts = []
            for day in self.days:
                vars_for_day = []
                for slot in range(self.slots):
                    vars_for_day.extend(vars_for_teacher[day][slot])
                if not vars_for_day:
                    continue
                count = self.model.new_int_var(0, len(vars_for_day), f"load_{teacher_id}_{day}")
                self.model.add(count == sum(vars_for_day))
                daily_counts.append(count)

            if len(daily_counts) < 2:
                continue

            max_load = self.model.new_int_var(0, self.slots, f"maxload_{teacher_id}")
            min_load = self.model.new_int_var(0, self.slots, f"minload_{teacher_id}")
            self.model.add_max_equality(max_load, daily_counts)
            self.model.add_min_equality(min_load, daily_counts)

            spread = self.model.new_int_var(0, self.slots, f"spread_{teacher_id}")
            self.model.add(spread == max_load - min_load)
            self.silent_minimization.append(weight * spread)

    
    def _subject_max_per_day(self) -> None:
        # max_per_day subject
        for c_id, subjects in self.class_subject_schedule.items():
            for sub_id, vars_for_subject in subjects.items():

                subject = self.subjects_dict[sub_id]
                max_limit = subject.constraints.max_per_day

                if max_limit is not None:

                    for day in self.days:

                        vars_for_day = vars_for_subject[day]

                        error_msg =(
                                f"Max daily classes exceeded {subject.name} (limit: {max_limit})"
                            )
                        slack = self._create_slack(
                                name="subject daily limit", error_msg=error_msg, weight=200, upper_bound=self.slots
                            )

                        self.model.add(sum(vars_for_day) <= max_limit + slack)

    
    def _subject_min_per_day(self) -> None:
        # min_per_day subject
        for c_id, subjects in self.class_subject_schedule.items():
            for sub_id, vars_for_subject in subjects.items():

                subject = self.subjects_dict[sub_id]
                min_limit = subject.constraints.min_per_day

                if min_limit is not None and min_limit > 1:

                    for day in self.days:

                        vars_for_day = vars_for_subject[day]

                        error_msg =(
                                f"Min daily classes didnt meet {subject.name} (required: {min_limit})"
                            )
                        slack = self._create_slack(
                                name="subject daily required", error_msg=error_msg, weight=200, upper_bound=self.slots
                            )

                        self.model.add(sum(vars_for_day) >= min_limit - slack)

    
    def _subject_max_per_week(self) -> None:
        # max_per_week subject
        for c_id, subjects in self.class_subject_schedule.items():
            for sub_id, vars_for_subject in subjects.items():

                subject = self.subjects_dict[sub_id]
                max_limit = subject.constraints.max_per_week

                if max_limit is not None:
                    
                    vars_for_week = []

                    for day in self.days:
                        vars_for_week.extend(vars_for_subject[day])

                    error_msg =(
                                f"Max weekly classes exceeded {subject.name} (limit: {max_limit})"
                            )
                    slack = self._create_slack(
                                name="subject weekly limit", error_msg=error_msg, weight=200, upper_bound=self.slots * len(self.days)
                            )

                    self.model.add(sum(vars_for_week) <= max_limit + slack)


    def _subject_min_per_week(self) -> None:
        # min_per_week subject
        for c_id, subjects in self.class_subject_schedule.items():
            for sub_id, vars_for_subject in subjects.items():

                subject = self.subjects_dict[sub_id]
                min_limit = subject.constraints.min_per_week

                if min_limit is not None:
                    
                    vars_for_week = []

                    for day in self.days:
                        vars_for_week.extend(vars_for_subject[day])

                    error_msg =(
                                f"Min weekly classes didnt meet {subject.name} (limit: {min_limit})"
                            )
                    slack = self._create_slack(
                                name="subject weekly required", error_msg=error_msg, weight=200, upper_bound=self.slots * len(self.days)
                            )

                    self.model.add(sum(vars_for_week) >= min_limit - slack)

        
    def _subject_max_consecutive(self) -> None:
        for c_id, subjects in self.class_subject_schedule.items():
            for sub_id, vars_for_subject in subjects.items():

                subject = self.subjects_dict[sub_id]
                max_limit = subject.constraints.max_consecutive

                if max_limit is not None:

                    for day in self.days:

                        vars_for_day = vars_for_subject[day]

                        for i in range(len(vars_for_day) - max_limit + 1):
                            error_msg = f"Max consecutive classes exceeded for {subject.name} (limit: {max_limit})"
                            slack = self._create_slack(
                                name="subject maximum consecutive class",
                                error_msg=error_msg,
                                weight=250,
                                upper_bound=1
                            )

                            self.model.add(sum(vars_for_day[i : i + max_limit + 1]) <= max_limit + slack)

    def _subject_min_consecutive(self) -> None:
        # min_consecutive subject
        for c_id, subjects in self.class_subject_schedule.items():
            for sub_id, var_for_subject in subjects.items():

                subject = self.subjects_dict[sub_id]
                min_limit = subject.constraints.min_consecutive

                if min_limit is not None:
                    for day in self.days:

                        vars_for_day = var_for_subject[day]
                        len_of_var = len(vars_for_day)

                        if len_of_var == 0:
                            continue

                        error_msg = (
                            f"Min consecutive classes didnt met for {subject.name} (required: {min_limit})"
                        )

                        for i in range(len_of_var):

                            is_start = self.model.new_bool_var(
                                f"blk_start_{c_id}_{sub_id}_{day}_{i}"
                            )

                            curr = vars_for_day[i]
                            if i == 0:

                                self.model.add(is_start == curr)

                            else:

                                prev = vars_for_day[i - 1]

                                self.model.add_bool_and( [curr, prev.Not()] ).OnlyEnforceIf(is_start)

                                self.model.add_bool_or( [curr.Not(), prev] ).OnlyEnforceIf(is_start.Not())


                            for k in range(1, min_limit):
                                j = i + k
                                slack = self._create_slack(
                                    name="subject min consecutive",
                                    error_msg=error_msg,
                                    weight=270,
                                    upper_bound=1,
                                )
                                if j < len_of_var:

                                    self.model.add( vars_for_day[j] + slack >= 1 ).OnlyEnforceIf(is_start)

                                else:

                                    self.model.add(slack >= 1).OnlyEnforceIf(is_start)    

    def _subject_morning_tendency(self) -> None:
        # morning_tendency subject
        for c_id, subjects in self.class_subject_schedule.items():
            for sub_id, vars_for_subject in subjects.items():

                subject = self.subjects_dict[sub_id]
                tendency = subject.constraints.morning_tendency

                if tendency is not None:

                    multiplier = self.MorningTendencyValues.get(tendency.value)

                    for day in self.days:
                        vars_for_day = vars_for_subject[day]
                        n = len(vars_for_day)

                        if n == 0:
                            continue

                        for slot_index, var in enumerate(vars_for_day):

                            lateness = slot_index / (n - 1) if n > 1 else 0
                            if lateness == 0:
                                continue

                            cost = multiplier * lateness

                            scaled_cost = round(cost * 10)

                            self.silent_minimization.append(scaled_cost * var)


    def _subject_slot_variety(self, weight: int = 2):
        # slot_variety subject
        for c_id, subjects in self.class_subject_schedule.items():
            for sub_id, vars_for_subject in subjects.items():

                for slot_index in range(self.slots):
                    same_slot_flags = []
                    for day in self.days:
                        vars_for_day = vars_for_subject[day]
                        if slot_index < len(vars_for_day):
                            same_slot_flags.append(vars_for_day[slot_index])

                    if len(same_slot_flags) < 2:
                        continue  # At least 2 days to call it repetiv=tive

                    repeat_count = self.model.new_int_var( 0, len(same_slot_flags), f"repeat_{c_id}_{sub_id}_{slot_index}" )

                    self.model.add(repeat_count == sum(same_slot_flags))
                    
                    excess = self.model.new_int_var(0, len(same_slot_flags), f"excess_{c_id}_{sub_id}_{slot_index}")

                    self.model.add(excess >= repeat_count - 1)

                    self.model.add(excess >= 0)

                    self.silent_minimization.append(weight * excess)

    def _teacher_assignment_first_slot_days(self) -> None:

        FIRST_SLOT_INDEX = 0 # Using this cause we can reuse this to add new constraints later

        # first_slot_days teacher_assignment
        for assigment in self.assignments:
            first_slot_days = assigment.constraints.first_slot_days
            if not first_slot_days:
                continue

            vars_for_assignment = self.assignment_schedule.get(assigment.id)
            if vars_for_assignment is None:
                continue

            for day in first_slot_days:

                day = self.day_to_index.get(day)
                if day is None or day not in self.days:
                    continue

                vars_for_day = vars_for_assignment.get(day)
                if not vars_for_day or FIRST_SLOT_INDEX not in vars_for_day:
                    continue

                first_slot_var = vars_for_day[FIRST_SLOT_INDEX]

                error_msg = (
                    f"Assignment {assigment.id} required at first slot"
                )
                slack = self._create_slack(
                    name="assignment first slot required",
                    error_msg=error_msg,
                    weight=250,
                    upper_bound=1,
                )

                self.model.add(first_slot_var + slack >= 1)
    

    def _apply_class_constraints(self) -> None:

        # Applying class constraints
        self._minimize_gaps(label="classes", weight=3)

    def _apply_teacher_constraints(self) -> None:

        # Applying teacher constraints
        self._teacher_max_per_day()
        self._teacher_max_per_week()
        self._teacher_max_consecutive()
        self._teacher_balance_daily_load(weight=5)
        self._minimize_gaps(label="teachers", weight=5)

    def _apply_subject_constraints(self) -> None:

        # Applying subject constraints
        self._subject_max_per_day()
        self._subject_min_per_day()
        self._subject_max_per_week()
        self._subject_min_per_week()
        self._subject_max_consecutive()
        self._subject_min_consecutive()
        self._subject_morning_tendency()
        self._subject_slot_variety(weight=2)

    def _apply_teacher_assignment_constraints(self) -> None:

        # Apply assignment constraints
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

        self._create_and_apply_variables()
        self._apply_generic_conditions()
        self._apply_class_constraints()
        self._apply_teacher_constraints()
        self._apply_subject_constraints()
        self._apply_teacher_assignment_constraints()
        self._apply_minimization()

    
    def _fetch_error_slacks(self, solver: cp_model.CpSolver) -> List[ViolationOut]:

        slack_errors = []
        for slack in self.error_slacks.values():

            value = solver.value(slack.variable)

            if value > 0:
                slack_errors.append(ViolationOut(error_msg=slack.error_msg, val=value))

        return slack_errors
    
    
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

                        timetable_entries.append(TimeTableEntryOutput(assignment_id=assignment.id, class_name=class_.name, teacher_name=self.teachers_dict[assignment.teacher_id].name, subject_name=self.subjects_dict[assignment.subject_id].name, room_name=self.rooms_dict[room_id].name, day=self.index_to_day[day], slot=slot+1))
            
        return timetable_entries
    
    def solve(self, time_limit_sec: float = 60, seed: int = 42) -> GeneratedResponse: # For now return nothing change return type to timetable response

        self._build_and_apply_all_constraints()

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit_sec
        solver.parameters.num_search_workers = 8
        solver.parameters.random_seed = seed
        solver.parameters.symmetry_level = 2

        status = solver.solve(self.model)

        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return GeneratedResponse(success=False, entries=[], violations=[])
        
        timetable_entries = self._fetch_timetable_entries(solver=solver)
        error_slacks = self._fetch_error_slacks(solver=solver)

        return GeneratedResponse(
            success=True,
            entries=timetable_entries,
            violations=error_slacks,
        )
        


