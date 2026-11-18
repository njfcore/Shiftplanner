from datetime import date, time

from django.test import SimpleTestCase

from .solver import ShiftInfo, Window, can_work

SAT = date(2026, 11, 14)  # weekday() == 5
MORNING = ShiftInfo(1, "Morning", time(8, 0), time(16, 0))
NIGHT = ShiftInfo(3, "Night", time(22, 0), time(6, 0), is_night=True)

SATURDAY, SUNDAY, FRIDAY = 5, 6, 4


class CanWorkTests(SimpleTestCase):
    def test_no_windows_means_free(self):
        self.assertTrue(can_work([], SAT, MORNING))

    def test_overlapping_unavailable_window_blocks(self):
        windows = [Window(SATURDAY, time(6, 0), time(10, 0), available=False)]
        self.assertFalse(can_work(windows, SAT, MORNING))

    def test_touching_unavailable_window_does_not_block(self):
        windows = [Window(SATURDAY, time(16, 0), time(20, 0), available=False)]
        self.assertTrue(can_work(windows, SAT, MORNING))

    def test_unavailable_window_on_other_weekday_is_ignored(self):
        windows = [Window(SUNDAY, time(8, 0), time(16, 0), available=False)]
        self.assertTrue(can_work(windows, SAT, MORNING))

    def test_available_window_must_contain_the_shift(self):
        inside = [Window(SATURDAY, time(7, 0), time(17, 0))]
        too_small = [Window(SATURDAY, time(9, 0), time(13, 0))]
        self.assertTrue(can_work(inside, SAT, MORNING))
        self.assertFalse(can_work(too_small, SAT, MORNING))

    def test_available_window_on_other_weekday_does_not_restrict(self):
        windows = [Window(SUNDAY, time(9, 0), time(13, 0))]
        self.assertTrue(can_work(windows, SAT, MORNING))

    def test_adjacent_available_windows_are_merged(self):
        windows = [
            Window(SATURDAY, time(8, 0), time(12, 0)),
            Window(SATURDAY, time(12, 0), time(18, 0)),
        ]
        self.assertTrue(can_work(windows, SAT, MORNING))

    def test_overnight_unavailable_window_spills_into_next_day(self):
        # Friday 22:00 to Saturday 10:00 overlaps the Saturday morning shift.
        windows = [Window(FRIDAY, time(22, 0), time(10, 0), available=False)]
        self.assertFalse(can_work(windows, SAT, MORNING))

    def test_night_shift_overlapping_next_day_window(self):
        # The night shift ends 06:00 on Sunday.
        blocked = [Window(SUNDAY, time(4, 0), time(8, 0), available=False)]
        free = [Window(SUNDAY, time(7, 0), time(9, 0), available=False)]
        self.assertFalse(can_work(blocked, SAT, NIGHT))
        self.assertTrue(can_work(free, SAT, NIGHT))