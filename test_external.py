"""Focused checks for remote subscriptions and their read-only calendar view."""

from unittest.mock import patch
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from . import api, external
from .models import Calendar, CalendarAccess, ExternalCalendarEvent, ExternalCalendarSource


def feed(*events):
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + "".join(events) + "END:VCALENDAR\r\n").encode()


def event(uid="one", title="Réunion", start="20261101T090000Z", extra=""):
    return (f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTART:{start}\r\n"
            f"DTEND:20261101T100000Z\r\nSUMMARY:{title}\r\n{extra}END:VEVENT\r\n")


class ExternalCalendarTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("external-owner")
        self.viewer = get_user_model().objects.create_user("external-viewer")
        self.calendar = Calendar.objects.create(name="Remote", created_by=self.owner, calendar_type="external")
        self.source = ExternalCalendarSource.objects.create(calendar=self.calendar, url="https://calendar.example.test/secret")
        CalendarAccess.objects.create(calendar=self.calendar, user=self.viewer, level="view")

    def call(self, view, method, user, data=None, **kwargs):
        request = getattr(APIRequestFactory(), method)("/?" + urlencode({
            "start": "2026-10-30T00:00:00Z", "end": "2026-11-10T00:00:00Z",
        }), data=data, format="json")
        force_authenticate(request, user=user)
        return view.as_view()(request, pk=self.calendar.pk, **kwargs)

    def test_private_configuration_and_read_only_events(self):
        self.assertNotIn("secret", str(self.call(api.CalendarDetail, "get", self.owner).data))
        self.assertNotIn("external", self.call(api.CalendarDetail, "get", self.viewer).data)
        self.assertEqual(self.call(api.Events, "post", self.owner, {"title": "Local"}).status_code, 403)
        self.assertEqual(self.call(api.EventDetail, "patch", self.owner, {"title": "Local"}, event_id=1).status_code, 403)
        self.assertEqual(self.call(api.ExternalCalendarSync, "post", self.viewer).status_code, 403)

    @override_settings(REACT_PUBLIC_URL="https://labs.example.org/app")
    def test_published_external_events_receive_only_visible_labsmanager_links(self):
        from icalendar import Calendar as ICalendar

        from .Calendars import Calendars
        from plugin.builtin.ICalExport.ICalExport import build_published_ical

        with patch.object(external, "download", return_value=feed(event(
                extra="DESCRIPTION:Remote notes\\nSecond line\r\nLOCATION:Room A\r\n"))):
            external.sync_calendar(self.source.pk)
        start = api.timezone.make_aware(api.datetime(2026, 10, 30))
        end = api.timezone.make_aware(api.datetime(2026, 11, 10))
        provider = Calendars()
        published = provider.get_published_events(self.viewer, str(self.calendar.pk), start, end)
        self.assertEqual(len(published), 1)
        self.assertEqual(published[0].metadata["links"], ["plugins/calendars/calendars"])
        self.assertNotIn(self.source.url, str(published[0].metadata))
        self.assertIn("Remote notes", published[0].metadata["ical_component"])
        ics = build_published_ical("calendars", provider.get_published_calendar(
            self.viewer, str(self.calendar.pk)), published)
        description = str(ICalendar.from_ical(ics).walk("VEVENT")[0]["DESCRIPTION"])
        self.assertIn("Remote notes\nSecond line", description)
        self.assertIn("https://labs.example.org/app/plugins/calendars/calendars", description)
        self.assertNotIn(self.source.url, ics.decode("utf-8"))
        self.assertNotIn("secret", ics.decode("utf-8"))

    def test_create_external_calendar_requires_private_https_source(self):
        request = APIRequestFactory().post("/", {"name": "Subscribed", "calendar_type": "external",
                                                  "url": "https://calendar.example.test/private"}, format="json")
        force_authenticate(request, user=self.owner)
        with patch.object(api, "_public_addresses", return_value=["93.184.215.14"]):
            response = api.Calendars.as_view()(request)
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data["external"]["has_url"])
        self.assertNotIn("private", str(response.data))
        self.assertEqual(ExternalCalendarSource.objects.get(calendar_id=response.data["id"]).interval_minutes, 60)

    def test_sync_diff_failure_and_recurrence(self):
        first = feed(event(extra="RRULE:FREQ=DAILY;COUNT=3\r\nEXDATE:20261102T090000Z\r\n"
                                 "RDATE:20261105T090000Z\r\n"))
        with patch.object(external, "download", return_value=first):
            self.assertEqual(external.sync_calendar(self.source.pk)["created"], 1)
            self.assertEqual(external.sync_calendar(self.source.pk)["updated"], 0)
        self.source.enabled = False
        self.source.save()
        self.assertFalse(self.call(api.CalendarDetail, "get", self.owner).data["has_events"])
        self.source.enabled = True
        self.source.save()
        rows = self.call(api.Events, "get", self.viewer).data["results"]
        self.assertEqual(len(rows), 3)
        self.assertEqual([row["start"][:10] for row in rows], ["2026-11-01", "2026-11-03", "2026-11-05"])
        changed = feed(event(title="Nouveau"))
        with patch.object(external, "download", return_value=changed):
            self.assertEqual(external.sync_calendar(self.source.pk)["updated"], 1)
        self.assertEqual(ExternalCalendarEvent.objects.get(source=self.source).title, "Nouveau")
        with patch.object(external, "download", side_effect=external.ExternalFeedError("Unavailable")):
            self.assertEqual(external.sync_calendar(self.source.pk)["status"], "error")
        self.assertTrue(ExternalCalendarEvent.objects.get(source=self.source).active)
        with patch.object(external, "download", return_value=feed()):
            self.assertEqual(external.sync_calendar(self.source.pk)["deactivated"], 1)
        self.assertFalse(self.call(api.CalendarDetail, "get", self.owner).data["has_events"])

    def test_manual_sync_is_queued(self):
        with patch("django_q.tasks.async_task") as queued:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.call(api.ExternalCalendarSync, "post", self.owner)
        self.assertEqual(response.status_code, 202)
        queued.assert_called_once_with("plugins.Calendars.external.sync_calendar", self.source.pk)

    def test_due_scan_only_runs_eligible_sources(self):
        with patch.object(external, "sync_calendar") as sync:
            external.sync_due_calendars()
        sync.assert_called_once_with(self.source.pk)

    def test_running_sync_is_not_started_twice_and_invalid_feed_keeps_rows(self):
        self.source.status = "running"
        self.source.last_attempt = api.timezone.now()
        self.source.save()
        with patch.object(external, "download") as download:
            self.assertEqual(external.sync_calendar(self.source.pk)["status"], "running")
        download.assert_not_called()
        self.source.status = "pending"
        self.source.save()
        with patch.object(external, "download", return_value=feed(event())):
            external.sync_calendar(self.source.pk)
        with patch.object(external, "download", return_value=b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"):
            self.assertEqual(external.sync_calendar(self.source.pk)["status"], "error")
        self.assertTrue(ExternalCalendarEvent.objects.get(source=self.source).active)

    def test_invalid_url_and_incomplete_feed(self):
        for url in ("http://example.org/file.ics", "https://127.0.0.1/file.ics",
                    "https://user:secret@example.org/file.ics"):
            with self.assertRaises(external.ExternalFeedError):
                parsed, host = external.validate_url(url)
                external._public_addresses(host)
        with self.assertRaises(external.ExternalFeedError):
            external.parse_feed(feed(event())[:-20])

    def test_download_pins_validated_address_and_rejects_redirects(self):
        with patch.object(external, "_public_addresses", return_value=["93.184.215.14"]), \
                patch.object(external, "_PinnedHTTPS") as connection_type:
            response = connection_type.return_value.getresponse.return_value
            response.status = 200
            response.getheader.side_effect = lambda _name, default=None: default
            response.read.return_value = feed()
            self.assertEqual(external.download("https://calendar.example.test/private?token=one"), feed())
            connection_type.assert_called_once_with("calendar.example.test", "93.184.215.14")
            self.assertEqual(connection_type.return_value.request.call_args.args[:2],
                             ("GET", "/private?token=one"))
            response.status = 302
            with self.assertRaises(external.ExternalFeedError):
                external.download("https://calendar.example.test/private")

    def test_exception_moves_and_cancels_occurrences(self):
        master = event(extra="RRULE:FREQ=DAILY;COUNT=3\r\n")
        moved = ("BEGIN:VEVENT\r\nUID:one\r\nRECURRENCE-ID:20261102T090000Z\r\n"
                 "DTSTART:20261104T090000Z\r\nDTEND:20261104T100000Z\r\nSUMMARY:Moved\r\nEND:VEVENT\r\n")
        cancelled = ("BEGIN:VEVENT\r\nUID:one\r\nRECURRENCE-ID:20261103T090000Z\r\n"
                     "DTSTART:20261103T090000Z\r\nDTEND:20261103T100000Z\r\n"
                     "STATUS:CANCELLED\r\nEND:VEVENT\r\n")
        with patch.object(external, "download", return_value=feed(master, moved, cancelled)):
            self.assertEqual(external.sync_calendar(self.source.pk)["created"], 3)
        rows = self.call(api.Events, "get", self.owner).data["results"]
        self.assertEqual([(row["title"], row["start"][:10]) for row in rows],
                         [("Réunion", "2026-11-01"), ("Moved", "2026-11-04")])

    def test_weekly_monthly_and_local_timezone_recurrences(self):
        weekly = ("BEGIN:VEVENT\r\nUID:weekly\r\nDTSTART;TZID=Europe/Paris:20261018T090000\r\n"
                  "DTEND;TZID=Europe/Paris:20261018T100000\r\n"
                  "RRULE:FREQ=WEEKLY;COUNT=3\r\nEND:VEVENT\r\n")
        monthly = ("BEGIN:VEVENT\r\nUID:monthly\r\nDTSTART;VALUE=DATE:20261101\r\n"
                   "DTEND;VALUE=DATE:20261102\r\nRRULE:FREQ=MONTHLY;COUNT=2\r\nEND:VEVENT\r\n")
        with patch.object(external, "download", return_value=feed(weekly, monthly)):
            self.assertEqual(external.sync_calendar(self.source.pk)["created"], 2)
        start = api.timezone.make_aware(api.datetime(2026, 10, 1))
        end = api.timezone.make_aware(api.datetime(2026, 12, 3))
        rows = external.occurrences(self.source, start, end)
        local = ZoneInfo("Europe/Paris")
        weekly_starts = [begins.astimezone(local).isoformat() for _, item, begins, _ in rows if item.uid == "weekly"]
        self.assertEqual(weekly_starts, ["2026-10-18T09:00:00+02:00", "2026-10-25T09:00:00+01:00",
                                         "2026-11-01T09:00:00+01:00"])
        monthly_starts = [begins.date().isoformat() for _, item, begins, _ in rows if item.uid == "monthly"]
        self.assertEqual(monthly_starts, ["2026-11-01", "2026-12-01"])


class ExternalParsingTests(TestCase):
    def test_all_day_and_unbounded_recurrence_are_bounded(self):
        start = "BEGIN:VEVENT\r\nUID:all-day\r\nDTSTART;VALUE=DATE:20261101\r\n"
        body = start + "DTEND;VALUE=DATE:20261102\r\nRRULE:FREQ=DAILY\r\nEND:VEVENT\r\n"
        records, _ = external.parse_feed(feed(body))
        self.assertTrue(records[("all-day", "")]["all_day"])
