from collections import defaultdict
from datetime import datetime, timedelta

from ortools.sat.python import cp_model

from .data import Assignment, Shortage, Solution, is_holiday

# Objective weights, from most to least important.
HEAD_WEIGHT = 1000  # a seat left empty
SKILL_WEIGHT = 100  # a skill minimum not met
HEAVY_WEIGHT = 30  # one unit of gap in night or holiday shifts between employees
WORK_WEIGHT = 3  # one hour of gap in total working hours between employees


class SolverError(Exception):
    """Raised when the solver cannot produce any solution."""


def week_start(day):
    """First day (Saturday) of the Iranian week that contains `day`."""
    return day - timedelta(days=(day.weekday() - 5) % 7)


def _validate(problem):
    shift_ids = {s.id for s in problem.shifts}
    if len(shift_ids) != len(problem.shifts):
        raise ValueError("Duplicate shift ids.")
    employee_ids = {e.id for e in problem.employees}
    if len(employee_ids) != len(problem.employees):
        raise ValueError("Duplicate employee ids.")
    seen = set()
    for req in problem.requirements:
        if req.shift_id not in shift_ids:
            raise ValueError(f"Unknown shift id {req.shift_id} in requirements.")
        key = (req.day, req.shift_id)
        if key in seen:
            raise ValueError(f"Duplicate requirement for {key}.")
        seen.add(key)
    fixed_days = set()
    for employee_id, day, shift_id in problem.fixed:
        if employee_id not in employee_ids:
            raise ValueError(f"Unknown employee id {employee_id} in fixed assignments.")
        if shift_id not in shift_ids:
            raise ValueError(f"Unknown shift id {shift_id} in fixed assignments.")
        if (employee_id, day) in fixed_days:
            raise ValueError("An employee has two fixed assignments on the same day.")
        fixed_days.add((employee_id, day))


def _explain(problem, requirement, skill_id, needed):
    eligible = [
        e
        for e in problem.employees
        if (e.id, requirement.day, requirement.shift_id) not in problem.unavailable
        and (skill_id is None or skill_id in e.skill_ids)
    ]
    with_skill = "" if skill_id is None else " with this skill"
    if not eligible:
        if skill_id is None:
            return "No employee is available."
        return "No available employee has this skill."
    if len(eligible) < needed:
        return f"Only {len(eligible)} available employee(s){with_skill} could work this shift."
    return (
        "Eligible employees are already assigned elsewhere or would exceed "
        "their weekly hour or rest limits."
    )


def _load_hours(shift):
    """Working load of a shift in whole hours (used for fairness only)."""
    return max(1, round(shift.minutes / 60))


def _balance_penalty(model, name, weight, carried, terms):
    """Penalty on the gap between the most and the least loaded employee.

    carried: employee_id -> load that employee already carries.
    terms: employee_id -> list of (coefficient, variable) this schedule adds.
    """
    if not terms:
        return []
    upper = max(carried.values(), default=0) + sum(
        coefficient for parts in terms.values() for coefficient, _var in parts
    )
    busiest = model.new_int_var(0, upper, f"{name}_max")
    lightest = model.new_int_var(0, upper, f"{name}_min")
    for emp_id, base in carried.items():
        total = base + sum(
            coefficient * var for coefficient, var in terms.get(emp_id, [])
        )
        model.add(total <= busiest)
        model.add(total >= lightest)
    return [weight * (busiest - lightest)]


def solve(problem):
    """Build the best possible schedule for the problem.

    Hard rules (never broken for new assignments): one shift per employee
    per day, no work when unavailable, weekly hour limit, minimum rest
    between shifts, never more people than a shift needs.
    Fixed assignments (manual edits) are always kept, even when they break
    those rules; they only limit what can be added around them.
    Coverage is soft: missing staff is minimised and reported as shortages.
    Among equally covered schedules, the solver prefers a fair one: night
    shifts, holiday shifts and total hours are spread evenly.
    """
    _validate(problem)
    fixed = set(problem.fixed)

    if not problem.requirements:
        return Solution(
            status="optimal",
            assignments=sorted(
                (Assignment(*key) for key in fixed),
                key=lambda a: (a.day, a.shift_id, a.employee_id),
            ),
        )

    shifts = {s.id: s for s in problem.shifts}
    employees = {e.id: e for e in problem.employees}

    model = cp_model.CpModel()

    # x[(employee, day, shift)] = 1 when the employee works that shift.
    # Variables exist where the shift is needed and the employee is free,
    # plus every fixed assignment (which is forced to 1).
    x = {}
    for req in problem.requirements:
        for emp in problem.employees:
            key = (emp.id, req.day, req.shift_id)
            if key not in problem.unavailable or key in fixed:
                x[key] = model.new_bool_var(f"x_{emp.id}_{req.day}_{req.shift_id}")
    for key in fixed:
        if key not in x:
            x[key] = model.new_bool_var(f"x_{key[0]}_{key[1]}_{key[2]}")
        model.add(x[key] == 1)

    fixed_per_slot = defaultdict(int)
    for _emp_id, day, shift_id in fixed:
        fixed_per_slot[(day, shift_id)] += 1

    # One shift per employee per day.
    per_day = defaultdict(list)
    for (emp_id, day, _shift_id), var in x.items():
        per_day[(emp_id, day)].append(var)
    for variables in per_day.values():
        if len(variables) > 1:
            model.add(sum(variables) <= 1)

    # Coverage with slack variables (the shortages).
    head_slack = {}
    skill_slack = {}
    penalties = []
    for req in problem.requirements:
        slot = (req.day, req.shift_id)
        slot_vars = [
            x[(e.id, req.day, req.shift_id)]
            for e in problem.employees
            if (e.id, req.day, req.shift_id) in x
        ]
        slack = model.new_int_var(0, req.required_count, f"missing_{req.day}_{req.shift_id}")
        if slot_vars:
            model.add(sum(slot_vars) + slack >= req.required_count)
            # Manual edits may put more people on a shift than required.
            model.add(sum(slot_vars) <= max(req.required_count, fixed_per_slot[slot]))
        else:
            model.add(slack >= req.required_count)
        head_slack[slot] = slack
        penalties.append(HEAD_WEIGHT * slack)

        for skill_id, minimum in req.skill_minimums.items():
            skilled = [
                x[(e.id, req.day, req.shift_id)]
                for e in problem.employees
                if skill_id in e.skill_ids and (e.id, req.day, req.shift_id) in x
            ]
            s_slack = model.new_int_var(
                0, minimum, f"missing_skill_{skill_id}_{req.day}_{req.shift_id}"
            )
            if skilled:
                model.add(sum(skilled) + s_slack >= minimum)
            else:
                model.add(s_slack >= minimum)
            skill_slack[(req.day, req.shift_id, skill_id)] = s_slack
            penalties.append(SKILL_WEIGHT * s_slack)

    # Weekly hour limit (Saturday-based week). Fixed assignments alone may
    # already exceed it; then they only block further shifts that week.
    fixed_minutes = defaultdict(int)
    for emp_id, day, shift_id in fixed:
        fixed_minutes[(emp_id, week_start(day))] += shifts[shift_id].minutes
    per_week = defaultdict(list)
    for (emp_id, day, shift_id), var in x.items():
        per_week[(emp_id, week_start(day))].append(shifts[shift_id].minutes * var)
    for (emp_id, week), terms in per_week.items():
        limit = max(employees[emp_id].max_weekly_minutes, fixed_minutes[(emp_id, week)])
        model.add(sum(terms) <= limit)

    # Minimum rest between two shifts of the same employee. A pair of fixed
    # assignments is skipped: the manager chose it, the checker will report it.
    def start_of(day, shift_id):
        return datetime.combine(day, shifts[shift_id].start_time)

    def end_of(day, shift_id):
        return start_of(day, shift_id) + shifts[shift_id].duration

    rest = timedelta(minutes=problem.min_rest_minutes)
    keys_by_employee = defaultdict(list)
    for key in x:
        keys_by_employee[key[0]].append(key)
    for keys in keys_by_employee.values():
        keys.sort(key=lambda k: start_of(k[1], k[2]))
        for i in range(len(keys)):
            first = keys[i]
            first_end = end_of(first[1], first[2])
            for j in range(i + 1, len(keys)):
                second = keys[j]
                if start_of(second[1], second[2]) - first_end >= rest:
                    break  # later shifts start even later
                if first in fixed and second in fixed:
                    continue
                model.add(x[first] + x[second] <= 1)

    # Fairness (soft): balance night shifts, holiday shifts and total hours.
    night_terms = defaultdict(list)
    holiday_terms = defaultdict(list)
    work_terms = defaultdict(list)
    for (emp_id, day, shift_id), var in x.items():
        shift = shifts[shift_id]
        work_terms[emp_id].append((_load_hours(shift), var))
        if shift.is_night:
            night_terms[emp_id].append((1, var))
        if is_holiday(day, problem.holidays):
            holiday_terms[emp_id].append((1, var))

    penalties += _balance_penalty(
        model, "nights", HEAVY_WEIGHT,
        {e.id: e.past_night_shifts for e in problem.employees}, night_terms,
    )
    penalties += _balance_penalty(
        model, "holidays", HEAVY_WEIGHT,
        {e.id: e.past_holiday_shifts for e in problem.employees}, holiday_terms,
    )
    penalties += _balance_penalty(
        model, "work", WORK_WEIGHT,
        {e.id: 0 for e in problem.employees}, work_terms,
    )

    model.minimize(sum(penalties))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = problem.time_limit_seconds
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise SolverError(f"Solver failed with status {solver.status_name(status)}.")

    assignments = sorted(
        (Assignment(e, d, s) for (e, d, s), var in x.items() if solver.value(var)),
        key=lambda a: (a.day, a.shift_id, a.employee_id),
    )

    shortages = []
    for req in problem.requirements:
        missing = solver.value(head_slack[(req.day, req.shift_id)])
        if missing:
            shortages.append(
                Shortage(
                    req.day, req.shift_id, None, missing,
                    _explain(problem, req, None, req.required_count),
                )
            )
        for skill_id, minimum in req.skill_minimums.items():
            missing = solver.value(skill_slack[(req.day, req.shift_id, skill_id)])
            if missing:
                shortages.append(
                    Shortage(
                        req.day, req.shift_id, skill_id, missing,
                        _explain(problem, req, skill_id, minimum),
                    )
                )

    return Solution(
        status="optimal" if status == cp_model.OPTIMAL else "feasible",
        assignments=assignments,
        shortages=shortages,
    )