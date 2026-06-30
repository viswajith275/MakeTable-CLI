from ortools.sat.python import cp_model
from dataclasses import dataclass
from models import TimeTableGenerationInput, WeekDay
import collections

@dataclass
class SlackTracker:
    variable: cp_model.IntVar
    error_msg: str
    weight: int


class TimeTableGenerator:
    def __init__(self, RawData: TimeTableGenerationInput) -> None:

        self.teachers_dict = {t.id: t for t in RawData.teachers}
        self.subjectss_dict = {s.id: s for s in RawData.subjects}
        self.rooms_dict    = {r.id: r for r in RawData.rooms}
        self.classes_dict  = {c.id: c for c in RawData.classes}

        self.model = cp_model.CpModel()
        self.days = [self.day_to_index[d] for d in RawData.project.days]
        self.slots = RawData.project.slots

        self.teacher_schedule = collections.defaultdict(lambda: collections.defaultdict(lambda: collections.defaultdict(list)))
        self.subject_schedule = collections.defaultdict(lambda: collections.defaultdict(lambda: collections.defaultdict(list)))
        self.room_schedule    = collections.defaultdict(lambda: collections.defaultdict(lambda: collections.defaultdict(list)))
        self.class_schedule   = collections.defaultdict(lambda: collections.defaultdict(lambda: collections.defaultdict(list)))
        self.class_subject_schedule = collections.defaultdict(lambda: collections.defaultdict(lambda: collections.defaultdict(list)))
        self.assignment_vars = collections.defaultdict(lambda: collections.defaultdict(dict))

        self.assignments = RawData.teacher_assignments

        self.index_to_day: dict[int, WeekDay] = {
            i: d for i, d in enumerate(WeekDay)
        }
        self.day_to_index: dict[WeekDay, int] = {
            d: i for i, d in enumerate(WeekDay)
        }
        self.vars = {}

        # Change values accordingly for better performaces (Dont forget)

        self.error_slacks: dict[str, SlackTracker] = {}
        self.slack_counter: int = 0
        self.silent_minimization: list = []

    def _create_slack(
        self, name: str, weight: int, error_msg: str, upper_bound: int
    ) -> cp_model.IntVar:
        self.slack_counter += 1

        unique_key = f"{name}_{self.slack_counter}"

        slack_var: cp_model.IntVar = self.model.new_int_var(
            lb=0, ub=upper_bound, name=f"slack_{unique_key}"
        )

        self.error_slacks[unique_key] = SlackTracker(
            variable=slack_var, weight=weight, error_msg=error_msg
        )

        return slack_var

    def _create_and_apply_variables(self):

        for assignment in self.assignments:
            
            class_ = self.classes_dict[assignment.class_id]
            room = self.rooms_dict[class_.room_id]

            if assignment.target_room_id != None:
                room = self.rooms_dict[assignment.target_room_id]

            for day in self.days:
                for slot in range(self.slots):

                    var = self.model.new_bool_var(f"assign_{assignment.id}_d{day}_s{slot}")
                        
                    self.teacher_schedule[assignment.teacher_idclea][day][slot].append(var)
                    self.room_schedule[room.id][day][slot].append(var)
                    self.class_schedule[class_.id][day][slot].append(var)
                    self.class_subject_vars[class_.id][assignment.subject_id][day].append(var)
                    self.assignment_vars[assignment.id][day][slot] = var
    

    def _apply_generic_conditions(self):

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

                    self.variables_at_this_time = schedule_grid[day][slot]
                    
                    room_capacity = self.rooms_dict[r_id].constraints.capacity 
                    
                    if room_capacity == 1:
                        self.model.add_at_most_one(variables_at_this_time)
                    else:
                        self.model.add(sum(variables_at_this_time) <= room_capacity)

        
    def _apply_teacher_constraints(self):
        pass

    def _teacher_max_per_day(self):
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
                    slack = self.create_slack(
                                name="teacher daily limit", error_msg=error_msg, weight=250, upper_bound=self.slots
                            )
                    self.model.add(sum(vars_for_this_day) <= max_limit + slack)


    def _teacher_max_per_week(self):
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
                slack = self.create_slack(
                            name="teacher weekly limit", error_msg=error_msg, weight=250, upper_bound=self.slots * len(self.days)
                        )
                    
                self.model.add(sum(vars_for_week) <= max_limit + slack)
    

    def _teacher_max_consecutive(self):
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
                        slack = self.create_slack(
                            name="teacher consecutive limit", error_msg=error_msg, weight=200, upper_bound=1
                        )

                        self.model.add(sum(vars_for_consecutive) <= max_limit + slack)
    

    def _apply_subject_constraints(self):
        pass

    
    def _subject_max_per_day(self):
        # max_per_day subject
        for c_id, subjects in self.class_subject_schedule.items():
            for sub_id, vars_for_subject in subjects.items():

                subject = self.subjectss_dict[sub_id]
                max_limit = subject.constraints.max_per_day

                if max_limit is not None:

                    for day in self.days:

                        vars_for_day = vars_for_subject[day]

                        error_msg =(
                                f"Max daily classes exceeded {subject.name} (limit: {max_limit})"
                            )
                        slack = self.create_slack(
                                name="subject daily limit", error_msg=error_msg, weight=200, upper_bound=self.slots
                            )

                        self.model.add(sum(vars_for_day) <= max_limit + slack)

    
    def _subject_min_per_day(self):
        # min_per_day subject
        for c_id, subjects in self.class_subject_schedule.items():
            for sub_id, vars_for_subject in subjects.items():

                subject = self.subjectss_dict[sub_id]
                min_limit = subject.constraints.min_per_day

                if min_limit is not None:

                    for day in self.days:

                        vars_for_day = vars_for_subject[day]

                        error_msg =(
                                f"Min daily classes didnt meet {subject.name} (required: {min_limit})"
                            )
                        slack = self.create_slack(
                                name="subject daily required", error_msg=error_msg, weight=200, upper_bound=self.slots
                            )

                        self.model.add(sum(vars_for_day) >= min_limit - slack)

    
    def _subject_max_per_week(self):
        # max_per_week subject
        for c_id, subjects in self.class_subject_schedule.items():
            for sub_id, vars_for_subject in subjects.items():

                subject = self.subjectss_dict[sub_id]
                max_limit = subject.constraints.max_per_week

                if max_limit is not None:
                    
                    vars_for_week = []

                    for day in self.days:
                        vars_for_week.extend(vars_for_subject[day])

                    error_msg =(
                                f"Max weekly classes exceeded {subject.name} (limit: {max_limit})"
                            )
                    slack = self.create_slack(
                                name="subject weekly limit", error_msg=error_msg, weight=200, upper_bound=self.slots * len(self.days)
                            )

                    self.model.add(sum(vars_for_week) <= max_limit + slack)


    def _subject_min_per_week(self):
        # min_per_week subject
        for c_id, subjects in self.class_subject_schedule.items():
            for sub_id, vars_for_subject in subjects.items():

                subject = self.subjectss_dict[sub_id]
                min_limit = subject.constraints.min_per_week

                if min_limit is not None:
                    
                    vars_for_week = []

                    for day in self.days:
                        vars_for_week.extend(vars_for_subject[day])

                    error_msg =(
                                f"Min weekly classes didnt meet {subject.name} (limit: {min_limit})"
                            )
                    slack = self.create_slack(
                                name="subject weekly required", error_msg=error_msg, weight=200, upper_bound=self.slots * len(self.days)
                            )

                    self.model.add(sum(vars_for_week) >= min_limit - slack)

        
    def _subject_max_consecutive(self):
        for c_id, subjects in self.class_subject_schedule.items():
            for sub_id, vars_for_subject in subjects.items():

                subject = self.subjectss_dict[sub_id]
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

    def _subject_min_consecutive(self):
        pass

    def _subject_morning_tendency(self):
        pass    

    def _apply_teacher_assignment_constraints(self):
        pass

    def _teacher_assignment_first_slot_days(self):
        pass

    def solve_and_output(self):
        pass