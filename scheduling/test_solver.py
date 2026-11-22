from datetime import date, time

from django.test import SimpleTestCase

from .solver import (
    EmployeeInfo,
    Problem,
    Requirement,
    ShiftInfo,
    Assignment,
    solve,
    week_start,
)

MORNING = ShiftInfo(1, "Morning", time(8, 0), time(16, 0))
EVENING = ShiftInfo(2, "Evening", time(16, 0), time(23, 0))
NIGHT = ShiftInfo(3, "Night", time(22, 0), time(6, 0), is_night=True)

SAT = date(2026, 11, 14)
SUN = date(2026, 11, 15)
MON = date(2026, 11, 16)
TUE = date(2026, 11, 17)
NEXT_MON = date(2026, 11, 23)
NEXT_SAT = date(2026, 11, 21)
FRI = date(2026, 11, 20)

CASHIER = 10


def make_employees(count, **kwargs):
    return [EmployeeInfo(id=i, name=f"E{i}", **kwargs) for i in range(1, count + 1)]


def total_missing(solution):
    return sum(s.missing_count for s in solution.shortages)


class WeekStartTests(SimpleTestCase):
    def test_saturday_starts_the_week(self):
        self.assertEqual(week_start(SAT), SAT)
        self.assertEqual(week_start(SUN), SAT)
        self.assertEqual(week_start(FRI), SAT)
        self.assertEqual(week_start(NEXT_SAT), NEXT_SAT)


class ShiftInfoTests(SimpleTestCase):
    def test_minutes_including_overnight(self):
        self.assertEqual(MORNING.minutes, 480)
        self.assertEqual(NIGHT.minutes, 480)


class CoverageTests(SimpleTestCase):
    def test_full_coverage_when_enough_staff(self):
        problem = Problem(
            employees=make_employees(3),
            shifts=[MORNING],
            requirements=[Requirement(d, 1, 2) for d in (SAT, SUN, MON)],
        )
        solution = solve(problem)
        self.assertTrue(solution.is_complete)
        for day in (SAT, SUN, MON):
            on_day = [a for a in solution.assignments if a.day == day]
            self.assertEqual(len(on_day), 2)
            self.assertEqual(len({a.employee_id for a in on_day}), 2)

    def test_no_requirements_gives_empty_solution(self):
        solution = solve(Problem(employees=make_employees(2), shifts=[MORNING], requirements=[]))
        self.assertEqual(solution.assignments, [])
        self.assertTrue(solution.is_complete)

    def test_shortage_is_reported_with_reason(self):
        problem = Problem(
            employees=make_employees(1),
            shifts=[MORNING],
            requirements=[Requirement(SAT, 1, 2)],
        )
        solution = solve(problem)
        self.assertEqual(len(solution.assignments), 1)
        self.assertEqual(total_missing(solution), 1)
        self.assertIn("Only 1", solution.shortages[0].reason)

    def test_nobody_available(self):
        problem = Problem(
            employees=make_employees(1),
            shifts=[MORNING],
            requirements=[Requirement(SAT, 1, 1)],
            unavailable={(1, SAT, 1)},
        )
        solution = solve(problem)
        self.assertEqual(solution.assignments, [])
        self.assertEqual(solution.shortages[0].reason, "No employee is available.")

    def test_unavailable_employee_is_not_assigned(self):
        problem = Problem(
            employees=make_employees(2),
            shifts=[MORNING],
            requirements=[Requirement(SAT, 1, 1)],
            unavailable={(1, SAT, 1)},
        )
        solution = solve(problem)
        self.assertTrue(solution.is_complete)
        self.assertEqual([a.employee_id for a in solution.assignments], [2])


class SkillTests(SimpleTestCase):
    def test_skill_minimum_is_respected(self):
        employees = [
            EmployeeInfo(1, "Skilled", frozenset({CASHIER})),
            EmployeeInfo(2, "B"),
            EmployeeInfo(3, "C"),
        ]
        problem = Problem(
            employees=employees,
            shifts=[MORNING],
            requirements=[Requirement(SAT, 1, 2, {CASHIER: 1})],
        )
        solution = solve(problem)
        self.assertTrue(solution.is_complete)
        self.assertIn(1, {a.employee_id for a in solution.assignments})

    def test_skill_shortage_is_reported(self):
        problem = Problem(
            employees=make_employees(2),
            shifts=[MORNING],
            requirements=[Requirement(SAT, 1, 1, {CASHIER: 1})],
        )
        solution = solve(problem)
        self.assertEqual(len(solution.assignments), 1)  # headcount still filled
        self.assertEqual(len(solution.shortages), 1)
        shortage = solution.shortages[0]
        self.assertEqual(shortage.skill_id, CASHIER)
        self.assertEqual(shortage.missing_count, 1)
        self.assertEqual(shortage.reason, "No available employee has this skill.")


class HourLimitTests(SimpleTestCase):
    def test_weekly_limit_is_enforced(self):
        problem = Problem(
            employees=make_employees(1, max_weekly_minutes=16 * 60),
            shifts=[MORNING],
            requirements=[Requirement(d, 1, 1) for d in (SAT, SUN, MON)],
        )
        solution = solve(problem)
        self.assertEqual(len(solution.assignments), 2)
        self.assertEqual(total_missing(solution), 1)

    def test_weekly_limit_resets_on_saturday(self):
        # Friday and the next Saturday are in different Iranian weeks.
        problem = Problem(
            employees=make_employees(1, max_weekly_minutes=8 * 60),
            shifts=[MORNING],
            requirements=[Requirement(FRI, 1, 1), Requirement(NEXT_SAT, 1, 1)],
        )
        solution = solve(problem)
        self.assertTrue(solution.is_complete)
        self.assertEqual(len(solution.assignments), 2)


class RuleTests(SimpleTestCase):
    def test_one_shift_per_day(self):
        problem = Problem(
            employees=make_employees(1),
            shifts=[MORNING, EVENING],
            requirements=[Requirement(SAT, 1, 1), Requirement(SAT, 2, 1)],
        )
        solution = solve(problem)
        self.assertEqual(len(solution.assignments), 1)
        self.assertEqual(total_missing(solution), 1)

    def test_minimum_rest_blocks_back_to_back_shifts(self):
        # Night ends 06:00 Sunday, morning starts 08:00: only 2 hours of rest.
        requirements = [Requirement(SAT, 3, 1), Requirement(SUN, 1, 1)]
        one = solve(Problem(make_employees(1), [MORNING, NIGHT], requirements))
        self.assertEqual(len(one.assignments), 1)
        self.assertEqual(total_missing(one), 1)

        two = solve(Problem(make_employees(2), [MORNING, NIGHT], requirements))
        self.assertTrue(two.is_complete)
        self.assertEqual({a.employee_id for a in two.assignments}, {1, 2})


class ValidationTests(SimpleTestCase):
    def test_duplicate_requirement_is_rejected(self):
        problem = Problem(
            employees=make_employees(1),
            shifts=[MORNING],
            requirements=[Requirement(SAT, 1, 1), Requirement(SAT, 1, 2)],
        )
        with self.assertRaises(ValueError):
            solve(problem)

    def test_unknown_shift_is_rejected(self):
        problem = Problem(
            employees=make_employees(1),
            shifts=[MORNING],
            requirements=[Requirement(SAT, 99, 1)],
        )
        with self.assertRaises(ValueError):
            solve(problem)


def count_by_employee(solution):
    counts = {}
    for a in solution.assignments:
        counts[a.employee_id] = counts.get(a.employee_id, 0) + 1
    return counts


class FairnessTests(SimpleTestCase):
    def test_night_shifts_are_shared_equally(self):
        problem = Problem(
            employees=make_employees(2),
            shifts=[NIGHT],
            requirements=[Requirement(d, 3, 1) for d in (SAT, SUN, MON, TUE)],
        )
        solution = solve(problem)
        self.assertTrue(solution.is_complete)
        self.assertEqual(count_by_employee(solution), {1: 2, 2: 2})

    def test_past_night_shifts_are_taken_into_account(self):
        employees = [EmployeeInfo(1, "E1", past_night_shifts=2), EmployeeInfo(2, "E2")]
        problem = Problem(
            employees=employees,
            shifts=[NIGHT],
            requirements=[Requirement(SAT, 3, 1), Requirement(SUN, 3, 1)],
        )
        solution = solve(problem)
        self.assertEqual(count_by_employee(solution), {2: 2})

    def test_friday_counts_as_a_holiday(self):
        employees = [EmployeeInfo(1, "E1", past_holiday_shifts=1), EmployeeInfo(2, "E2")]
        problem = Problem(
            employees=employees,
            shifts=[MORNING],
            requirements=[Requirement(FRI, 1, 1), Requirement(NEXT_MON, 1, 1)],
        )
        solution = solve(problem)
        friday = [a for a in solution.assignments if a.day == FRI]
        self.assertEqual([a.employee_id for a in friday], [2])

    def test_registered_holidays_are_used(self):
        employees = [EmployeeInfo(1, "E1", past_holiday_shifts=1), EmployeeInfo(2, "E2")]
        problem = Problem(
            employees=employees,
            shifts=[MORNING],
            requirements=[Requirement(SAT, 1, 1), Requirement(SUN, 1, 1)],
            holidays={SAT},
        )
        solution = solve(problem)
        saturday = [a for a in solution.assignments if a.day == SAT]
        self.assertEqual([a.employee_id for a in saturday], [2])

    def test_total_hours_are_shared(self):
        problem = Problem(
            employees=make_employees(2),
            shifts=[MORNING],
            requirements=[Requirement(d, 1, 1) for d in (SAT, SUN, MON, TUE)],
        )
        solution = solve(problem)
        self.assertEqual(count_by_employee(solution), {1: 2, 2: 2})

    def test_fairness_never_costs_coverage(self):
        employees = [EmployeeInfo(1, "E1", past_night_shifts=10), EmployeeInfo(2, "E2")]
        problem = Problem(
            employees=employees,
            shifts=[NIGHT],
            requirements=[Requirement(SAT, 3, 1)],
            unavailable={(2, SAT, 3)},
        )
        solution = solve(problem)
        self.assertTrue(solution.is_complete)
        self.assertEqual([a.employee_id for a in solution.assignments], [1])         


class FixedAssignmentTests(SimpleTestCase):
    def test_fixed_assignment_is_kept(self):
        problem = Problem(
            make_employees(2), [MORNING], [Requirement(SAT, 1, 1)], fixed={(2, SAT, 1)}
        )
        solution = solve(problem)
        self.assertEqual([a.employee_id for a in solution.assignments], [2])

    def test_fixed_assignment_overrides_unavailability(self):
        problem = Problem(
            make_employees(2), [MORNING], [Requirement(SAT, 1, 1)],
            unavailable={(2, SAT, 1)}, fixed={(2, SAT, 1)},
        )
        solution = solve(problem)
        self.assertTrue(solution.is_complete)
        self.assertEqual([a.employee_id for a in solution.assignments], [2])

    def test_fixed_assignment_counts_toward_coverage(self):
        problem = Problem(
            make_employees(3), [MORNING], [Requirement(SAT, 1, 2)], fixed={(1, SAT, 1)}
        )
        solution = solve(problem)
        self.assertTrue(solution.is_complete)
        self.assertEqual(len(solution.assignments), 2)
        self.assertIn(1, {a.employee_id for a in solution.assignments})

    def test_fixed_assignment_on_a_day_without_requirement_is_kept(self):
        problem = Problem(
            make_employees(2), [MORNING], [Requirement(SAT, 1, 1)], fixed={(1, SUN, 1)}
        )
        solution = solve(problem)
        self.assertTrue(solution.is_complete)
        self.assertIn(Assignment(1, SUN, 1), solution.assignments)

    def test_more_fixed_than_required_is_allowed(self):
        problem = Problem(
            make_employees(2), [MORNING], [Requirement(SAT, 1, 1)],
            fixed={(1, SAT, 1), (2, SAT, 1)},
        )
        solution = solve(problem)
        self.assertTrue(solution.is_complete)
        self.assertEqual(len(solution.assignments), 2)

    def test_fixed_hours_above_weekly_limit_only_block_more_work(self):
        problem = Problem(
            make_employees(1, max_weekly_minutes=8 * 60), [MORNING],
            [Requirement(MON, 1, 1)], fixed={(1, SAT, 1), (1, SUN, 1)},
        )
        solution = solve(problem)
        self.assertEqual(len(solution.assignments), 2)  # both fixed, none added
        self.assertEqual(total_missing(solution), 1)

    def test_fixed_pair_breaking_rest_rule_does_not_fail(self):
        problem = Problem(
            make_employees(1), [MORNING, NIGHT], [Requirement(TUE, 1, 1)],
            fixed={(1, SAT, 3), (1, SUN, 1)},
        )
        solution = solve(problem)
        self.assertIn(Assignment(1, SAT, 3), solution.assignments)
        self.assertIn(Assignment(1, SUN, 1), solution.assignments)

    def test_only_fixed_assignments_without_requirements(self):
        problem = Problem(make_employees(1), [MORNING], [], fixed={(1, SAT, 1)})
        solution = solve(problem)
        self.assertEqual(solution.assignments, [Assignment(1, SAT, 1)])

    def test_invalid_fixed_assignments_are_rejected(self):
        base = dict(employees=make_employees(1), shifts=[MORNING, NIGHT],
                    requirements=[Requirement(SAT, 1, 1)])
        with self.assertRaises(ValueError):
            solve(Problem(**base, fixed={(1, SAT, 99)}))  # unknown shift
        with self.assertRaises(ValueError):
            solve(Problem(**base, fixed={(9, SAT, 1)}))  # unknown employee
        with self.assertRaises(ValueError):
            solve(Problem(**base, fixed={(1, SAT, 1), (1, SAT, 3)}))  # same day        