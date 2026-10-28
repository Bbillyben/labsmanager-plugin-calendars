"""Independent user-owned calendars, exposed through the existing plugin contracts."""

from django.conf import settings
from django.db.models import Prefetch
from django.http import Http404
from django.urls import path
from types import SimpleNamespace

from common.calendar import LabsManagerCalendarEvent
from plugin.published_calendars import PublishedCalendar

from plugin import LabManagerPlugin
from plugin.mixins import ModelMixin, PublishedCalendarMixin, UIContributionMixin, UrlsMixin
from staff.models import Employee

from . import api


class Calendars(ModelMixin, UrlsMixin, PublishedCalendarMixin, UIContributionMixin, LabManagerPlugin):
    NAME = "Calendars"
    SLUG = "calendars"
    TITLE = "Calendars"
    DESCRIPTION = "Independent calendars shared between LabsManager users."
    AUTHOR = "LabsManager"
    VERSION = "1.8.0"
    DJANGO_APP = "plugins.Calendars.apps.CalendarsConfig"

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
        events = api.Event.objects.filter(calendar=calendar, start__lt=end, end__gt=start).select_related("event_type")
        if options.get("assigned_to_me"):
            employee_ids = Employee.objects.filter(user=user).values_list("pk", flat=True)
            events = events.filter(employees__pk__in=employee_ids).distinct()
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
        if calendar.calendar_type == "assignments":
            events = events.prefetch_related(Prefetch(
                "employees", queryset=api.visible_employee_queryset(user), to_attr="visible_assignees"
            ))
        results = []
        for item in events:
            names = sorted((str(employee) for employee in getattr(item, "visible_assignees", ())), key=str.casefold)
            title = item.title
            if calendar.calendar_type == "assignments":
                title = " — ".join(part for part in (
                    item.event_type.name if item.event_type else "", item.title, ", ".join(names)
                ) if part)
            results.append(LabsManagerCalendarEvent(
                id=str(item.pk), title=title, description=item.description,
                start=item.start, end=item.end, all_day=item.all_day,
                source=f"plugin:{self.slug}", color=(item.event_type.color if item.event_type and item.event_type.color else calendar.color),
                metadata={"last_modified": item.updated_at,
                          "assignees": [{"name": name} for name in names], "links": links},
            ))
        return results
