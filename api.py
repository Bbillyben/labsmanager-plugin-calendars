"""Session-authenticated, object-scoped REST API for independent calendars."""

from datetime import date, datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, Prefetch, Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions, serializers
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from project.models import Participant, Project
from staff.models import Employee, Team, TeamMate
from staff.permissions_v1 import has_global_employee_visibility
from staff.team_api_v1 import visible_teams
from plugin.ui_contributions import resolve_context

from .models import Calendar, CalendarAccess, Event, EventType


LEVEL = {"view": 1, "edit": 2, "manage": 3}


def model_error(error):
    if hasattr(error, "message_dict"):
        raise ValidationError(error.message_dict) from error
    raise ValidationError(error.messages) from error


def role_for(calendar, user):
    if user.is_superuser or calendar.created_by_id == user.pk:
        return "manage"
    if hasattr(calendar, "my_access"):
        explicit = calendar.my_access[0].level if calendar.my_access else None
    else:
        explicit = CalendarAccess.objects.filter(calendar=calendar, user=user).values_list("level", flat=True).first()
    if explicit:
        return explicit
    if calendar.access_mode == "inherited" and getattr(calendar, "inherited_view", False):
        return "view"
    return None


def visible_objects(user):
    """Reuse the querysets that back the core Employee, Team and Project lists."""
    employees = (Employee.objects.all() if has_global_employee_visibility(user) else
                 Employee.get_instances_for_user("view", user, Employee.objects.all()))
    return employees, visible_teams(user), Project.get_instances_for_user("view", user, Project.objects.all())


def visible_calendars(user):
    employees, teams, projects = visible_objects(user)
    employee_links = Calendar.employees.through.objects.filter(calendar_id=OuterRef("pk"))
    team_links = Calendar.teams.through.objects.filter(calendar_id=OuterRef("pk"))
    project_links = Calendar.projects.through.objects.filter(calendar_id=OuterRef("pk"))
    base = Calendar.objects.annotate(
        inherited_employee=Exists(employee_links.filter(employee_id__in=employees.values("pk"))),
        inherited_team=Exists(team_links.filter(team_id__in=teams.values("pk"))),
        inherited_project=Exists(project_links.filter(project_id__in=projects.values("pk"))),
        has_employee=Exists(employee_links), has_team=Exists(team_links), has_project=Exists(project_links),
        has_events=Exists(Event.objects.filter(calendar_id=OuterRef("pk"))),
    )
    general = user.has_perm("common.display_calendar") or user.has_perm("leave.view_leave")
    base = base.annotate(inherited_view=(
        Q(inherited_employee=True) | Q(inherited_team=True) | Q(inherited_project=True) |
        (Q(has_employee=False, has_team=False, has_project=False) if general else Q(pk__isnull=True))
    ))
    if not user.is_superuser:
        base = base.filter(Q(created_by=user) | Q(accesses__user=user) |
                           Q(access_mode="inherited", inherited_view=True)).distinct()
    return base.prefetch_related(Prefetch(
        "accesses", queryset=CalendarAccess.objects.filter(user=user), to_attr="my_access"
    ))


CONTEXT_RELATIONS = {"employee": "employees", "team": "teams", "project": "projects"}


def associated_calendars(user, entity, object_id):
    """Filter the same visibility queryset used by every calendar endpoint."""
    if entity not in CONTEXT_RELATIONS:
        raise Http404
    return visible_calendars(user).filter(
        **{f"{CONTEXT_RELATIONS[entity]}__pk": object_id}
    ).distinct()


def contextual_calendars(request, entity, object_id):
    """Authorize the detail object before exposing associated calendars."""
    context = resolve_context(entity, request, object_id)
    return associated_calendars(request.user, entity, context[entity].pk)


def calendar_for(request, pk, required="view"):
    calendar = get_object_or_404(visible_calendars(request.user), pk=pk)
    role = role_for(calendar, request.user)
    if role not in LEVEL or LEVEL[role] < LEVEL[required]:
        raise PermissionDenied("Insufficient calendar access.")
    return calendar


def calendar_data(calendar, user):
    role = role_for(calendar, user)
    return {
        "id": calendar.pk, "name": calendar.name, "description": calendar.description,
        "color": calendar.color, "access_mode": calendar.access_mode, "calendar_type": calendar.calendar_type,
        "has_events": calendar.has_events if hasattr(calendar, "has_events") else calendar.events.exists(),
        "created_by": calendar.created_by_id,
        "created_at": calendar.created_at, "updated_at": calendar.updated_at,
        "role": role, "can_edit": LEVEL[role] >= LEVEL["edit"],
        "can_manage": LEVEL[role] >= LEVEL["manage"],
    }


def type_data(item):
    return {"id": item.pk, "calendar": item.calendar_id, "name": item.name,
            "color": item.color, "is_active": item.is_active}


def visible_employee_queryset(user):
    return visible_objects(user)[0]


def eligible_employees(calendar, user):
    """Current members of attached objects, intersected with core Employee visibility."""
    visible = visible_employee_queryset(user).filter(is_active=True)
    project_ids = calendar.projects.values("pk")
    team_ids = calendar.teams.values("pk")
    direct_ids = calendar.employees.values("pk")
    if not (project_ids.exists() or team_ids.exists() or direct_ids.exists()):
        return visible.distinct()
    today = timezone.localdate()
    current = (Q(start_date__isnull=True) | Q(start_date__lte=today)) & (
        Q(end_date__isnull=True) | Q(end_date__gte=today))
    participants = Participant.objects.filter(current, project_id__in=project_ids).values("employee_id")
    mates = TeamMate.objects.filter(current, team_id__in=team_ids).values("employee_id")
    leaders = Team.objects.filter(pk__in=team_ids).values("leader_id")
    return visible.filter(Q(pk__in=direct_ids) | Q(pk__in=participants) |
                          Q(pk__in=mates) | Q(pk__in=leaders)).distinct()


def event_employees(item, user):
    if item.calendar.calendar_type != "assignments":
        return []
    if hasattr(item, "visible_assignees"):
        assignees = item.visible_assignees
    else:
        assignees = item.employees.filter(pk__in=visible_employee_queryset(user).values("pk"))
    return [{"id": employee.pk, "label": str(employee)} for employee in assignees]


def event_data(item, user):
    local = timezone.localtime
    return {
        "id": item.pk, "calendar": item.calendar_id, "event_type": item.event_type_id,
        "event_type_name": item.event_type.name if item.event_type else "",
        "title": item.title, "description": item.description,
        "start": local(item.start).date().isoformat() if item.all_day else item.start.isoformat(),
        "end": local(item.end).date().isoformat() if item.all_day else item.end.isoformat(),
        "all_day": item.all_day, "color": (item.event_type.color if item.event_type and item.event_type.color else item.calendar.color),
        "created_by": item.created_by_id, "created_at": item.created_at, "updated_at": item.updated_at,
        "employees": event_employees(item, user),
    }


def user_data(user):
    name = user.get_full_name().strip()
    return {"id": user.pk, "label": f"{name} ({user.get_username()})" if name else user.get_username()}


def access_data(item):
    return {"user": user_data(item.user), "level": item.level}


def period_bound(raw):
    if not raw:
        raise ValidationError({"period": "Both start and end are required."})
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.combine(date.fromisoformat(raw), time.min)
        except ValueError as error:
            raise ValidationError({"period": "Use ISO dates or datetimes."}) from error
    return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed


class CalendarWrite(serializers.ModelSerializer):
    class Meta:
        model = Calendar
        fields = ("name", "description", "color", "access_mode", "calendar_type")


ATTACHMENTS = {"employees": Employee, "teams": Team, "projects": Project}


def attachment_data(calendar):
    return {
        "employees": [{"id": item.pk, "label": str(item)} for item in calendar.employees.all()],
        "teams": [{"id": item.pk, "label": str(item)} for item in calendar.teams.all()],
        "projects": [{"id": item.pk, "label": str(item)} for item in calendar.projects.all()],
    }


def validate_attachments(user, calendar, payload):
    visible = dict(zip(ATTACHMENTS, visible_objects(user)))
    result = {}
    for name in ATTACHMENTS:
        if name not in payload:
            continue
        ids = payload[name]
        if not isinstance(ids, list) or any(type(pk) is not int or pk <= 0 for pk in ids):
            raise ValidationError({name: "Expected a list of positive object ids."})
        unique = set(ids)
        existing = set(getattr(calendar, name).values_list("pk", flat=True))
        new_ids = unique - existing
        if visible[name].filter(pk__in=new_ids).distinct().count() != len(new_ids):
            raise ValidationError({name: "Unknown or inaccessible object."})
        result[name] = unique
    return result


class TypeWrite(serializers.ModelSerializer):
    class Meta:
        model = EventType
        fields = ("name", "color", "is_active")


class EventWrite(serializers.ModelSerializer):
    start = serializers.CharField()
    end = serializers.CharField()
    event_type = serializers.PrimaryKeyRelatedField(queryset=EventType.objects.all(), allow_null=True, required=False)
    employees = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False)

    class Meta:
        model = Event
        fields = ("title", "description", "start", "end", "all_day", "event_type", "employees")

    def validate(self, attrs):
        requested = attrs.pop("employees", None)
        if requested is not None:
            calendar = self.context["calendar"]
            if calendar.calendar_type != "assignments" and requested:
                raise ValidationError({"employees": "This calendar does not allow assignments."})
            existing = set(self.instance.employees.values_list("pk", flat=True)) if self.instance else set()
            visible_existing = set(visible_employee_queryset(self.context["user"]).filter(pk__in=existing).values_list("pk", flat=True))
            requested_ids = set(requested)
            if len(requested_ids) != len(requested):
                raise ValidationError({"employees": "Duplicate employees are not allowed."})
            added = requested_ids - existing
            if eligible_employees(calendar, self.context["user"]).filter(pk__in=added).count() != len(added):
                raise ValidationError({"employees": "Unknown, inaccessible or ineligible employee."})
            # Invisible historical assignments remain intact; they cannot be disclosed or edited here.
            self.assignment_ids = requested_ids | (existing - visible_existing)
        all_day = attrs.get("all_day", self.instance.all_day if self.instance else False)
        for field in ("start", "end"):
            if field not in attrs:
                continue
            raw = attrs[field]
            if all_day:
                try:
                    if len(raw) != 10:
                        raise ValueError
                    attrs[field] = timezone.make_aware(datetime.combine(date.fromisoformat(raw), time.min))
                except ValueError as error:
                    raise ValidationError({field: "Use YYYY-MM-DD for an all-day event."}) from error
            else:
                attrs[field] = serializers.DateTimeField().run_validation(raw)
        candidate = Event(calendar=self.context["calendar"], created_by=self.context["user"])
        if self.instance:
            candidate.pk = self.instance.pk
            for field in ("title", "description", "start", "end", "all_day", "event_type"):
                setattr(candidate, field, getattr(self.instance, field))
        for field, value in attrs.items():
            setattr(candidate, field, value)
        if candidate.event_type_id and candidate.event_type.calendar_id != candidate.calendar_id:
            raise ValidationError({"event_type": "The event type belongs to another calendar."})
        if candidate.event_type_id and not candidate.event_type.is_active and (
            not self.instance or candidate.event_type_id != self.instance.event_type_id
        ):
            raise ValidationError({"event_type": "This event type is inactive."})
        try:
            candidate.clean()
        except DjangoValidationError as error:
            model_error(error)
        return attrs


class CalendarAPI(APIView):
    authentication_classes = (SessionAuthentication,)
    permission_classes = (permissions.IsAuthenticated,)


class Calendars(CalendarAPI):
    def get(self, request):
        scope = request.query_params.get("scope")
        if scope not in (None, "general", "managed"):
            raise ValidationError({"scope": "Unknown calendar scope."})
        filters = [(entity, request.query_params.get(f"{entity}_id"))
                   for entity in CONTEXT_RELATIONS if f"{entity}_id" in request.query_params]
        if len(filters) > 1 or (scope is not None and filters):
            raise ValidationError({"context": "Supply only one object filter."})
        if filters:
            entity, raw_id = filters[0]
            if not raw_id or not raw_id.isdecimal() or int(raw_id) <= 0:
                raise ValidationError({f"{entity}_id": "Expected a positive object id."})
            calendars = contextual_calendars(request, entity, int(raw_id))
        else:
            calendars = visible_calendars(request.user)
            if scope == "general":
                calendars = calendars.filter(has_employee=False, has_team=False, has_project=False)
            elif scope == "managed" and not request.user.is_superuser:
                calendars = calendars.filter(
                    Q(created_by=request.user) |
                    Q(accesses__user=request.user, accesses__level="manage")
                ).distinct()
            if scope == "managed":
                calendars = calendars.prefetch_related("employees", "teams", "projects")
        return Response({"results": [
            {**calendar_data(item, request.user), **({"attachments": attachment_data(item)} if scope == "managed" else {})}
            for item in calendars
        ]})

    def post(self, request):
        write = CalendarWrite(data=request.data)
        write.is_valid(raise_exception=True)
        try:
            calendar = write.save(created_by=request.user)
        except DjangoValidationError as error:
            model_error(error)
        return Response(calendar_data(calendar, request.user), status=201)


class CalendarDetail(CalendarAPI):
    def get(self, request, pk):
        return Response(calendar_data(calendar_for(request, pk), request.user))

    def patch(self, request, pk):
        calendar_for(request, pk, "manage")
        with transaction.atomic():
            calendar = Calendar.objects.select_for_update().get(pk=pk)
            write = CalendarWrite(calendar, data=request.data, partial=True)
            write.is_valid(raise_exception=True)
            try:
                write.save()
            except DjangoValidationError as error:
                model_error(error)
        return Response(calendar_data(calendar, request.user))


class EligibleEmployees(CalendarAPI):
    def get(self, request, pk):
        calendar = calendar_for(request, pk)
        if calendar.calendar_type != "assignments":
            raise ValidationError({"calendar_type": "This calendar does not allow assignments."})
        raw_id = request.query_params.get("id")
        employees = eligible_employees(calendar, request.user)
        if raw_id is not None:
            employees = employees.filter(pk=int(raw_id)) if raw_id.isdecimal() and int(raw_id) > 0 else employees.none()
        else:
            term = request.query_params.get("q", "").strip()[:100]
            if term:
                employees = employees.filter(Q(first_name__icontains=term) | Q(last_name__icontains=term))
        employees = list(employees.order_by("last_name", "first_name", "pk")[:11])
        return Response({"results": [{"id": item.pk, "label": str(item)} for item in employees[:10]],
                         "has_more": len(employees) > 10})


class CalendarResources(CalendarAPI):
    """Visible eligible rows, scoped to relevant employees for an unbound calendar."""

    def get(self, request, pk):
        calendar = calendar_for(request, pk)
        if calendar.calendar_type != "assignments":
            raise ValidationError({"calendar_type": "This calendar does not allow assignments."})
        start = period_bound(request.query_params.get("start"))
        end = period_bound(request.query_params.get("end"))
        if end <= start or end - start > timedelta(days=370):
            raise ValidationError({"period": "Choose a positive interval of at most 370 days."})
        employees = eligible_employees(calendar, request.user)
        if not (calendar.employees.exists() or calendar.teams.exists() or calendar.projects.exists()):
            assigned = Event.objects.filter(calendar=calendar, start__lt=end, end__gt=start).values("employees__pk")
            employees = employees.filter(pk__in=assigned)
        employees = employees.order_by("last_name", "first_name", "pk")
        return Response({"results": [{"id": item.pk, "label": str(item)} for item in employees]})


class CalendarAttachments(CalendarAPI):
    def get(self, request, pk):
        return Response(attachment_data(calendar_for(request, pk, "manage")))

    def patch(self, request, pk):
        calendar = calendar_for(request, pk, "manage")
        if not request.data or any(key not in ATTACHMENTS for key in request.data):
            raise ValidationError({"attachments": "Supply employees, teams or projects."})
        objects = validate_attachments(request.user, calendar, request.data)
        with transaction.atomic():
            for name, items in objects.items():
                getattr(calendar, name).set(items)
        return Response(attachment_data(calendar))


class CalendarUsers(CalendarAPI):
    def get(self, request, pk):
        calendar_for(request, pk, "manage")
        users = get_user_model().objects.filter(is_active=True)
        identifier = request.query_params.get("id")
        if identifier is not None:
            users = users.filter(pk=identifier) if identifier.isdecimal() else users.none()
        else:
            term = request.query_params.get("q", "").strip()[:100]
            if term:
                users = users.filter(Q(username__icontains=term) | Q(first_name__icontains=term) | Q(last_name__icontains=term))
        users = list(users.order_by("username")[:11])
        return Response({"results": [user_data(user) for user in users[:10]], "has_more": len(users) > 10})


class CalendarMembers(CalendarAPI):
    def get(self, request, pk):
        calendar = calendar_for(request, pk, "manage")
        members = CalendarAccess.objects.filter(calendar=calendar).select_related("user").order_by("user__username")
        return Response({"owner": user_data(calendar.created_by), "results": [access_data(item) for item in members]})

    def post(self, request, pk):
        calendar = calendar_for(request, pk, "manage")
        user_id, level = request.data.get("user_id"), request.data.get("level")
        if type(user_id) is not int or user_id <= 0 or level not in LEVEL:
            raise ValidationError({"member": "A user id and view, edit or manage level are required."})
        user = get_object_or_404(get_user_model().objects.filter(is_active=True), pk=user_id)
        if user.pk == calendar.created_by_id:
            raise ValidationError({"user_id": "The owner already has manage access."})
        try:
            with transaction.atomic():
                item, created = CalendarAccess.objects.update_or_create(calendar=calendar, user=user, defaults={"level": level})
        except DjangoValidationError as error:
            model_error(error)
        return Response(access_data(item), status=201 if created else 200)


class CalendarMemberDetail(CalendarAPI):
    def patch(self, request, pk, user_id):
        calendar = calendar_for(request, pk, "manage")
        item = get_object_or_404(CalendarAccess, calendar=calendar, user_id=user_id)
        level = request.data.get("level")
        if level not in LEVEL:
            raise ValidationError({"level": "Use view, edit or manage."})
        item.level = level
        try:
            item.save(update_fields=("level",))
        except DjangoValidationError as error:
            model_error(error)
        return Response(access_data(item))

    def delete(self, request, pk, user_id):
        calendar = calendar_for(request, pk, "manage")
        get_object_or_404(CalendarAccess, calendar=calendar, user_id=user_id).delete()
        return Response(status=204)


class EventTypes(CalendarAPI):
    def get(self, request, pk):
        calendar = calendar_for(request, pk)
        return Response({"results": [type_data(item) for item in calendar.event_types.all()]})

    def post(self, request, pk):
        calendar = calendar_for(request, pk, "manage")
        write = TypeWrite(data=request.data)
        write.is_valid(raise_exception=True)
        try:
            item = write.save(calendar=calendar)
        except (DjangoValidationError, IntegrityError) as error:
            if isinstance(error, IntegrityError):
                raise ValidationError({"name": "This event type already exists."}) from error
            model_error(error)
        return Response(type_data(item), status=201)


class EventTypeDetail(CalendarAPI):
    def patch(self, request, pk, type_id):
        calendar = calendar_for(request, pk, "manage")
        item = get_object_or_404(EventType, calendar=calendar, pk=type_id)
        write = TypeWrite(item, data=request.data, partial=True)
        write.is_valid(raise_exception=True)
        try:
            write.save()
        except DjangoValidationError as error:
            model_error(error)
        return Response(type_data(item))

    def delete(self, request, pk, type_id):
        calendar = calendar_for(request, pk, "manage")
        item = get_object_or_404(EventType, calendar=calendar, pk=type_id)
        if item.events.exists():
            return Response({"detail": "Deactivate a type used by events."}, status=409)
        item.delete()
        return Response(status=204)


class Events(CalendarAPI):
    def get(self, request, pk):
        calendar = calendar_for(request, pk)
        start = period_bound(request.query_params.get("start"))
        end = period_bound(request.query_params.get("end"))
        if end <= start or end - start > timedelta(days=370):
            raise ValidationError({"period": "Choose a positive interval of at most 370 days."})
        items = Event.objects.filter(calendar=calendar, start__lt=end, end__gt=start).select_related("event_type", "calendar")
        if calendar.calendar_type == "assignments":
            items = items.prefetch_related(Prefetch("employees", queryset=visible_employee_queryset(request.user), to_attr="visible_assignees"))
        return Response({"results": [event_data(item, request.user) for item in items]})

    def post(self, request, pk):
        calendar_for(request, pk, "edit")
        with transaction.atomic():
            calendar = Calendar.objects.select_for_update().get(pk=pk)
            write = EventWrite(data=request.data, context={"calendar": calendar, "user": request.user})
            write.is_valid(raise_exception=True)
            try:
                item = write.save(calendar=calendar, created_by=request.user)
                if hasattr(write, "assignment_ids"):
                    item.employees.set(write.assignment_ids)
            except DjangoValidationError as error:
                model_error(error)
        return Response(event_data(item, request.user), status=201)


class EventDetail(CalendarAPI):
    def get(self, request, pk, event_id):
        calendar = calendar_for(request, pk)
        item = get_object_or_404(Event.objects.select_related("event_type", "calendar"), calendar=calendar, pk=event_id)
        return Response(event_data(item, request.user))

    def patch(self, request, pk, event_id):
        calendar = calendar_for(request, pk, "edit")
        item = get_object_or_404(Event.objects.select_related("event_type", "calendar"), calendar=calendar, pk=event_id)
        with transaction.atomic():
            calendar = Calendar.objects.select_for_update().get(pk=pk)
            write = EventWrite(item, data=request.data, partial=True, context={"calendar": calendar, "user": request.user})
            write.is_valid(raise_exception=True)
            try:
                write.save()
                if hasattr(write, "assignment_ids"):
                    item.employees.set(write.assignment_ids)
            except DjangoValidationError as error:
                model_error(error)
        return Response(event_data(item, request.user))

    def delete(self, request, pk, event_id):
        calendar = calendar_for(request, pk, "edit")
        get_object_or_404(Event, calendar=calendar, pk=event_id).delete()
        return Response(status=204)
