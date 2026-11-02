"""Independent user-owned calendars, exposed through the existing plugin contracts."""

from django.conf import settings
from django.http import Http404
from django.urls import path
from types import SimpleNamespace

from common.calendar import LabsManagerCalendarEvent
from plugin.published_calendars import PublishedCalendar

from plugin import LabManagerPlugin
from plugin.mixins import ModelMixin, PublishedCalendarMixin, ScheduleMixin, UIContributionMixin, UrlsMixin
from staff.models import Employee

from . import api, external


class Calendars(ModelMixin, UrlsMixin, PublishedCalendarMixin, ScheduleMixin, UIContributionMixin, LabManagerPlugin):
    NAME = "Calendars"
    SLUG = "calendars"
    TITLE = "Calendars"
    DESCRIPTION = "Independent calendars shared between LabsManager users."
    AUTHOR = "LabsManager"
    VERSION = "1.9.0"
    DJANGO_APP = "plugins.Calendars.apps.CalendarsConfig"
    SCHEDULED_TASKS = {
        "sync_external": {"func": "sync_external", "schedule": "I", "minutes": 15, "repeats": -1},
    }

    @classmethod
    def sync_external(cls):
        """Run the shared due-source scan from the existing plugin scheduler."""
        external.sync_due_calendars()

    UI_CONTRIBUTIONS = [
        {"id": "calendars", "slot": "plugin.page", "type": "page", "label": "Calendriers",
         "module": "main", "component": "Page", "api_version": 1,
         "navigation": {"location": "calendars", "labels": {"fr": "Calendars", "en": "Calendars"},
                        "icon": "calendar", "order": 0}},
        {"id": "settings", "slot": "user.plugin.settings.panels", "type": "tab",
         "label": "Calendars settings", "module": "main", "component": "SettingsPanel", "api_version": 1},
        *({"id": f"{entity}-calendars", "slot": f"{entity}.detail.tabs", "type": "tab",
           "label": "Calendriers", "module": "main", "component": "ContextTab",
           "api_version": 1, "visible": "has_context_calendars"}
          for entity in ("employee", "team", "project")),
    ]

    def has_context_calendars(self, request, context):
        entity = context["slot"].split(".", 1)[0]
        return api.associated_calendars(request.user, entity, context[entity].pk).exists()

    URLS = [
        path("api/calendars/", api.Calendars.as_view(), name="calendars-list"),
        path("api/calendars/<int:pk>/", api.CalendarDetail.as_view(), name="calendar-detail"),
        path("api/calendars/<int:pk>/sync/", api.ExternalCalendarSync.as_view(), name="calendar-sync"),
        path("api/calendars/<int:pk>/attachments/", api.CalendarAttachments.as_view(), name="calendar-attachments"),
        path("api/calendars/<int:pk>/eligible-employees/", api.EligibleEmployees.as_view(), name="calendar-eligible-employees"),
        path("api/calendars/<int:pk>/resources/", api.CalendarResources.as_view(), name="calendar-resources"),
        path("api/calendars/<int:pk>/users/", api.CalendarUsers.as_view(), name="calendar-users"),
        path("api/calendars/<int:pk>/members/", api.CalendarMembers.as_view(), name="calendar-members"),
        path("api/calendars/<int:pk>/members/<int:user_id>/", api.CalendarMemberDetail.as_view(), name="calendar-member-detail"),
        path("api/calendars/<int:pk>/types/", api.EventTypes.as_view(), name="calendar-types"),
        path("api/calendars/<int:pk>/types/<int:type_id>/", api.EventTypeDetail.as_view(), name="calendar-type-detail"),
        path("api/calendars/<int:pk>/events/", api.Events.as_view(), name="calendar-events"),
        path("api/calendars/<int:pk>/events/<int:event_id>/", api.EventDetail.as_view(), name="calendar-event-detail"),
        path("api/calendars/<int:pk>/events/<int:event_id>/occurrences/<str:recurrence_id>/",
             api.EventOccurrenceDetail.as_view(), name="calendar-event-occurrence"),
        path("api/calendars/<int:pk>/events/<int:event_id>/occurrences/<str:recurrence_id>/restore/",
             api.EventOccurrenceRestore.as_view(), name="calendar-event-occurrence-restore"),
    ]

    def list_published_calendars(self, user):
        return [self._published_calendar(item) for item in api.visible_calendars(user)]

    @staticmethod
    def _published_calendar(item):
        return PublishedCalendar(id=str(item.pk), name=item.name, description=item.description,
                                 timezone=settings.TIME_ZONE, color=item.color, incarnation=item.created_at.isoformat(),
                                 options=("assigned_to_me",) if item.calendar_type == "assignments" else ())

    def get_published_calendar(self, user, calendar_id):
        if not calendar_id.isdecimal() or int(calendar_id) <= 0:
            raise Http404
        item = api.calendar_for(SimpleNamespace(user=user), int(calendar_id))
        return self._published_calendar(item)

    def get_published_events(self, user, calendar_id, start, end, *, options=None):
        if not calendar_id.isdecimal() or int(calendar_id) <= 0:
            raise Http404
        calendar = api.calendar_for(SimpleNamespace(user=user), int(calendar_id))
        options = options or {}
        if set(options) - {"assigned_to_me"} or (options.get("assigned_to_me") and calendar.calendar_type != "assignments"):
            raise ValueError("Unsupported calendar option")
        visible_employees, visible_teams, visible_projects = api.visible_objects(user)
        links = []
        for relation, visible, route in (
            ("employees", visible_employees, "employees"),
            ("teams", visible_teams, "teams"),
            ("projects", visible_projects, "projects"),
        ):
            ids = getattr(calendar, relation).filter(pk__in=visible.values("pk")).order_by("pk").values_list("pk", flat=True)
            links.extend(f"{route}/{pk}/plugins/calendars/{relation[:-1]}-calendars" for pk in ids)
        if not calendar.employees.exists() and not calendar.teams.exists() and not calendar.projects.exists():
            links.append("plugins/calendars/calendars")
        if calendar.calendar_type == "external":
            source = api.ExternalCalendarSource.objects.filter(calendar=calendar, enabled=True).first()
            if source is None:
                return []
            # The ICS consumer needs the master and all overrides to preserve
            # native recurrence rules; its existing token controls still apply.
            return [LabsManagerCalendarEvent(
                id=str(item.pk), title=item.title, description=item.description,
                start=item.start, end=item.end, all_day=item.all_day,
                source=f"plugin:{self.slug}", color=calendar.color,
                metadata={"ical_uid": item.uid, "ical_component": item.raw_component,
                          "ical_timezones": source.timezone_components,
                          "last_modified": item.last_modified, "links": links},
            ) for item in source.events.filter(active=True).order_by("uid", "recurrence_key")]
        rows = api.native_occurrences(calendar, start, end)
        own_ids = set(Employee.objects.filter(user=user).values_list("pk", flat=True)) if options.get("assigned_to_me") else set()
        visible_ids = set(api.visible_employee_queryset(user).values_list("pk", flat=True)) if calendar.calendar_type == "assignments" else set()
        results = []
        for item, exception, begins, finishes, recurrence_id in rows:
            assignees = api.effective_employees(item, exception) if calendar.calendar_type == "assignments" else []
            if options.get("assigned_to_me") and not any(employee.pk in own_ids for employee in assignees):
                continue
            names = sorted((str(employee) for employee in assignees if employee.pk in visible_ids), key=str.casefold)
            kind = exception.event_type if exception and exception.event_type_override else item.event_type
            title = exception.title if exception and exception.title_override else item.title
            if calendar.calendar_type == "assignments":
                title = " — ".join(part for part in (
                    kind.name if kind else "", title, ", ".join(names)
                ) if part)
            description = exception.description if exception and exception.description_override else item.description
            location = exception.location if exception and exception.location_override else item.location
            results.append(LabsManagerCalendarEvent(
                id=str(item.pk) if recurrence_id is None else f"{item.pk}:{recurrence_id.isoformat()}",
                title=title, description=description,
                start=begins, end=finishes,
                all_day=exception.all_day if exception and exception.timeslot_override else item.all_day,
                source=f"plugin:{self.slug}", color=(kind.color if kind and kind.color else calendar.color),
                metadata={"last_modified": item.updated_at, "location": location,
                          "assignees": [{"name": name} for name in names], "links": links,
                          **({"series_id": item.pk, "recurrence_id": recurrence_id.isoformat()}
                             if recurrence_id is not None else {})},
            ))
        return results
