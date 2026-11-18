from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

DEFAULT_MAX_WEEKLY_MINUTES = 44 * 60
DEFAULT_MIN_REST_MINUTES = 8 * 60


@dataclass(frozen=True)
class ShiftInfo:
    id: int
    name: str
    start_time: time
    end_time: time
    is_night: bool = False

    @property
    def duration(self):
        start = datetime.combine(date.min, self.start_time)
        end = datetime.combine(date.min, self.end_time)
        if end <= start:
            end += timedelta(days=1)
        return end - start

    @property
    def minutes(self):
        return int(self.duration.total_seconds() // 60)


@dataclass(frozen=True)
class EmployeeInfo:
    id: int
    name: str = ""
    skill_ids: frozenset = frozenset()
    max_weekly_minutes: int = DEFAULT_MAX_WEEKLY_MINUTES


@dataclass
class Requirement:
    """How many people a shift needs on a given day."""

    day: date
    shift_id: int
    required_count: int
    # skill_id -> minimum number of people with that skill
    skill_minimums: dict = field(default_factory=dict)


@dataclass
class Problem:
    employees: list
    shifts: list
    requirements: list
    # (employee_id, day, shift_id) combinations the employee cannot work
    # (approved leave, unavailable window, ...).
    unavailable: set = field(default_factory=set)
    min_rest_minutes: int = DEFAULT_MIN_REST_MINUTES
    time_limit_seconds: float = 10.0


@dataclass(frozen=True)
class Assignment:
    employee_id: int
    day: date
    shift_id: int


@dataclass(frozen=True)
class Shortage:
    """Staff the solver could not find. skill_id None means headcount."""

    day: date
    shift_id: int
    skill_id: int | None
    missing_count: int
    reason: str


@dataclass
class Solution:
    status: str  # "optimal" or "feasible" (time limit reached)
    assignments: list = field(default_factory=list)
    shortages: list = field(default_factory=list)

    @property
    def is_complete(self):
        return not self.shortages