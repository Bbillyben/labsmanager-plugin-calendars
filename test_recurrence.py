"""Focused native recurrence, exception, permission and publication contracts."""

from datetime import datetime, timedelta, timezone as utc
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIRequestFactory, force_authenticate

from . import api
from .models import Calendar, CalendarAccess, Event, EventOccurrenceException
from .native import effective_employees, native_occurrences
from .recurrence import RecurrenceError, occurrence_starts
from staff.models import Employee


class NativeRecurrenceTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("recurrence-owner")
        self.viewer = get_user_model().objects.create_user("recurrence-viewer")
        self.calendar = Calendar.objects.create(name="Recurring", created_by=self.owner)
        CalendarAccess.objects.create(calendar=self.calendar, user=self.viewer, level="view")
        self.first = datetime(2026, 10, 19, 8, tzinfo=utc.utc)
        self.event = Event.objects.create(
            calendar=self.calendar, created_by=self.owner, title="Weekly", location="Room A",
            start=self.first, end=self.first + timedelta(hours=1),
            recurrence_rule="FREQ=WEEKLY;COUNT=5", recurrence_timezone="Europe/Paris",
        )

    def call(self, view, method, user=None, data=None, period=None, **kwargs):
        params = period or ("2026-10-18T00:00:00Z", "2026-11-25T00:00:00Z")
        request = getattr(APIRequestFactory(), method)(
            "/?" + urlencode({"start": params[0], "end": params[1]}), data=data, format="json")
        force_authenticate(request, user=user or self.owner)
        return view.as_view()(request, pk=self.calendar.pk, **kwargs)

    def rows(self, period=None):
        response = self.call(api.Events, "get", period=period)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data["results"]

    def test_weekly_window_and_stable_original_identity_across_dst(self):
        rows = self.rows()
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]["series_id"], self.event.pk)
        self.assertEqual(rows[0]["recurrence_id"], self.first.isoformat())
        self.assertEqual(rows[1]["start"], "2026-10-26T09:00:00+00:00")
        self.assertEqual(len(self.rows(("2026-11-01T00:00:00Z", "2026-11-25T00:00:00Z"))), 3)

    def test_all_day_series_detail_uses_declared_zone(self):
        first = datetime(2026, 10, 24, 15, tzinfo=utc.utc)  # 25 October in Tokyo.
        series = Event.objects.create(
            calendar=self.calendar, created_by=self.owner, title="Tokyo days",
            start=first, end=first + timedelta(days=2), all_day=True,
            recurrence_rule="FREQ=DAILY;COUNT=2", recurrence_timezone="Asia/Tokyo",
        )
        detail = self.call(api.EventDetail, "get", event_id=series.pk)
        self.assertEqual((detail.data["start"], detail.data["end"]), ("2026-10-25", "2026-10-27"))

    def test_override_inheritance_move_and_restore(self):
        rid = self.first.isoformat()
        moved = self.call(api.EventOccurrenceDetail, "patch", data={
            "start": "2026-12-01T10:00:00Z", "end": "2026-12-01T11:00:00Z", "location": "",
        }, event_id=self.event.pk, recurrence_id=rid)
        self.assertEqual(moved.status_code, 200, moved.data)
        self.assertEqual(len(self.rows(("2026-10-18T00:00:00Z", "2026-10-20T00:00:00Z"))), 0)
        moved_rows = self.rows(("2026-12-01T00:00:00Z", "2026-12-02T00:00:00Z"))
        self.assertEqual([(row["title"], row["location"]) for row in moved_rows], [("Weekly", "")])
        self.assertEqual(moved_rows[0]["overrides"], {
            "title": False, "description": False, "location": True,
            "event_type": False, "timeslot": True, "employees": False,
        })
        self.assertEqual(moved_rows[0]["recurrence_id"], rid)
        self.call(api.EventDetail, "patch", data={"title": "Updated"}, event_id=self.event.pk)
        self.assertEqual(self.rows(("2026-12-01T00:00:00Z", "2026-12-02T00:00:00Z"))[0]["title"], "Updated")
        restored = self.call(api.EventOccurrenceRestore, "post", event_id=self.event.pk, recurrence_id=rid)
        self.assertEqual(restored.status_code, 200)
        self.assertFalse(EventOccurrenceException.objects.filter(event=self.event).exists())
        self.assertEqual(len(self.rows(("2026-10-18T00:00:00Z", "2026-10-20T00:00:00Z"))), 1)

    def test_cancel_permissions_and_invalid_identity(self):
        rid = self.first.isoformat()
        self.assertEqual(self.call(api.EventOccurrenceDetail, "delete", user=self.viewer,
                                   event_id=self.event.pk, recurrence_id=rid).status_code, 403)
        self.assertEqual(self.call(api.EventOccurrenceDetail, "delete", event_id=self.event.pk,
                                   recurrence_id="2026-10-20T08:00:00Z").status_code, 400)
        self.assertEqual(self.call(api.EventOccurrenceDetail, "delete", event_id=self.event.pk,
                                   recurrence_id=rid).status_code, 200)
        self.assertEqual(len(self.rows(("2026-10-18T00:00:00Z", "2026-10-20T00:00:00Z"))), 0)

    def test_structural_change_needs_fresh_confirmation(self):
        rid = self.first.isoformat()
        self.call(api.EventOccurrenceDetail, "patch", data={"location": "Room B"},
                  event_id=self.event.pk, recurrence_id=rid)
        change = {"recurrence_rule": "FREQ=WEEKLY;COUNT=6"}
        refused = self.call(api.EventDetail, "patch", data=change, event_id=self.event.pk)
        self.assertEqual((refused.status_code, refused.data["code"]), (409, "exceptions_reset_required"))
        stale = self.call(api.EventDetail, "patch", data={**change, "reset_exceptions": True,
                                                          "exceptions_revision": refused.data["exceptions_revision"] - 1},
                          event_id=self.event.pk)
        self.assertEqual(stale.status_code, 409)
        accepted = self.call(api.EventDetail, "patch", data={**change, "reset_exceptions": True,
                                                             "exceptions_revision": refused.data["exceptions_revision"]},
                             event_id=self.event.pk)
        self.assertEqual(accepted.status_code, 200, accepted.data)
        self.assertFalse(EventOccurrenceException.objects.filter(event=self.event).exists())

    def test_monthly_and_yearly_rules_and_invalid_rule(self):
        for rule in ("FREQ=DAILY;INTERVAL=2;COUNT=3", "FREQ=MONTHLY;COUNT=3;BYMONTHDAY=19",
                     "FREQ=MONTHLY;COUNT=3;BYDAY=MO;BYSETPOS=3", "FREQ=YEARLY;COUNT=2"):
            result = self.call(api.EventDetail, "patch", data={"recurrence_rule": rule}, event_id=self.event.pk)
            self.assertEqual(result.status_code, 200, (rule, result.data))
        invalid = self.call(api.EventDetail, "patch", data={"recurrence_rule": "FREQ=SECONDLY"},
                            event_id=self.event.pk)
        self.assertEqual(invalid.status_code, 400)

    def test_published_occurrences_have_stable_identity_and_effective_values(self):
        from .Calendars import Calendars
        rid = self.first.isoformat()
        self.call(api.EventOccurrenceDetail, "patch", data={"location": "Room B"},
                  event_id=self.event.pk, recurrence_id=rid)
        published = Calendars().get_published_events(self.viewer, str(self.calendar.pk),
                                                      self.first - timedelta(days=1), self.first + timedelta(days=15))
        self.assertEqual(len(published), 3)
        self.assertEqual(published[0].metadata["location"], "Room B")
        self.assertEqual(published[0].metadata["recurrence_id"], rid)
        self.assertEqual(published[0].id, f"{self.event.pk}:{rid}")

    def test_rdate_exdate_and_bounded_daily_rule(self):
        changed = self.call(api.EventDetail, "patch", data={
            "recurrence_rule": "FREQ=DAILY;COUNT=3",
            "rdates": ["2026-10-25T08:00:00+00:00"],
            "exdates": ["2026-10-20T08:00:00+00:00"],
        }, event_id=self.event.pk)
        self.assertEqual(changed.status_code, 200, changed.data)
        dates = [row["start"][:10] for row in self.rows()]
        self.assertEqual(dates, ["2026-10-19", "2026-10-21", "2026-10-25"])
        too_long = self.call(api.Events, "get", period=("2026-01-01T00:00:00Z", "2027-02-01T00:00:00Z"))
        self.assertEqual(too_long.status_code, 400)

    def test_point_event_keeps_numeric_id_and_existing_routes(self):
        self.event.recurrence_rule = ""
        self.event.recurrence_timezone = ""
        self.event.save()
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], self.event.pk)
        self.assertNotIn("series_id", rows[0])
        self.assertEqual(self.call(api.EventDetail, "patch", data={"location": "Room B"},
                                   event_id=self.event.pk).status_code, 200)
        self.assertEqual(self.call(api.EventDetail, "delete", event_id=self.event.pk).status_code, 204)

    def test_assignment_override_empty_and_inherited_publication_filter(self):
        assigned = Calendar.objects.create(name="Assignments", created_by=self.owner, calendar_type="assignments")
        self.calendar = assigned
        self.owner.user_permissions.add(Permission.objects.get(codename="view_employee"))
        employee = Employee.objects.create(user=self.owner, first_name="A", last_name="B")
        second = Employee.objects.create(first_name="C", last_name="D")
        series = Event.objects.create(calendar=assigned, created_by=self.owner, title="Duty",
                                      start=self.first, end=self.first + timedelta(hours=1),
                                      recurrence_rule="FREQ=DAILY;COUNT=2", recurrence_timezone="Europe/Paris")
        series.employees.add(employee)
        rid = self.first.isoformat()
        alternate = self.call(api.EventOccurrenceDetail, "patch", data={"employees": [second.pk]},
                              event_id=series.pk, recurrence_id=rid)
        self.assertEqual(alternate.status_code, 200, alternate.data)
        self.assertEqual([row["employees"][0]["id"] for row in self.rows((
            "2026-10-19T00:00:00Z", "2026-10-21T00:00:00Z"))], [second.pk, employee.pk])
        empty = self.call(api.EventOccurrenceDetail, "patch", data={"employees": []},
                          event_id=series.pk, recurrence_id=rid)
        self.assertEqual(empty.status_code, 200, empty.data)
        rows = self.rows(("2026-10-19T00:00:00Z", "2026-10-21T00:00:00Z"))
        self.assertEqual([len(row["employees"]) for row in rows], [0, 1])
        from .Calendars import Calendars
        filtered = Calendars().get_published_events(self.owner, str(assigned.pk),
                                                    self.first - timedelta(days=1), self.first + timedelta(days=3),
                                                    options={"assigned_to_me": True})
        self.assertEqual(len(filtered), 1)
        inherited = self.call(api.EventOccurrenceDetail, "patch", data={"inherit": ["employees"]},
                              event_id=series.pk, recurrence_id=rid)
        self.assertEqual(inherited.status_code, 200)
        self.assertEqual([len(row["employees"]) for row in self.rows((
            "2026-10-19T00:00:00Z", "2026-10-21T00:00:00Z"))], [1, 1])

    def test_occurrence_editor_preserves_invisible_historical_employee(self):
        editor = get_user_model().objects.create_user("limited-occurrence-editor")
        assigned = Calendar.objects.create(name="Restricted assignments", created_by=self.owner,
                                           calendar_type="assignments")
        CalendarAccess.objects.create(calendar=assigned, user=editor, level="edit")
        employee = Employee.objects.create(first_name="Hidden", last_name="Employee")
        series = Event.objects.create(calendar=assigned, created_by=self.owner, title="Duty",
                                      start=self.first, end=self.first + timedelta(hours=1),
                                      recurrence_rule="FREQ=DAILY;COUNT=2", recurrence_timezone="Europe/Paris")
        series.employees.add(employee)
        self.calendar = assigned
        edited = self.call(api.EventOccurrenceDetail, "patch", user=editor, data={"employees": []},
                           event_id=series.pk, recurrence_id=self.first.isoformat())
        self.assertEqual(edited.status_code, 200, edited.data)
        exception = EventOccurrenceException.objects.get(event=series)
        self.assertEqual(list(exception.employees.values_list("pk", flat=True)), [employee.pk])
        response = self.call(api.Events, "get", user=editor)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["employees"], [])

    def test_structure_reset_rejects_revision_after_new_exception(self):
        first_id = self.first.isoformat()
        self.call(api.EventOccurrenceDetail, "patch", data={"location": "Room B"},
                  event_id=self.event.pk, recurrence_id=first_id)
        change = {"recurrence_rule": "FREQ=WEEKLY;COUNT=6"}
        prompt = self.call(api.EventDetail, "patch", data=change, event_id=self.event.pk)
        second_id = (self.first + timedelta(days=7, hours=1)).isoformat()
        self.assertEqual(self.call(api.EventOccurrenceDetail, "delete", event_id=self.event.pk,
                                   recurrence_id=second_id).status_code, 200)
        stale = self.call(api.EventDetail, "patch", data={**change, "reset_exceptions": True,
                                                          "exceptions_revision": prompt.data["exceptions_revision"]},
                          event_id=self.event.pk)
        self.assertEqual((stale.status_code, stale.data["code"]), (409, "exceptions_revision_changed"))
        self.assertEqual(EventOccurrenceException.objects.filter(event=self.event).count(), 2)

    def test_until_and_endless_rules_are_bounded_by_read_window(self):
        result = self.call(api.EventDetail, "patch", data={
            "recurrence_rule": "FREQ=DAILY;UNTIL=20261021T080000Z",
        }, event_id=self.event.pk)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(len(self.rows(("2026-10-19T00:00:00Z", "2026-10-25T00:00:00Z"))), 3)
        result = self.call(api.EventDetail, "patch", data={"recurrence_rule": "FREQ=DAILY"},
                           event_id=self.event.pk)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(len(self.rows(("2026-10-19T00:00:00Z", "2026-10-22T00:00:00Z"))), 3)

    def test_overlap_and_all_day_across_dst(self):
        self.event.start = datetime(2026, 10, 24, tzinfo=ZoneInfo("Europe/Paris"))
        self.event.end = datetime(2026, 10, 27, tzinfo=ZoneInfo("Europe/Paris"))
        self.event.all_day = True
        self.event.recurrence_rule = "FREQ=WEEKLY;COUNT=2"
        self.event.save()
        overlap = self.rows(("2026-10-25T00:00:00Z", "2026-10-26T00:00:00Z"))
        self.assertEqual(len(overlap), 1)
        self.assertEqual((overlap[0]["start"], overlap[0]["end"]), ("2026-10-24", "2026-10-27"))
        later = self.rows(("2026-10-31T00:00:00Z", "2026-11-01T00:00:00Z"))
        self.assertEqual((later[0]["start"], later[0]["end"]), ("2026-10-31", "2026-11-03"))

    def test_spring_dst_preserves_local_hour(self):
        self.event.start = datetime(2026, 3, 23, 10, tzinfo=ZoneInfo("Europe/Paris"))
        self.event.end = datetime(2026, 3, 23, 11, tzinfo=ZoneInfo("Europe/Paris"))
        self.event.recurrence_rule = "FREQ=WEEKLY;COUNT=2"
        self.event.save()
        rows = self.rows(("2026-03-22T00:00:00Z", "2026-04-01T00:00:00Z"))
        self.assertEqual([row["start"] for row in rows],
                         ["2026-03-23T09:00:00+00:00", "2026-03-30T08:00:00+00:00"])

    def test_nonexistent_local_time_is_skipped_and_ambiguous_time_uses_first_fold(self):
        zone = ZoneInfo("Europe/Paris")
        spring = datetime(2026, 3, 28, 2, 30, tzinfo=zone)
        starts = list(occurrence_starts(spring, "FREQ=DAILY;COUNT=3", [], [], "Europe/Paris",
                                        spring - timedelta(days=1), spring + timedelta(days=4)))
        self.assertEqual([value.astimezone(zone).date().isoformat() for value in starts],
                         ["2026-03-28", "2026-03-30"])
        autumn = datetime(2026, 10, 24, 2, 30, tzinfo=zone)
        starts = list(occurrence_starts(autumn, "FREQ=DAILY;COUNT=3", [], [], "Europe/Paris",
                                        autumn - timedelta(days=1), autumn + timedelta(days=4)))
        self.assertEqual(starts[1].isoformat(), "2026-10-25T00:30:00+00:00")

    def test_expansion_cap_and_cross_calendar_identity(self):
        with self.assertRaises(RecurrenceError):
            list(occurrence_starts(self.first, "FREQ=DAILY", [], [], "Europe/Paris",
                                   self.first, self.first + timedelta(days=6000)))
        another = Calendar.objects.create(name="Other", created_by=self.owner)
        self.calendar = another
        result = self.call(api.EventOccurrenceDetail, "delete", event_id=self.event.pk,
                           recurrence_id=self.first.isoformat())
        self.assertEqual(result.status_code, 404)

    def test_invalid_structural_reset_is_atomic_and_exception_identity_unique(self):
        self.call(api.EventOccurrenceDetail, "patch", data={"description": "Special"},
                  event_id=self.event.pk, recurrence_id=self.first.isoformat())
        revision = Event.objects.get(pk=self.event.pk).exceptions_revision
        bad = self.call(api.EventDetail, "patch", data={"recurrence_rule": "FREQ=SECONDLY",
                                                          "reset_exceptions": True,
                                                          "exceptions_revision": revision},
                        event_id=self.event.pk)
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(EventOccurrenceException.objects.filter(event=self.event).count(), 1)
        duplicate = EventOccurrenceException(event=self.event, recurrence_id=self.first)
        with self.assertRaises(DjangoValidationError):
            duplicate.save()

    def test_ics_consumer_keeps_existing_point_uid_and_recurring_identity(self):
        from icalendar import Calendar as ICalendar
        from plugin.builtin.ICalExport.ICalExport import build_published_ical
        from .Calendars import Calendars

        Event.objects.create(calendar=self.calendar, created_by=self.owner, title="Point",
                             start=self.first + timedelta(days=2),
                             end=self.first + timedelta(days=2, hours=1))
        provider = Calendars()
        window = (self.first - timedelta(days=1), self.first + timedelta(days=2, hours=2))

        def identities():
            published = provider.get_published_events(self.owner, str(self.calendar.pk), *window)
            payload = build_published_ical("calendars", provider.get_published_calendar(
                self.owner, str(self.calendar.pk)), published)
            return {str(item["UID"]) for item in ICalendar.from_ical(payload).walk("VEVENT")}

        before = identities()
        self.assertEqual(len(before), 2)
        self.event.title = "Renamed"
        self.event.save()
        self.assertEqual(identities(), before)

    def test_expansion_query_count_does_not_follow_occurrence_count(self):
        self.event.recurrence_rule = "FREQ=DAILY;COUNT=100"
        self.event.save()

        def read(end):
            with CaptureQueriesContext(connection) as queries:
                rows = native_occurrences(self.calendar, self.first, end)
                for event, exception, *_ in rows:
                    effective_employees(event, exception)
            return len(rows), len(queries)

        short = read(self.first + timedelta(days=2))
        long = read(self.first + timedelta(days=101))
        self.assertEqual((short[0], long[0]), (2, 100))
        self.assertEqual(short[1], long[1])
