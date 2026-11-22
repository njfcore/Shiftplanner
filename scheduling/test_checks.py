from datetime import date, time

from django.test import SimpleTestCase

from .solver import (
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    Assignment,
    EmployeeInfo,
    Problem,
    Requirement,
    ShiftInfo,
    check_assignments,
)

MORNING = ShiftInfo(1, "Morning", time(8, 0), time(16, 0))
NIGHT = ShiftInfo(3, "Night", time(22, 0), time(6, 0), is_night=True)

SAT = date(2026, 11, 14)
SUN = date(2026, 11, 15)
CASHIER = 10


def make_problem(employees=None, requirements=None, **kwargs):
    return Problem(
        employees=employees or [EmployeeInfo(1, "Ali"), EmployeeInfo(2, "Sara")],
        shifts=[MORNING, NIGHT],
        requirements=requirements if requirements is not None else [Requirement(SAT, 1, 1)],
        **kwargs,
    )


def codes(problem, assignments):
    return {v.code for v in check_assignments(problem, assignments)}


class CheckAssignmentsTests(SimpleTestCase):
    def test_valid_schedule_has_no_violations(self):
        self.assertEqual(check_assignments(make_problem(), [Assignment(1, SAT, 1)]), [])

    def test_assignment_while_unavailable(self):
        problem = make_problem(unavailable={(1, SAT, 1)})
        violations = check_assignments(problem, [Assignment(1, SAT, 1)])
        self.assertEqual([v.code for v in violations], ["unavailable"])
        self.assertEqual(violations[0].severity, SEVERITY_ERROR)

    def test_two_shifts_on_one_day(self):
        problem = make_problem(requirements=[Requirement(SAT, 1, 1), Requirement(SAT, 3, 1)])
        found = codes(problem, [Assignment(1, SAT, 1), Assignment(1, SAT, 3)])
        self.assertIn("double_booking", found)

    def test_weekly_limit_exceeded(self):
        employees = [EmployeeInfo(1, "Ali", max_weekly_minutes=8 * 60), EmployeeInfo(2, "Sara")]
        problem = make_problem(
            employees=employees,
            requirements=[Requirement(SAT, 1, 1), Requirement(SUN, 1, 1)],
        )
        found = codes(problem, [Assignment(1, SAT, 1), Assignment(1, SUN, 1)])
        self.assertIn("weekly_limit", found)

    def test_not_enough_rest_after_night_shift(self):
        problem = make_problem(requirements=[Requirement(SAT, 3, 1), Requirement(SUN, 1, 1)])
        found = codes(problem, [Assignment(1, SAT, 3), Assignment(1, SUN, 1)])
        self.assertIn("rest", found)

    def test_understaffed_shift_is_a_warning(self):
        problem = make_problem(requirements=[Requirement(SAT, 1, 2)])
        violations = check_assignments(problem, [Assignment(1, SAT, 1)])
        self.assertEqual([v.code for v in violations], ["understaffed"])
        self.assertEqual(violations[0].severity, SEVERITY_WARNING)

    def test_overstaffed_shift(self):
        found = codes(make_problem(), [Assignment(1, SAT, 1), Assignment(2, SAT, 1)])
        self.assertEqual(found, {"overstaffed"})

    def test_missing_skill(self):
        employees = [EmployeeInfo(1, "Ali"), EmployeeInfo(2, "Sara", frozenset({CASHIER}))]
        problem = make_problem(
            employees=employees, requirements=[Requirement(SAT, 1, 1, {CASHIER: 1})]
        )
        self.assertEqual(codes(problem, [Assignment(1, SAT, 1)]), {"missing_skill"})
        self.assertEqual(codes(problem, [Assignment(2, SAT, 1)]), set())

    def test_unknown_employee(self):
        found = codes(make_problem(), [Assignment(1, SAT, 1), Assignment(99, SAT, 1)])
        self.assertIn("unknown_employee", found)

    def test_shift_that_is_not_needed(self):
        found = codes(make_problem(), [Assignment(1, SAT, 1), Assignment(2, SUN, 1)])
        self.assertIn("unrequired_shift", found)