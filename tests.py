"""Targeted model, migration, API and object permission checks."""

from datetime import timedelta
from unittest.mock import patch
from urllib.parse import urlencode

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from . import api
from .models import Calendar, CalendarAccess, Event, EventType
from plugin.registry import registry
from plugin.ui_api_v1 import UIContributions, UIContributionDetail
from project.models import Participant, Project
from staff.models import Employee, Team, TeamMate


class CalendarsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user("owner", password="test")
        cls.editor = get_user_model().objects.create_user("editor", password="test")
        cls.viewer = get_user_model().objects.create_user("viewer", password="test")
        cls.stranger = get_user_model().objects.create_user("stranger", password="test")
        cls.calendar = Calendar.objects.create(name="Laboratoire", created_by=cls.owner)
        cls.other = Calendar.objects.create(name="Privé", created_by=cls.stranger)
        CalendarAccess.objects.create(calendar=cls.calendar, user=cls.editor, level="edit")
        CalendarAccess.objects.create(calendar=cls.calendar, user=cls.viewer, level="view")
        cls.kind = EventType.objects.create(calendar=cls.calendar, name="Réunion", color="#0088cc")
        cls.start = timezone.now().replace(minute=0, second=0, microsecond=0)

    def call(self, view, method, user, url="/", data=None, **kwargs):
        request = getattr(APIRequestFactory(), method)(url, data=data, format="json")
        force_authenticate(request, user=user)
        return view.as_view()(request, **kwargs)

    def event_payload(self, **changes):
        data = {
            "title": "Séminaire", "description": "", "start": self.start.isoformat(),
            "end": (self.start + timedelta(hours=1)).isoformat(), "all_day": False,
            "event_type": self.kind.pk,
        }
        return {**data, **changes}

    def test_app_and_migration_are_registered(self):
        self.assertEqual(apps.get_app_config("plugin_calendars").name, "plugins.Calendars")
        executor = MigrationExecutor(connection)
        self.assertIn(("plugin_calendars", "0001_initial"), executor.loader.graph.nodes)
        self.assertIn(("plugin_calendars", "0002_calendar_access_mode_calendar_employees_and_more"), executor.loader.graph.nodes)
        self.assertIn(("plugin_calendars", "0003_calendar_type_event_employees"), executor.loader.graph.nodes)
        self.assertIn("calendar_type", [field.name for field in Calendar._meta.fields])

    def test_calendar_type_defaults_and_locks_after_event_creation(self):
        self.assertEqual(self.calendar.calendar_type, "events")
        created = self.call(api.Calendars, "post", self.owner, data={"name": "Assignments", "calendar_type": "assignments"})
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.data["calendar_type"], "assignments")
        self.assertEqual(self.call(api.CalendarDetail, "patch", self.owner, pk=self.calendar.pk,
                                   data={"calendar_type": "assignments"}).status_code, 200)
        self.assertEqual(self.call(api.Events, "post", self.owner, pk=self.calendar.pk, data=self.event_payload()).status_code, 201)
        self.assertEqual(self.call(api.CalendarDetail, "patch", self.owner, pk=self.calendar.pk,
                                   data={"calendar_type": "events"}).status_code, 400)
        self.assertEqual(self.call(api.Calendars, "post", self.owner, data={"name": "Invalid", "calendar_type": "other"}).status_code, 400)

    def test_assignments_are_optional_unique_and_permission_scoped(self):
        employee, _, _ = self.contextual_objects(self.owner)
        self.owner.user_permissions.add(Permission.objects.get(codename="view_employee"))
        self.assertEqual(self.call(api.Events, "post", self.owner, pk=self.calendar.pk,
                                   data=self.event_payload(employees=[employee.pk])).status_code, 400)
        simple = Event.objects.create(calendar=self.calendar, created_by=self.owner,
                                      title="Simple", start=self.start, end=self.start + timedelta(hours=1))
        with self.assertRaises(ValidationError), transaction.atomic():
            simple.employees.add(employee)
        assigned = Calendar.objects.create(name="Assignments", created_by=self.owner, calendar_type="assignments")
        created = self.call(api.Events, "post", self.editor, pk=assigned.pk, data=self.event_payload(event_type=None, employees=[]))
        self.assertEqual(created.status_code, 404)
        CalendarAccess.objects.create(calendar=assigned, user=self.editor, level="edit")
        created = self.call(api.Events, "post", self.editor, pk=assigned.pk, data=self.event_payload(event_type=None, employees=[]))
        self.assertEqual(created.status_code, 201)
        event_id = created.data["id"]
        self.assertEqual(created.data["employees"], [])
        self.assertEqual(self.call(api.EventDetail, "patch", self.editor, pk=assigned.pk, event_id=event_id,
                                   data={"employees": [employee.pk, employee.pk]}).status_code, 400)
        self.assertEqual(self.call(api.EventDetail, "patch", self.owner, pk=assigned.pk, event_id=event_id,
                                   data={"employees": [employee.pk]}).status_code, 200)
        self.assertEqual(self.call(api.EventDetail, "patch", self.owner, pk=assigned.pk, event_id=event_id,
                                   data={"employees": []}).status_code, 200)
        self.assertTrue(Event.objects.filter(pk=event_id).exists())
        self.assertEqual(self.call(api.EventDetail, "patch", self.viewer, pk=assigned.pk, event_id=event_id,
                                   data={"employees": [employee.pk]}).status_code, 404)

    def test_resource_rows_are_scoped_to_visible_eligible_employees(self):
        self.owner.user_permissions.add(Permission.objects.get(codename="view_employee"))
        assigned = Calendar.objects.create(name="Assignments", created_by=self.owner, calendar_type="assignments")
        first = Employee.objects.create(first_name="Ada", last_name="Visible", is_active=True)
        second = Employee.objects.create(first_name="Bob", last_name="Visible", is_active=True)
        event = Event.objects.create(calendar=assigned, created_by=self.owner, title="Shift",
                                     start=self.start, end=self.start + timedelta(hours=1))
        event.employees.add(first)
        query = "/?" + urlencode({"start": self.start.isoformat(), "end": (self.start + timedelta(days=1)).isoformat()})
        rows = self.call(api.CalendarResources, "get", self.owner, query, pk=assigned.pk)
        self.assertEqual(rows.status_code, 200)
        self.assertEqual([item["id"] for item in rows.data["results"]], [first.pk])
        assigned.employees.add(second)
        rows = self.call(api.CalendarResources, "get", self.owner, query, pk=assigned.pk)
        self.assertEqual({item["id"] for item in rows.data["results"]}, {second.pk})
        self.assertEqual(self.call(api.CalendarResources, "get", self.stranger, query, pk=assigned.pk).status_code, 404)
        self.assertEqual(self.call(api.CalendarResources, "get", self.owner, query, pk=self.calendar.pk).status_code, 400)

    def test_eligibility_unions_current_relations_and_preserves_historical_assignments(self):
        attached, team, project = self.contextual_objects(self.owner)
        self.owner.user_permissions.add(Permission.objects.get(codename="view_employee"))
        second = Employee.objects.create(first_name="Alice", last_name="Visible")
        third = Employee.objects.create(first_name="Bob", last_name="Visible")
        outsider = Employee.objects.create(first_name="Cara", last_name="Visible")
        Participant.objects.create(project=project, employee=second)
        TeamMate.objects.create(team=team, employee=third)
        calendar = Calendar.objects.create(name="Assignments", created_by=self.owner, calendar_type="assignments")
        calendar.projects.add(project); calendar.teams.add(team); calendar.employees.add(second)
        result = self.call(api.EligibleEmployees, "get", self.owner, pk=calendar.pk)
        self.assertEqual({item["id"] for item in result.data["results"]}, {attached.pk, second.pk, third.pk})
        limited = get_user_model().objects.create_user("limited-assignments")
        CalendarAccess.objects.create(calendar=calendar, user=limited, level="view")
        self.assertEqual(self.call(api.EligibleEmployees, "get", limited, pk=calendar.pk).data["results"], [])
        self.assertEqual(self.call(api.EligibleEmployees, "get", self.stranger, pk=calendar.pk).status_code, 404)
        self.assertEqual(self.call(api.Events, "post", self.owner, pk=calendar.pk,
                                   data=self.event_payload(event_type=None, employees=[outsider.pk])).status_code, 400)
        created = self.call(api.Events, "post", self.owner, pk=calendar.pk,
                            data=self.event_payload(event_type=None, employees=[attached.pk, second.pk, third.pk]))
        self.assertEqual(created.status_code, 201)
        event_id = created.data["id"]
        self.assertEqual(self.call(api.EventDetail, "get", limited, pk=calendar.pk, event_id=event_id).data["employees"], [])
        CalendarAccess.objects.filter(calendar=calendar, user=limited).update(level="edit")
        self.assertEqual(self.call(api.EventDetail, "patch", limited, pk=calendar.pk, event_id=event_id,
                                   data={"employees": []}).status_code, 200)
        self.assertEqual(Event.objects.get(pk=event_id).employees.count(), 3)
        calendar.projects.clear(); calendar.teams.clear(); calendar.employees.add(attached)
        # The former project participant remains assigned, but cannot be added to a new event.
        self.assertEqual(self.call(api.EventDetail, "patch", self.owner, pk=calendar.pk, event_id=event_id,
                                   data={"title": "Updated"}).status_code, 200)
        self.assertEqual(self.call(api.EventDetail, "patch", self.owner, pk=calendar.pk, event_id=event_id,
                                   data={"employees": [attached.pk, second.pk]}).status_code, 200)
        self.assertEqual(set(Event.objects.get(pk=event_id).employees.values_list("pk", flat=True)), {attached.pk, second.pk})
        self.assertEqual(self.call(api.Events, "post", self.owner, pk=calendar.pk,
                                   data=self.event_payload(event_type=None, employees=[third.pk])).status_code, 400)

    def test_creation_and_calendar_visibility(self):
        created = self.call(api.Calendars, "post", self.viewer, data={"name": "Projet", "description": "", "color": "#abc123"})
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.data["role"], "manage")
        self.assertEqual(created.data["created_by"], self.viewer.pk)
        result = self.call(api.Calendars, "get", self.viewer)
        self.assertEqual({item["name"] for item in result.data["results"]}, {"Laboratoire", "Projet"})
        self.assertEqual(self.call(api.CalendarDetail, "get", self.viewer, pk=self.other.pk).status_code, 404)
        self.assertEqual(self.call(api.CalendarDetail, "patch", self.editor, pk=self.calendar.pk, data={"name": "Changed"}).status_code, 403)

    def test_general_and_managed_scopes_separate_attachments_without_changing_access(self):
        employee, team, project = self.contextual_objects(self.owner)
        attached = []
        for relation, obj in (("employees", employee), ("teams", team), ("projects", project)):
            calendar = Calendar.objects.create(name=relation, created_by=self.owner)
            getattr(calendar, relation).add(obj)
            attached.append(calendar)
        multiple = Calendar.objects.create(name="Multiple", created_by=self.owner)
        multiple.employees.add(employee)
        multiple.teams.add(team)
        multiple.projects.add(project)
        attached.append(multiple)

        def ids(user, url):
            return {item["id"] for item in self.call(api.Calendars, "get", user, url=url).data["results"]}

        self.assertEqual(ids(self.owner, "/?scope=general"), {self.calendar.pk})
        self.assertEqual(ids(self.owner, "/?scope=managed"), {self.calendar.pk, *(item.pk for item in attached)})
        managed = self.call(api.Calendars, "get", self.owner, url="/?scope=managed").data["results"]
        self.assertEqual(next(item for item in managed if item["id"] == multiple.pk)["attachments"], {
            "employees": [{"id": employee.pk, "label": str(employee)}],
            "teams": [{"id": team.pk, "label": str(team)}],
            "projects": [{"id": project.pk, "label": str(project)}],
        })
        self.assertNotIn("attachments", self.call(api.Calendars, "get", self.owner, url="/?scope=general").data["results"][0])
        self.assertEqual(ids(self.owner, "/"), {self.calendar.pk, *(item.pk for item in attached)})
        for entity, obj, calendar in (("employee", employee, attached[0]),
                                      ("team", team, attached[1]), ("project", project, attached[2])):
            self.assertIn(calendar.pk, ids(self.owner, f"/?{entity}_id={obj.pk}"))
            self.assertIn(multiple.pk, ids(self.owner, f"/?{entity}_id={obj.pk}"))
        manager = get_user_model().objects.create_user("scope-manager")
        CalendarAccess.objects.create(calendar=multiple, user=manager, level="manage")
        self.assertEqual(ids(manager, "/?scope=managed"), {multiple.pk})
        self.assertEqual(ids(manager, "/?scope=general"), set())
        self.assertEqual(self.call(api.CalendarDetail, "get", manager, pk=multiple.pk).status_code, 200)
        self.assertEqual(self.call(api.CalendarAttachments, "get", manager, pk=multiple.pk).status_code, 200)
        self.assertEqual(self.call(api.Calendars, "get", self.owner, url="/?scope=unknown").status_code, 400)
        self.assertEqual(self.call(api.Calendars, "get", self.owner,
                                   url=f"/?scope=general&employee_id={employee.pk}").status_code, 400)

        multiple.employees.clear(); multiple.teams.clear(); multiple.projects.clear()
        self.assertIn(multiple.pk, ids(self.owner, "/?scope=general"))
        self.assertIn(multiple.pk, ids(manager, "/?scope=general"))
        administrator = get_user_model().objects.create_superuser("scope-admin", "scope-admin@example.test", "test")
        self.assertIn(self.other.pk, ids(administrator, "/?scope=managed"))

    def test_membership_hierarchy_and_owner_protection(self):
        created = self.call(api.CalendarMembers, "post", self.owner, pk=self.calendar.pk,
                            data={"user_id": self.stranger.pk, "level": "manage"})
        self.assertEqual(created.status_code, 201)
        self.assertEqual(self.call(api.CalendarDetail, "patch", self.stranger, pk=self.calendar.pk,
                                   data={"name": "Managed"}).status_code, 200)
        self.assertEqual(self.call(api.CalendarMembers, "post", self.editor, pk=self.calendar.pk,
                                   data={"user_id": self.stranger.pk, "level": "view"}).status_code, 403)
        self.assertEqual(self.call(api.CalendarMembers, "post", self.owner, pk=self.calendar.pk,
                                   data={"user_id": self.owner.pk, "level": "view"}).status_code, 400)
        self.assertEqual(self.call(api.CalendarMemberDetail, "delete", self.owner, pk=self.calendar.pk,
                                   user_id=self.stranger.pk).status_code, 204)
        self.assertEqual(self.call(api.CalendarDetail, "get", self.stranger, pk=self.calendar.pk).status_code, 404)

    def test_event_permissions_and_direct_access(self):
        created = self.call(api.Events, "post", self.editor, pk=self.calendar.pk, data=self.event_payload())
        self.assertEqual(created.status_code, 201)
        event_id = created.data["id"]
        self.assertEqual(self.call(api.Events, "post", self.viewer, pk=self.calendar.pk, data=self.event_payload()).status_code, 403)
        self.assertEqual(self.call(api.Events, "get", self.stranger, pk=self.calendar.pk,
                                   url=f"/?start={self.start.isoformat()}&end={(self.start + timedelta(days=1)).isoformat()}").status_code, 404)
        self.assertEqual(self.call(api.EventDetail, "get", self.stranger, pk=self.calendar.pk, event_id=event_id).status_code, 404)
        self.assertEqual(self.call(api.EventDetail, "get", self.viewer, pk=self.calendar.pk, event_id=event_id).status_code, 200)
        self.assertEqual(self.call(api.EventDetail, "patch", self.viewer, pk=self.calendar.pk, event_id=event_id,
                                   data={"title": "No"}).status_code, 403)
        self.assertEqual(self.call(api.EventDetail, "patch", self.editor, pk=self.calendar.pk, event_id=event_id,
                                   data={"title": "Updated"}).status_code, 200)
        self.assertEqual(self.call(api.EventDetail, "delete", self.editor, pk=self.calendar.pk, event_id=event_id).status_code, 204)

    def test_authentication_and_user_search_are_scoped_to_managers(self):
        request = APIRequestFactory().get("/")
        self.assertEqual(api.Calendars.as_view()(request).status_code, 403)
        self.assertEqual(self.call(api.CalendarUsers, "get", self.viewer, pk=self.calendar.pk,
                                   url="/?q=editor").status_code, 403)
        self.assertEqual(self.call(api.CalendarUsers, "get", self.stranger, pk=self.calendar.pk,
                                   url="/?q=editor").status_code, 404)
        result = self.call(api.CalendarUsers, "get", self.owner, pk=self.calendar.pk, url="/?q=editor")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data["results"], [{"id": self.editor.pk, "label": "editor"}])

    def test_event_validation_type_isolation_and_time_overlap(self):
        foreign_type = EventType.objects.create(calendar=self.other, name="Private")
        self.assertEqual(self.call(api.Events, "post", self.owner, pk=self.calendar.pk,
                                   data=self.event_payload(event_type=foreign_type.pk)).status_code, 400)
        self.assertEqual(self.call(api.Events, "post", self.owner, pk=self.calendar.pk,
                                   data=self.event_payload(end=self.start.isoformat())).status_code, 400)
        created = self.call(api.Events, "post", self.owner, pk=self.calendar.pk, data=self.event_payload())
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.data["color"], "#0088cc")
        query = lambda start, end: self.call(api.Events, "get", self.viewer, pk=self.calendar.pk,
            url=f"/?{urlencode({'start': start.isoformat(), 'end': end.isoformat()})}")
        self.assertEqual(len(query(self.start + timedelta(minutes=30), self.start + timedelta(hours=2)).data["results"]), 1)
        self.assertEqual(len(query(self.start + timedelta(hours=1), self.start + timedelta(hours=2)).data["results"]), 0)
        self.assertEqual(len(query(self.start - timedelta(hours=1), self.start).data["results"]), 0)
        self.assertEqual(self.call(api.EventDetail, "get", self.owner, pk=self.other.pk,
                                   event_id=created.data["id"]).status_code, 404)

    def test_all_day_uses_exclusive_local_midnights(self):
        response = self.call(api.Events, "post", self.owner, pk=self.calendar.pk, data=self.event_payload(
            start="2026-03-28", end="2026-03-30", all_day=True,
        ))
        self.assertEqual(response.status_code, 201)
        self.assertEqual((response.data["start"], response.data["end"]), ("2026-03-28", "2026-03-30"))
        item = Event.objects.get(pk=response.data["id"])
        self.assertEqual(timezone.localtime(item.start).hour, 0)
        self.assertEqual(timezone.localtime(item.end).hour, 0)
        self.assertEqual(timezone.localtime(item.end).date().isoformat(), "2026-03-30")
        self.assertEqual(self.call(api.Events, "post", self.owner, pk=self.calendar.pk, data=self.event_payload(
            start="2026-03-30", end="2026-03-30", all_day=True,
        )).status_code, 400)

    def test_type_management_and_inactive_type(self):
        self.assertEqual(self.call(api.EventTypes, "post", self.editor, pk=self.calendar.pk,
                                   data={"name": "A", "color": "", "is_active": True}).status_code, 403)
        inactive = self.call(api.EventTypeDetail, "patch", self.owner, pk=self.calendar.pk,
                             type_id=self.kind.pk, data={"is_active": False})
        self.assertEqual(inactive.status_code, 200)
        self.assertEqual(self.call(api.Events, "post", self.owner, pk=self.calendar.pk,
                                   data=self.event_payload()).status_code, 400)
        self.assertEqual(self.call(api.EventTypeDetail, "patch", self.owner, pk=self.other.pk,
                                   type_id=self.kind.pk, data={"name": "No"}).status_code, 404)

    def test_model_validation_also_blocks_invalid_direct_writes(self):
        with self.assertRaises(ValidationError):
            Event.objects.create(calendar=self.calendar, created_by=self.owner, title="Bad",
                                 start=self.start, end=self.start)
        with self.assertRaises(ValidationError):
            Event.objects.create(calendar=self.calendar, created_by=self.owner, title="Bad",
                                 start=self.start, end=self.start + timedelta(hours=1),
                                 event_type=EventType.objects.create(calendar=self.other, name="Other"))

    def test_effective_color_tracks_type_and_calendar_changes(self):
        created = self.call(api.Events, "post", self.owner, pk=self.calendar.pk, data=self.event_payload())
        event_id = created.data["id"]
        self.assertEqual(created.data["color"], "#0088cc")
        self.call(api.EventTypeDetail, "patch", self.owner, pk=self.calendar.pk,
                  type_id=self.kind.pk, data={"color": "#aabbcc"})
        self.assertEqual(self.call(api.EventDetail, "get", self.owner, pk=self.calendar.pk,
                                   event_id=event_id).data["color"], "#aabbcc")
        self.call(api.EventTypeDetail, "patch", self.owner, pk=self.calendar.pk,
                  type_id=self.kind.pk, data={"color": ""})
        self.assertEqual(self.call(api.EventDetail, "get", self.owner, pk=self.calendar.pk,
                                   event_id=event_id).data["color"], self.calendar.color)

    def contextual_objects(self, user):
        employee = Employee.objects.create(first_name="Camille", last_name="Test", user=user)
        team = Team.objects.create(name=f"Équipe {user.pk}", leader=employee)
        project = Project.objects.create(name=f"Projet {user.pk}")
        Participant.objects.create(project=project, employee=employee)
        return employee, team, project

    def test_contextual_tabs_and_filtered_api_share_visibility(self):
        from .Calendars import Calendars as CalendarsPlugin
        reader = get_user_model().objects.create_user("tab-reader")
        employee, team, project = self.contextual_objects(reader)
        plugin = CalendarsPlugin()
        request = APIRequestFactory().get("/")
        request.user = reader
        for entity, obj, relation in (("employee", employee, "employees"),
                                      ("team", team, "teams"), ("project", project, "projects")):
            context = {"slot": f"{entity}.detail.tabs", entity: obj}
            self.assertFalse(plugin.has_context_calendars(request, context))
            inherited = Calendar.objects.create(name=f"Inherited {entity}", created_by=self.owner,
                                                access_mode="inherited")
            restricted = Calendar.objects.create(name=f"Restricted {entity}", created_by=self.owner,
                                                 access_mode="restricted")
            getattr(inherited, relation).add(obj)
            getattr(restricted, relation).add(obj)
            self.assertTrue(plugin.has_context_calendars(request, context))
            url = f"/?{entity}_id={obj.pk}"
            result = self.call(api.Calendars, "get", reader, url=url)
            self.assertEqual([item["id"] for item in result.data["results"]], [inherited.pk])
            CalendarAccess.objects.create(calendar=restricted, user=reader, level="edit")
            result = self.call(api.Calendars, "get", reader, url=url)
            self.assertEqual({item["id"] for item in result.data["results"]}, {inherited.pk, restricted.pk})
            self.assertEqual(len(result.data["results"]), 2)
            self.assertEqual(next(item for item in result.data["results"] if item["id"] == restricted.pk)["role"], "edit")
            outsider = get_user_model().objects.create_user(f"tab-outsider-{entity}")
            CalendarAccess.objects.create(calendar=restricted, user=outsider, level="view")
            self.assertEqual(self.call(api.Calendars, "get", outsider, url=url).status_code, 404)
        self.assertEqual(self.call(api.Calendars, "get", reader, url="/?employee_id=0").status_code, 400)
        self.assertEqual(self.call(api.Calendars, "get", reader, url=f"/?employee_id={employee.pk}&team_id={team.pk}").status_code, 400)
        administrator = get_user_model().objects.create_superuser("tab-admin", "tab-admin@example.test", "test")
        admin_result = self.call(api.Calendars, "get", administrator, url=f"/?project_id={project.pk}")
        self.assertEqual({item["role"] for item in admin_result.data["results"]}, {"manage"})
        self.assertEqual(len(admin_result.data["results"]), 2)

    def test_contextual_tab_visibility_query_count_is_constant(self):
        reader = get_user_model().objects.create_user("tab-query-reader")
        employee, _, _ = self.contextual_objects(reader)
        request = APIRequestFactory().get("/")
        request.user = reader
        from .Calendars import Calendars as CalendarsPlugin
        plugin = CalendarsPlugin()
        context = {"slot": "employee.detail.tabs", "employee": employee}
        first = Calendar.objects.create(name="First", created_by=self.owner, access_mode="inherited")
        first.employees.add(employee)
        with CaptureQueriesContext(connection) as baseline:
            self.assertTrue(plugin.has_context_calendars(request, context))
        for index in range(20):
            calendar = Calendar.objects.create(name=f"Extra {index}", created_by=self.owner,
                                               access_mode="inherited")
            calendar.employees.add(employee)
        with CaptureQueriesContext(connection) as expanded:
            self.assertTrue(plugin.has_context_calendars(request, context))
        self.assertLessEqual(len(expanded), len(baseline))

    def test_contextual_contribution_metadata_does_not_reveal_hidden_calendars(self):
        from .Calendars import Calendars as CalendarsPlugin
        reader = get_user_model().objects.create_user("tab-metadata-reader")
        employee, _, _ = self.contextual_objects(reader)
        restricted = Calendar.objects.create(name="Secret", created_by=self.owner, access_mode="restricted")
        restricted.employees.add(employee)
        plugin = CalendarsPlugin()
        with patch.object(registry, "with_mixin", return_value=[plugin]), patch(
            "plugin.ui_api_v1.module_assets", return_value={"module_url": "/static/test.js", "css_urls": [], "asset_version": "1"}
        ) as assets:
            listed = self.call(UIContributions, "get", reader, entity="employee", object_id=employee.pk)
            self.assertEqual(listed.data["contributions"], [])
            self.assertEqual(self.call(UIContributionDetail, "get", reader, entity="employee",
                                       object_id=employee.pk, identity="plugin:calendars:employee-calendars").status_code, 404)
            self.assertEqual(assets.call_count, 0)
            CalendarAccess.objects.create(calendar=restricted, user=reader, level="view")
            listed = self.call(UIContributions, "get", reader, entity="employee", object_id=employee.pk)
            self.assertEqual([item["id"] for item in listed.data["contributions"]],
                             ["plugin:calendars:employee-calendars"])
            self.assertEqual(assets.call_count, 0)
            detail = self.call(UIContributionDetail, "get", reader, entity="employee",
                               object_id=employee.pk, identity="plugin:calendars:employee-calendars")
            self.assertEqual(detail.status_code, 200)
            self.assertEqual(detail.data["context"], {"entity": "employee", "objectId": employee.pk})

    def test_inherited_employee_team_project_and_or_visibility(self):
        reader = get_user_model().objects.create_user("context-reader")
        employee, team, project = self.contextual_objects(reader)
        unrelated = Employee.objects.create(first_name="Autre", last_name="Personne")
        for kind, obj in (("employees", employee), ("teams", team), ("projects", project)):
            calendar = Calendar.objects.create(name=kind, created_by=self.owner, access_mode="inherited")
            getattr(calendar, kind).add(obj)
            detail = self.call(api.CalendarDetail, "get", reader, pk=calendar.pk)
            self.assertEqual(detail.status_code, 200, kind)
            self.assertEqual((detail.data["role"], detail.data["can_edit"], detail.data["can_manage"]),
                             ("view", False, False))
            self.assertEqual(self.call(api.Events, "post", reader, pk=calendar.pk,
                                       data=self.event_payload(event_type=None)).status_code, 403)
        mixed = Calendar.objects.create(name="mixte", created_by=self.owner, access_mode="inherited")
        mixed.employees.add(unrelated)
        mixed.projects.add(project)
        self.assertEqual(self.call(api.CalendarDetail, "get", reader, pk=mixed.pk).status_code, 200)
        self.assertIn(mixed.pk, {item["id"] for item in self.call(api.Calendars, "get", reader).data["results"]})
        self.assertEqual(self.call(api.CalendarDetail, "get", self.viewer, pk=mixed.pk).status_code, 404)

    def test_restricted_explicit_and_global_administrator(self):
        reader = get_user_model().objects.create_user("restricted-reader")
        employee, _, _ = self.contextual_objects(reader)
        calendar = Calendar.objects.create(name="Restricted context", created_by=self.owner, access_mode="restricted")
        calendar.employees.add(employee)
        self.assertEqual(self.call(api.CalendarDetail, "get", reader, pk=calendar.pk).status_code, 404)
        self.assertEqual(self.call(api.Events, "get", reader, pk=calendar.pk,
                                   url="/?start=2026-01-01&end=2026-02-01").status_code, 404)
        for level, can_edit, can_manage in (("view", False, False), ("edit", True, False), ("manage", True, True)):
            CalendarAccess.objects.update_or_create(calendar=calendar, user=reader, defaults={"level": level})
            data = self.call(api.CalendarDetail, "get", reader, pk=calendar.pk).data
            self.assertEqual((data["role"], data["can_edit"], data["can_manage"]), (level, can_edit, can_manage))
        administrator = get_user_model().objects.create_superuser("global-admin", "admin@example.test", "test")
        self.assertIn(calendar.pk, {item["id"] for item in self.call(api.Calendars, "get", administrator).data["results"]})
        self.assertEqual(self.call(api.CalendarDetail, "get", administrator, pk=calendar.pk).data["role"], "manage")
        self.assertEqual(self.call(api.CalendarAttachments, "get", administrator, pk=calendar.pk).status_code, 200)

    def test_general_calendar_uses_core_calendar_capability(self):
        reader = get_user_model().objects.create_user("general-reader")
        general = Calendar.objects.create(name="General", created_by=self.owner, access_mode="inherited")
        self.assertEqual(self.call(api.CalendarDetail, "get", reader, pk=general.pk).status_code, 404)
        permission = Permission.objects.get(content_type__app_label="common", codename="display_calendar")
        reader.user_permissions.add(permission)
        reader = get_user_model().objects.get(pk=reader.pk)
        self.assertEqual(self.call(api.CalendarDetail, "get", reader, pk=general.pk).data["role"], "view")
        associated = Calendar.objects.create(name="Associated", created_by=self.owner, access_mode="inherited")
        associated.employees.add(Employee.objects.create(first_name="Hidden", last_name="Person"))
        self.assertEqual(self.call(api.CalendarDetail, "get", reader, pk=associated.pk).status_code, 404)
        general.access_mode = "restricted"; general.save()
        self.assertEqual(self.call(api.CalendarDetail, "get", reader, pk=general.pk).status_code, 404)

    def test_attachments_are_many_to_many_scoped_and_atomic(self):
        manager = get_user_model().objects.create_user("attachment-manager")
        employee, team, project = self.contextual_objects(manager)
        CalendarAccess.objects.create(calendar=self.calendar, user=manager, level="manage")
        another = Calendar.objects.create(name="Second", created_by=self.owner)
        data = {"employees": [employee.pk], "teams": [team.pk], "projects": [project.pk]}
        self.assertEqual(self.call(api.CalendarAttachments, "patch", manager, pk=self.calendar.pk, data=data).status_code, 200)
        self.assertEqual(self.call(api.CalendarAttachments, "patch", manager, pk=another.pk, data=data).status_code, 404)
        self.assertEqual(self.call(api.CalendarAttachments, "patch", self.editor, pk=self.calendar.pk, data=data).status_code, 403)
        self.assertEqual(self.call(api.CalendarAttachments, "get", self.viewer, pk=self.calendar.pk).status_code, 403)
        self.assertEqual(self.call(api.CalendarDetail, "patch", self.editor, pk=self.calendar.pk,
                                   data={"access_mode": "inherited"}).status_code, 403)
        self.assertEqual(self.call(api.CalendarAttachments, "patch", manager, pk=self.calendar.pk,
                                   data={"employees": [], "projects": [999999]}).status_code, 400)
        hidden = Employee.objects.create(first_name="Hidden", last_name="Employee")
        self.assertEqual(self.call(api.CalendarAttachments, "patch", manager, pk=self.calendar.pk,
                                   data={"employees": [hidden.pk]}).status_code, 400)
        self.assertEqual(self.calendar.employees.count(), 1)
        self.assertEqual(self.calendar.projects.count(), 1)
        self.assertEqual(self.call(api.CalendarAttachments, "patch", manager, pk=self.calendar.pk,
                                   data={"employees": [employee.pk, employee.pk]}).status_code, 200)
        self.assertEqual(self.calendar.employees.count(), 1)
        another.employees.add(employee)
        another.teams.add(team)
        another.projects.add(project)
        self.assertEqual(employee.plugin_calendars.count(), 2)
        self.assertEqual(team.plugin_calendars.count(), 2)
        self.assertEqual(project.plugin_calendars.count(), 2)
        employee.delete()
        self.assertFalse(self.calendar.employees.exists())
        self.assertFalse(self.calendar.teams.exists())  # Team.leader cascades with its Employee.
        self.assertTrue(self.calendar.projects.exists())
        project.delete()
        self.assertFalse(self.calendar.projects.exists())

    def test_calendar_list_queries_do_not_grow_per_attachment(self):
        reader = get_user_model().objects.create_user("many-reader")
        employee, team, project = self.contextual_objects(reader)
        first = Calendar.objects.create(name="Inherited 0", created_by=self.owner, access_mode="inherited")
        first.employees.add(employee)
        with CaptureQueriesContext(connection) as baseline:
            self.call(api.Calendars, "get", reader)
        with CaptureQueriesContext(connection) as managed_baseline:
            self.call(api.Calendars, "get", self.owner, url="/?scope=managed")
        for index in range(12):
            item = Calendar.objects.create(name=f"Inherited {index + 1}", created_by=self.owner,
                                           access_mode="inherited")
            item.employees.add(employee)
            item.teams.add(team)
            item.projects.add(project)
        with CaptureQueriesContext(connection) as expanded:
            response = self.call(api.Calendars, "get", reader)
        with CaptureQueriesContext(connection) as managed_expanded:
            managed_response = self.call(api.Calendars, "get", self.owner, url="/?scope=managed")
        self.assertEqual(len(response.data["results"]), 13)
        self.assertLessEqual(len(expanded), len(baseline) + 2)
        self.assertEqual(len(managed_response.data["results"]), 14)
        self.assertLessEqual(len(managed_expanded), len(managed_baseline) + 2)

    def test_published_calendar_provider_scopes_events_and_permissions(self):
        from django.http import Http404
        from .Calendars import Calendars

        provider = Calendars()
        event = Event.objects.create(calendar=self.calendar, created_by=self.owner,
                                     title="Séminaire", description="Équipe",
                                     start=self.start, end=self.start + timedelta(hours=2),
                                     event_type=self.kind)
        self.assertEqual([item.id for item in provider.list_published_calendars(self.viewer)],
                         [str(self.calendar.pk)])
        self.assertEqual(provider.get_published_calendar(self.viewer, str(self.calendar.pk)).name,
                         self.calendar.name)
        with self.assertRaises(Http404):
            provider.get_published_calendar(self.stranger, str(self.calendar.pk))
        current = provider.get_published_events(self.viewer, str(self.calendar.pk),
                                                self.start + timedelta(hours=1), self.start + timedelta(hours=3))
        self.assertEqual([item.id for item in current], [str(event.pk)])
        self.assertEqual(current[0].title, "Séminaire")
        self.assertEqual(current[0].description, "Équipe")
        self.assertEqual(current[0].color, self.kind.color)
        self.assertEqual(provider.get_published_events(self.viewer, str(self.calendar.pk),
                         self.start + timedelta(hours=2), self.start + timedelta(hours=3)), [])
        CalendarAccess.objects.filter(calendar=self.calendar, user=self.viewer).delete()
        with self.assertRaises(Http404):
            provider.get_published_events(self.viewer, str(self.calendar.pk), self.start,
                                          self.start + timedelta(days=1))

    def test_published_assignments_only_include_visible_employee_names(self):
        from .Calendars import Calendars

        employee = Employee.objects.create(first_name="Ada", last_name="Visible")
        assigned = Calendar.objects.create(name="Assignments", created_by=self.owner,
                                           calendar_type="assignments")
        CalendarAccess.objects.create(calendar=assigned, user=self.viewer, level="view")
        event = Event.objects.create(calendar=assigned, created_by=self.owner, title="Shift",
                                     start=self.start, end=self.start + timedelta(hours=1))
        event.employees.add(employee)
        provider = Calendars()
        events = provider.get_published_events(self.viewer, str(assigned.pk), self.start,
                                               self.start + timedelta(days=1))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].metadata["assignees"], [])
        self.viewer.user_permissions.add(Permission.objects.get(codename="view_employee"))
        self.viewer = get_user_model().objects.get(pk=self.viewer.pk)
        visible = provider.get_published_events(self.viewer, str(assigned.pk), self.start,
                                                self.start + timedelta(days=1))
        self.assertEqual(visible[0].metadata["assignees"], [{"name": str(employee)}])
        self.assertNotIn("email", visible[0].metadata["assignees"][0])

    def test_published_calendars_include_each_attachment_kind_once(self):
        from .Calendars import Calendars

        employee, team, project = self.contextual_objects(self.owner)
        kinds = []
        for index, relation in enumerate(("employees", "teams", "projects"), start=1):
            calendar = Calendar.objects.create(name=f"Attached {index}", created_by=self.owner)
            getattr(calendar, relation).add({"employees": employee, "teams": team, "projects": project}[relation])
            CalendarAccess.objects.create(calendar=calendar, user=self.viewer, level="view")
            kinds.append(calendar)
        multiple = Calendar.objects.create(name="Multiple", created_by=self.owner)
        multiple.employees.add(employee); multiple.teams.add(team); multiple.projects.add(project)
        CalendarAccess.objects.create(calendar=multiple, user=self.viewer, level="view")
        published = Calendars().list_published_calendars(self.viewer)
        ids = [item.id for item in published]
        for calendar in (*kinds, multiple):
            self.assertEqual(ids.count(str(calendar.pk)), 1)

    def test_published_assignment_filter_titles_and_authorized_links(self):
        from .Calendars import Calendars

        employee, team, project = self.contextual_objects(self.viewer)
        other = Employee.objects.create(first_name="Zoé", last_name="Autre")
        self.viewer.user_permissions.add(Permission.objects.get(codename="view_employee"))
        self.viewer = get_user_model().objects.get(pk=self.viewer.pk)
        calendar = Calendar.objects.create(name="Astreintes", created_by=self.owner, calendar_type="assignments")
        CalendarAccess.objects.create(calendar=calendar, user=self.viewer, level="view")
        kind = EventType.objects.create(calendar=calendar, name="Maintenance")
        own = Event.objects.create(calendar=calendar, created_by=self.owner, event_type=kind,
                                   title="Vérification", description="À contrôler",
                                   start=self.start, end=self.start + timedelta(hours=1))
        own.employees.add(employee, other)
        foreign = Event.objects.create(calendar=calendar, created_by=self.owner, title="Autre",
                                       start=self.start, end=self.start + timedelta(hours=1))
        foreign.employees.add(other)
        Event.objects.create(calendar=calendar, created_by=self.owner, title="Libre",
                             start=self.start, end=self.start + timedelta(hours=1))
        plugin = Calendars()
        self.assertEqual(plugin.get_published_calendar(self.viewer, str(calendar.pk)).options, ("assigned_to_me",))
        full = plugin.get_published_events(self.viewer, str(calendar.pk), self.start,
                                           self.start + timedelta(days=1))
        self.assertEqual(len(full), 3)
        self.assertIn("Maintenance — Vérification —", full[0].title)
        self.assertIn(str(employee), full[0].title)
        self.assertIn(str(other), full[0].title)
        self.assertEqual(full[0].metadata["links"], ["plugins/calendars/calendars"])
        filtered = plugin.get_published_events(self.viewer, str(calendar.pk), self.start,
                                               self.start + timedelta(days=1), options={"assigned_to_me": True})
        self.assertEqual([item.id for item in filtered], [str(own.pk)])
        calendar.employees.add(employee); calendar.teams.add(team); calendar.projects.add(project)
        linked = plugin.get_published_events(self.viewer, str(calendar.pk), self.start,
                                             self.start + timedelta(days=1), options={"assigned_to_me": True})
        self.assertEqual(linked[0].metadata["links"], [
            f"employees/{employee.pk}/plugins/calendars/employee-calendars",
            f"teams/{team.pk}/plugins/calendars/team-calendars",
            f"projects/{project.pk}/plugins/calendars/project-calendars",
        ])
        self.assertEqual(plugin.get_published_events(self.owner, str(calendar.pk), self.start,
                                                     self.start + timedelta(days=1), options={"assigned_to_me": True}), [])
        self.assertEqual(plugin.get_published_calendar(self.viewer, str(self.calendar.pk)).options, ())
        with self.assertRaises(ValueError):
            plugin.get_published_events(self.viewer, str(self.calendar.pk), self.start,
                                        self.start + timedelta(days=1), options={"assigned_to_me": True})
