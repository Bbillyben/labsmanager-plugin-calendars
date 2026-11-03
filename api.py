"""Session-authenticated, object-scoped REST API for independent calendars."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Exists, F, OuterRef, Prefetch, Q
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

from .models import Calendar, CalendarAccess, Event, EventOccurrenceException, EventType, ExternalCalendarSource
from .external import ExternalFeedError, _public_addresses, occurrences, validate_url
from .native import effective_employees, native_occurrences
from .recurrence import RecurrenceError, occurrence_exists


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


def visible_calendars(user, *, include_inactive=False):
    employees, teams, projects = visible_objects(user)
    employee_links = Calendar.employees.through.objects.filter(calendar_id=OuterRef("pk"))
    team_links = Calendar.teams.through.objects.filter(calendar_id=OuterRef("pk"))
    project_links = Calendar.projects.through.objects.filter(calendar_id=OuterRef("pk"))
    base = (Calendar.objects if include_inactive else Calendar.objects.filter(is_active=True)).annotate(
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


def calendar_for(request, pk, required="view", *, management_read=False):
    calendar = get_object_or_404(visible_calendars(
        request.user, include_inactive=required == "manage" or management_read), pk=pk)
    role = role_for(calendar, request.user)
    if not calendar.is_active and required != "manage" and (not management_read or role != "manage"):
        raise Http404
    if role not in LEVEL or LEVEL[role] < LEVEL[required]:
        raise PermissionDenied("Insufficient calendar access.")
    return calendar


def calendar_data(calendar, user):
    role = role_for(calendar, user)
    external = calendar.calendar_type == "external"
    source = ExternalCalendarSource.objects.filter(calendar=calendar).first() if external else None
    data = {
        "id": calendar.pk, "name": calendar.name, "description": calendar.description,
        "color": calendar.color, "access_mode": calendar.access_mode, "calendar_type": calendar.calendar_type,
        "is_active": calendar.is_active,
        "has_events": (source.events.filter(active=True).exists() if source and source.enabled else False) if external else
                      (calendar.has_events if hasattr(calendar, "has_events") else calendar.events.exists()),
        "created_by": calendar.created_by_id,
        "created_at": calendar.created_at, "updated_at": calendar.updated_at,
        "role": role, "can_edit": not external and LEVEL[role] >= LEVEL["edit"],
        "can_manage": LEVEL[role] >= LEVEL["manage"],
    }
    if external and role == "manage":
        data["external"] = {
            "has_url": bool(source and source.url), "enabled": source.enabled if source else False,
            "interval_minutes": source.interval_minutes if source else 60,
            "status": source.status if source else "pending",
            "last_attempt": source.last_attempt if source else None,
            "last_success": source.last_success if source else None,
            "last_error": source.last_error if source else "",
            "statistics": source.statistics if source else {},
        }
    return data


def external_options(payload, calendar_type, *, create=False):
    """Remove write-only source options before using the existing serializer."""
    fields = {"url", "interval_minutes", "enabled"}
    options = {key: payload[key] for key in fields if key in payload}
    if options and calendar_type != "external":
        raise ValidationError({"calendar_type": "Source options require an external calendar."})
    if calendar_type == "external":
        if create and not options.get("url"):
            raise ValidationError({"url": "A subscription URL is required."})
        if "url" in options:
            try:
                _, host = validate_url(options["url"])
                _public_addresses(host)
            except ExternalFeedError as exc:
                raise ValidationError({"url": str(exc)}) from exc
        if "interval_minutes" in options and (type(options["interval_minutes"]) is not int or
                                               not 15 <= options["interval_minutes"] <= 10080):
            raise ValidationError({"interval_minutes": "Choose 15 minutes to seven days."})
        if "enabled" in options and type(options["enabled"]) is not bool:
            raise ValidationError({"enabled": "Expected a boolean."})
    return {key: value for key, value in payload.items() if key not in fields}, options


def save_external_options(calendar, options):
    if calendar.calendar_type != "external":
        return
    source, _ = ExternalCalendarSource.objects.get_or_create(
        calendar=calendar, defaults={"url": options.get("url", "")})
    changed_url = "url" in options and options["url"] != source.url
    for key, value in options.items():
        setattr(source, key, value)
    if changed_url:
        source.status = "pending"
        source.last_error = ""
        source.sync_requested = True
    source.save()


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
    # Recurring all-day series use their declared zone, including the series detail response.
    date_zone = ZoneInfo(item.recurrence_timezone) if item.recurrence_rule and item.recurrence_timezone else None
    return {
        "id": item.pk, "calendar": item.calendar_id, "event_type": item.event_type_id,
        "event_type_name": item.event_type.name if item.event_type else "",
        "title": item.title, "description": item.description, "location": item.location,
        "start": local(item.start, date_zone).date().isoformat() if item.all_day else item.start.isoformat(),
        "end": local(item.end, date_zone).date().isoformat() if item.all_day else item.end.isoformat(),
        "all_day": item.all_day, "color": (item.event_type.color if item.event_type and item.event_type.color else item.calendar.color),
        "created_by": item.created_by_id, "created_at": item.created_at, "updated_at": item.updated_at,
        "employees": event_employees(item, user),
        "recurrence_rule": item.recurrence_rule, "recurrence_timezone": item.recurrence_timezone,
        "rdates": item.rdates, "exdates": item.exdates,
        "exceptions_revision": item.exceptions_revision,
    }


def occurrence_data(row, user, visible_ids):
    """Apply each explicit override while preserving the legacy point-event shape."""
    item, exception, start, end, recurrence_id = row
    result = event_data(item, user)
    if recurrence_id is None:
        return result
    kind = exception.event_type if exception and exception.event_type_override else item.event_type
    all_day = exception.all_day if exception and exception.timeslot_override else item.all_day
    zone = ZoneInfo(item.recurrence_timezone)
    result.update({
        "id": f"{item.pk}:{recurrence_id.isoformat()}", "series_id": item.pk,
        "recurrence_id": recurrence_id.isoformat(), "is_exception": exception is not None,
        "overrides": {
            "title": bool(exception and exception.title_override),
            "description": bool(exception and exception.description_override),
            "location": bool(exception and exception.location_override),
            "event_type": bool(exception and exception.event_type_override),
            "timeslot": bool(exception and exception.timeslot_override),
            "employees": bool(exception and exception.employees_override),
        },
        "title": exception.title if exception and exception.title_override else item.title,
        "description": exception.description if exception and exception.description_override else item.description,
        "location": exception.location if exception and exception.location_override else item.location,
        "event_type": kind.pk if kind else None, "event_type_name": kind.name if kind else "",
        "start": start.astimezone(zone).date().isoformat() if all_day else start.isoformat(),
        "end": end.astimezone(zone).date().isoformat() if all_day else end.isoformat(),
        "all_day": all_day, "color": kind.color if kind and kind.color else item.calendar.color,
        "employees": ([{"id": employee.pk, "label": str(employee)} for employee in effective_employees(item, exception)
                       if employee.pk in visible_ids] if item.calendar.calendar_type == "assignments" else []),
    })
    return result


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
        fields = ("name", "description", "color", "access_mode", "calendar_type", "is_active")


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
    recurrence_rule = serializers.CharField(required=False, allow_blank=True)
    recurrence_timezone = serializers.CharField(required=False, allow_blank=True)
    rdates = serializers.ListField(child=serializers.CharField(), required=False)
    exdates = serializers.ListField(child=serializers.CharField(), required=False)

    class Meta:
        model = Event
        fields = ("title", "description", "location", "start", "end", "all_day", "event_type", "employees",
                  "recurrence_rule", "recurrence_timezone", "rdates", "exdates")

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
        recurrence_zone = attrs.get("recurrence_timezone", self.instance.recurrence_timezone if self.instance else "")
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            zone = ZoneInfo(recurrence_zone) if recurrence_zone else timezone.get_current_timezone()
        except ZoneInfoNotFoundError as error:
            raise ValidationError({"recurrence_timezone": "Use an IANA timezone."}) from error
        for field in ("start", "end"):
            if field not in attrs:
                continue
            raw = attrs[field]
            if all_day:
                try:
                    if len(raw) != 10:
                        raise ValueError
                    attrs[field] = datetime.combine(date.fromisoformat(raw), time.min, tzinfo=zone)
                except ValueError as error:
                    raise ValidationError({field: "Use YYYY-MM-DD for an all-day event."}) from error
            else:
                attrs[field] = serializers.DateTimeField().run_validation(raw)
        candidate = Event(calendar=self.context["calendar"], created_by=self.context["user"])
        if self.instance:
            candidate.pk = self.instance.pk
            for field in ("title", "description", "location", "start", "end", "all_day", "event_type",
                          "recurrence_rule", "recurrence_timezone", "rdates", "exdates"):
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
            calendars = visible_calendars(request.user, include_inactive=scope == "managed")
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
        payload, options = external_options(request.data, request.data.get("calendar_type", "events"), create=True)
        write = CalendarWrite(data=payload)
        write.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                calendar = write.save(created_by=request.user)
                save_external_options(calendar, options)
        except DjangoValidationError as error:
            model_error(error)
        return Response(calendar_data(calendar, request.user), status=201)


class CalendarDetail(CalendarAPI):
    def get(self, request, pk):
        return Response(calendar_data(calendar_for(request, pk), request.user))

    def patch(self, request, pk):
        calendar_for(request, pk, "manage")
        with transaction.atomic():
            calendar = get_object_or_404(Calendar.objects.select_for_update(), pk=pk)
            next_type = request.data.get("calendar_type", calendar.calendar_type)
            payload, options = external_options(request.data, next_type)
            if next_type == "external" and calendar.calendar_type != "external" and not options.get("url"):
                raise ValidationError({"url": "A subscription URL is required."})
            write = CalendarWrite(calendar, data=payload, partial=True)
            write.is_valid(raise_exception=True)
            try:
                write.save()
                save_external_options(calendar, options)
                if not calendar.is_active and calendar.calendar_type == "external":
                    ExternalCalendarSource.objects.filter(calendar=calendar).update(
                        status="pending", sync_requested=False)
            except DjangoValidationError as error:
                model_error(error)
        return Response(calendar_data(calendar, request.user))

    def delete(self, request, pk):
        calendar_for(request, pk, "manage")
        with transaction.atomic():
            # Match the calendar-first lock order used by external synchronization.
            calendar = get_object_or_404(Calendar.objects.select_for_update(), pk=pk)
            ExternalCalendarSource.objects.select_for_update().filter(calendar=calendar).first()
            # EventType is CASCADE from Calendar, while Event references its type
            # with PROTECT. Remove native events first so that protection remains
            # effective for direct EventType deletion.
            calendar.events.all().delete()
            calendar.delete()
        return Response(status=204)


class ExternalCalendarSync(CalendarAPI):
    """Queue a source refresh for an authorized calendar manager."""

    def post(self, request, pk):
        calendar = calendar_for(request, pk, "manage")
        if calendar.calendar_type != "external":
            raise ValidationError({"calendar_type": "Only external calendars can be synchronized."})
        with transaction.atomic():
            calendar = get_object_or_404(Calendar.objects.select_for_update(), pk=calendar.pk)
            if not calendar.is_active:
                raise ValidationError({"is_active": "An inactive calendar cannot be synchronized."})
            source = get_object_or_404(ExternalCalendarSource.objects.select_for_update(), calendar=calendar)
            if not source.enabled:
                raise ValidationError({"enabled": "The subscription is disabled."})
            if source.status == "running" or source.sync_requested:
                return Response({"status": source.status if source.status == "running" else "queued"}, status=202)
            source.sync_requested = True
            source.status = "queued"
            source.save(update_fields=("sync_requested", "status"))
            from django_q.tasks import async_task
            transaction.on_commit(lambda: async_task("plugins.Calendars.external.sync_calendar", source.pk))
        return Response({"status": "queued"}, status=202)


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
        calendar = calendar_for(request, pk, management_read=True)
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
        if calendar.calendar_type == "external":
            source = ExternalCalendarSource.objects.filter(calendar=calendar).first()
            if source is None or not source.enabled:
                return Response({"results": []})
            try:
                rows = occurrences(source, start, end)
            except ExternalFeedError as exc:
                raise ValidationError({"period": str(exc)}) from exc
            return Response({"results": [{
                "id": identity, "calendar": calendar.pk, "event_type": None,
                "event_type_name": "", "title": item.title, "description": item.description,
                "start": begins.date().isoformat() if item.all_day else begins.isoformat(),
                "end": finishes.date().isoformat() if item.all_day else finishes.isoformat(),
                "all_day": item.all_day, "color": calendar.color, "created_by": None,
                "employees": [], "location": item.location, "read_only": True,
            } for identity, item, begins, finishes in rows]})
        try:
            rows = native_occurrences(calendar, start, end)
        except RecurrenceError as exc:
            raise ValidationError({"period": str(exc)}) from exc
        visible_ids = set(visible_employee_queryset(request.user).values_list("pk", flat=True)) if calendar.calendar_type == "assignments" else set()
        for item, _, _, _, _ in rows:
            if calendar.calendar_type == "assignments":
                item.visible_assignees = [employee for employee in item.employees.all() if employee.pk in visible_ids]
        return Response({"results": [occurrence_data(row, request.user, visible_ids) for row in rows]})

    def post(self, request, pk):
        calendar = calendar_for(request, pk, "edit")
        if calendar.calendar_type == "external":
            raise PermissionDenied("External events are read-only.")
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
        if calendar.calendar_type == "external":
            raise PermissionDenied("External events are read-only.")
        with transaction.atomic():
            calendar = Calendar.objects.select_for_update().get(pk=pk)
            item = get_object_or_404(Event.objects.select_for_update(),
                                     calendar=calendar, pk=event_id)
            write = EventWrite(item, data=request.data, partial=True, context={"calendar": calendar, "user": request.user})
            write.is_valid(raise_exception=True)
            structural = any(getattr(item, field) != value for field, value in write.validated_data.items()
                             if field in {"start", "all_day", "recurrence_rule", "recurrence_timezone", "rdates", "exdates"})
            if structural and item.exceptions.exists():
                revision = item.exceptions_revision
                if request.data.get("reset_exceptions") is not True:
                    return Response({"code": "exceptions_reset_required", "exceptions_revision": revision,
                                     "detail": "Confirm the removal of occurrence exceptions."}, status=409)
                if type(request.data.get("exceptions_revision")) is not int or request.data["exceptions_revision"] != revision:
                    return Response({"code": "exceptions_revision_changed", "exceptions_revision": revision,
                                     "detail": "The exceptions changed; confirm again."}, status=409)
            try:
                write.save()
                if hasattr(write, "assignment_ids"):
                    item.employees.set(write.assignment_ids)
                if structural:
                    item.exceptions.all().delete()
                    Event.objects.filter(pk=item.pk).update(exceptions_revision=F("exceptions_revision") + 1)
                    item.refresh_from_db(fields=("exceptions_revision",))
            except DjangoValidationError as error:
                model_error(error)
        return Response(event_data(item, request.user))

    def delete(self, request, pk, event_id):
        calendar = calendar_for(request, pk, "edit")
        if calendar.calendar_type == "external":
            raise PermissionDenied("External events are read-only.")
        get_object_or_404(Event, calendar=calendar, pk=event_id).delete()
        return Response(status=204)


class EventOccurrenceDetail(CalendarAPI):
    """Edit or cancel one original recurrence identity, never the series."""

    def _locked(self, request, pk, event_id, raw_id):
        calendar = calendar_for(request, pk, "edit")
        if calendar.calendar_type == "external":
            raise PermissionDenied("External events are read-only.")
        calendar = Calendar.objects.select_for_update().get(pk=pk)
        event = get_object_or_404(Event.objects.select_for_update(), calendar=calendar, pk=event_id)
        try:
            recurrence_id = datetime.fromisoformat(raw_id.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValidationError({"recurrence_id": "Use an ISO datetime with timezone."}) from exc
        if timezone.is_naive(recurrence_id) or not occurrence_exists(event, recurrence_id):
            raise ValidationError({"recurrence_id": "This occurrence does not belong to the series."})
        return calendar, event, recurrence_id

    @staticmethod
    def _bump(event):
        Event.objects.filter(pk=event.pk).update(exceptions_revision=F("exceptions_revision") + 1,
                                                updated_at=timezone.now())

    def patch(self, request, pk, event_id, recurrence_id):
        allowed = {"title", "description", "location", "event_type", "start", "end", "all_day", "employees", "inherit"}
        if not request.data or set(request.data) == {"inherit"} and not request.data["inherit"]:
            raise ValidationError({"detail": "Supply at least one occurrence change."})
        if set(request.data) - allowed:
            raise ValidationError({"detail": "Unknown occurrence field."})
        inherited = request.data.get("inherit", [])
        if (not isinstance(inherited, list) or any(not isinstance(field, str) for field in inherited)
                or len(inherited) != len(set(inherited)) or set(inherited) - (allowed - {"inherit"})):
            raise ValidationError({"inherit": "Unknown or duplicate inherited field."})
        if set(inherited) & set(request.data):
            raise ValidationError({"inherit": "A field cannot be overridden and inherited together."})
        slot = {"start", "end", "all_day"}
        if slot & (set(inherited) | set(request.data)):
            if set(inherited) & slot and not slot <= set(inherited):
                raise ValidationError({"inherit": "Inherit the complete time slot."})
            if set(request.data) & slot and not {"start", "end"} <= set(request.data):
                raise ValidationError({"start": "Override the complete time slot."})
        with transaction.atomic():
            calendar, event, instant = self._locked(request, pk, event_id, recurrence_id)
            exception, _ = EventOccurrenceException.objects.select_for_update().get_or_create(
                event=event, recurrence_id=instant)
            for field in ("title", "description", "location", "event_type"):
                if field in inherited:
                    setattr(exception, f"{field}_override", False)
                    if field == "event_type":
                        exception.event_type = None
                elif field in request.data:
                    value = request.data[field]
                    if field == "event_type":
                        if value is not None:
                            if type(value) is not int:
                                raise ValidationError({field: "Expected a type id or null."})
                            value = get_object_or_404(EventType, pk=value, calendar=calendar)
                            if not value.is_active and value.pk != event.event_type_id:
                                raise ValidationError({field: "This event type is inactive."})
                        exception.event_type = value
                    else:
                        if not isinstance(value, str):
                            raise ValidationError({field: "Expected text."})
                        setattr(exception, field, value)
                    setattr(exception, f"{field}_override", True)
            if slot <= set(inherited):
                exception.timeslot_override = False
                exception.start = exception.end = exception.all_day = None
            elif {"start", "end"} <= set(request.data):
                all_day = request.data.get("all_day", exception.all_day if exception.timeslot_override else event.all_day)
                if type(all_day) is not bool:
                    raise ValidationError({"all_day": "Expected a boolean."})
                parsed = {}
                for field in ("start", "end"):
                    raw = request.data[field]
                    if all_day:
                        try:
                            if not isinstance(raw, str) or len(raw) != 10:
                                raise ValueError
                            from zoneinfo import ZoneInfo
                            parsed[field] = datetime.combine(date.fromisoformat(raw), time.min,
                                                              tzinfo=ZoneInfo(event.recurrence_timezone))
                        except ValueError as exc:
                            raise ValidationError({field: "Use YYYY-MM-DD for an all-day event."}) from exc
                    else:
                        parsed[field] = serializers.DateTimeField().run_validation(raw)
                exception.start, exception.end, exception.all_day = parsed["start"], parsed["end"], all_day
                exception.timeslot_override = True
            if "employees" in inherited:
                exception.employees_override = False
                employee_ids = []
            elif "employees" in request.data:
                if calendar.calendar_type != "assignments":
                    raise ValidationError({"employees": "This calendar does not allow assignments."})
                requested = request.data["employees"]
                if (not isinstance(requested, list) or any(type(value) is not int or value <= 0 for value in requested)
                        or len(requested) != len(set(requested))):
                    raise ValidationError({"employees": "Expected distinct employee ids."})
                current_ids = {employee.pk for employee in effective_employees(event, exception)}
                visible_existing = set(visible_employee_queryset(request.user).filter(pk__in=current_ids).values_list("pk", flat=True))
                added = set(requested) - current_ids
                if eligible_employees(calendar, request.user).filter(pk__in=added).count() != len(added):
                    raise ValidationError({"employees": "Unknown, inaccessible or ineligible employee."})
                employee_ids = set(requested) | (current_ids - visible_existing)
                exception.employees_override = True
            else:
                employee_ids = None
            try:
                exception.save()
                if employee_ids is not None:
                    exception.employees.set(employee_ids)
            except DjangoValidationError as error:
                model_error(error)
            self._bump(event)
        return Response({"series_id": event.pk, "recurrence_id": instant.isoformat(),
                         "is_exception": True, "cancelled": exception.cancelled})

    def delete(self, request, pk, event_id, recurrence_id):
        with transaction.atomic():
            _, event, instant = self._locked(request, pk, event_id, recurrence_id)
            exception, _ = EventOccurrenceException.objects.select_for_update().get_or_create(
                event=event, recurrence_id=instant)
            exception.cancelled = True
            exception.save()
            self._bump(event)
        return Response({"series_id": event.pk, "recurrence_id": instant.isoformat(), "cancelled": True})


class EventOccurrenceRestore(EventOccurrenceDetail):
    def post(self, request, pk, event_id, recurrence_id):
        """Fully restore inheritance by removing the exception, as the requested API contract specifies."""
        with transaction.atomic():
            _, event, instant = self._locked(request, pk, event_id, recurrence_id)
            # The user's specification explicitly says: "Une restauration complète
            # supprime l'exception et rétablit le comportement hérité de la série."
            exception = get_object_or_404(EventOccurrenceException, event=event, recurrence_id=instant)
            exception.delete()
            self._bump(event)
        return Response({"series_id": event.pk, "recurrence_id": instant.isoformat(), "is_exception": False})
