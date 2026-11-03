"""Calendar data and object-level access owned exclusively by this plugin."""

from datetime import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone


color_validator = RegexValidator(r"^#[0-9a-fA-F]{6}$", "Use a six-digit hexadecimal color.")


class Calendar(models.Model):
    ACCESS_MODES = (("inherited", "Inherited"), ("restricted", "Restricted"))
    CALENDAR_TYPES = (("events", "Events"), ("assignments", "Employee assignments"), ("external", "External calendar"))
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    color = models.CharField(max_length=7, default="#3b82f6", validators=[color_validator])
    access_mode = models.CharField(max_length=10, choices=ACCESS_MODES, default="restricted")
    calendar_type = models.CharField(max_length=11, choices=CALENDAR_TYPES, default="events")
    is_active = models.BooleanField(default=True)
    employees = models.ManyToManyField("staff.Employee", blank=True, related_name="plugin_calendars")
    teams = models.ManyToManyField("staff.Team", blank=True, related_name="plugin_calendars")
    projects = models.ManyToManyField("project.Project", blank=True, related_name="plugin_calendars")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="owned_plugin_calendars")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name", "pk")

    def clean(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValidationError({"name": "A calendar name is required."})
        if self.pk:
            previous = Calendar.objects.filter(pk=self.pk).values_list("calendar_type", flat=True).first()
            if previous and previous != self.calendar_type:
                has_native_events = self.events.exists()
                has_external_source = previous == "external" and ExternalCalendarSource.objects.filter(calendar_id=self.pk).exists()
                if has_native_events or has_external_source:
                    raise ValidationError({"calendar_type": "The type cannot change after events have been created or a source configured."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class CalendarAccess(models.Model):
    VIEW = "view"
    EDIT = "edit"
    MANAGE = "manage"
    LEVELS = ((VIEW, "View"), (EDIT, "Edit"), (MANAGE, "Manage"))

    calendar = models.ForeignKey(Calendar, on_delete=models.CASCADE, related_name="accesses")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="plugin_calendar_accesses")
    level = models.CharField(max_length=6, choices=LEVELS)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("calendar", "user"), name="plugin_calendars_unique_access")]

    def clean(self):
        if self.calendar_id and self.user_id and self.calendar.created_by_id == self.user_id:
            raise ValidationError({"user": "The calendar owner always has manage access."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class EventType(models.Model):
    calendar = models.ForeignKey(Calendar, on_delete=models.CASCADE, related_name="event_types")
    name = models.CharField(max_length=100)
    color = models.CharField(max_length=7, blank=True, validators=[color_validator])
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("name", "pk")
        constraints = [models.UniqueConstraint(fields=("calendar", "name"), name="plugin_calendars_unique_type_name")]

    def clean(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValidationError({"name": "An event type name is required."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Event(models.Model):
    calendar = models.ForeignKey(Calendar, on_delete=models.CASCADE, related_name="events")
    event_type = models.ForeignKey(EventType, on_delete=models.PROTECT, related_name="events", null=True, blank=True)
    employees = models.ManyToManyField("staff.Employee", blank=True, related_name="plugin_calendar_events")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    location = models.CharField(max_length=500, blank=True)
    start = models.DateTimeField()
    end = models.DateTimeField()
    all_day = models.BooleanField(default=False)
    recurrence_rule = models.TextField(blank=True)
    recurrence_timezone = models.CharField(max_length=100, blank=True)
    rdates = models.JSONField(default=list, blank=True)
    exdates = models.JSONField(default=list, blank=True)
    exceptions_revision = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_plugin_calendar_events")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("start", "pk")
        indexes = [models.Index(fields=("calendar", "start", "end"), name="plugin_calendars_range_idx")]

    def clean(self):
        errors = {}
        self.title = self.title.strip()
        if not self.title:
            errors["title"] = "An event title is required."
        if self.start and self.end:
            if timezone.is_naive(self.start) or timezone.is_naive(self.end):
                errors["start"] = "Dates must include a timezone."
            elif self.end <= self.start:
                errors["end"] = "The end must be after the start."
            elif self.all_day:
                try:
                    zone = ZoneInfo(self.recurrence_timezone) if self.recurrence_rule and self.recurrence_timezone else timezone.get_current_timezone()
                except ZoneInfoNotFoundError:
                    zone = timezone.get_current_timezone()
                if self.start.astimezone(zone).time() != time.min or self.end.astimezone(zone).time() != time.min:
                    errors["all_day"] = "All-day events use local midnight and an exclusive end date."
        if self.event_type_id and self.calendar_id and self.event_type.calendar_id != self.calendar_id:
            errors["event_type"] = "The event type belongs to another calendar."
        if self.recurrence_rule:
            from .recurrence import RecurrenceError, validate_rule
            try:
                validate_rule(self.recurrence_rule, self.start, self.recurrence_timezone,
                              self.rdates, self.exdates)
            except RecurrenceError as exc:
                errors["recurrence_rule"] = str(exc)
        elif self.recurrence_timezone or self.rdates or self.exdates:
            errors["recurrence_rule"] = "A recurrence rule is required for recurrence options."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.title


class EventOccurrenceException(models.Model):
    """Only explicit overrides are stored; all other values inherit the series."""

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="exceptions")
    recurrence_id = models.DateTimeField()
    cancelled = models.BooleanField(default=False)
    title_override = models.BooleanField(default=False)
    title = models.CharField(max_length=200, blank=True)
    description_override = models.BooleanField(default=False)
    description = models.TextField(blank=True)
    location_override = models.BooleanField(default=False)
    location = models.CharField(max_length=500, blank=True)
    event_type_override = models.BooleanField(default=False)
    event_type = models.ForeignKey(EventType, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="occurrence_exceptions")
    timeslot_override = models.BooleanField(default=False)
    start = models.DateTimeField(null=True, blank=True)
    end = models.DateTimeField(null=True, blank=True)
    all_day = models.BooleanField(null=True, blank=True)
    employees_override = models.BooleanField(default=False)
    employees = models.ManyToManyField("staff.Employee", blank=True,
                                       related_name="plugin_calendar_occurrence_exceptions")

    class Meta:
        constraints = [models.UniqueConstraint(fields=("event", "recurrence_id"),
                                                name="plugin_calendars_unique_occurrence_exception")]

    def clean(self):
        errors = {}
        if not self.event_id or not self.event.recurrence_rule:
            errors["event"] = "Exceptions require a recurring series."
        if not self.recurrence_id or timezone.is_naive(self.recurrence_id):
            errors["recurrence_id"] = "An aware original start is required."
        elif self.event_id and self.event.recurrence_rule:
            from .recurrence import occurrence_exists
            if not occurrence_exists(self.event, self.recurrence_id):
                errors["recurrence_id"] = "The original start does not belong to the series."
        if self.timeslot_override:
            if self.start is None or self.end is None or self.all_day is None:
                errors["start"] = "Override the complete time slot."
            elif timezone.is_naive(self.start) or timezone.is_naive(self.end) or self.end <= self.start:
                errors["end"] = "The end must follow the start."
            elif self.all_day:
                zone = ZoneInfo(self.event.recurrence_timezone)
                if self.start.astimezone(zone).time() != time.min or self.end.astimezone(zone).time() != time.min:
                    errors["all_day"] = "All-day occurrences use local midnight."
        elif self.start is not None or self.end is not None or self.all_day is not None:
            errors["timeslot_override"] = "A time slot requires an explicit override."
        if self.title_override and not self.title.strip():
            errors["title"] = "An event title is required."
        if self.event_type_id and self.event_id and self.event_type.calendar_id != self.event.calendar_id:
            errors["event_type"] = "The event type belongs to another calendar."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class ExternalCalendarSource(models.Model):
    """Private connection and synchronization state for one external calendar."""

    calendar = models.OneToOneField(Calendar, on_delete=models.CASCADE, related_name="external_source")
    url = models.TextField()
    enabled = models.BooleanField(default=True)
    interval_minutes = models.PositiveIntegerField(default=60)
    last_attempt = models.DateTimeField(null=True, blank=True)
    last_success = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, default="pending")
    last_error = models.CharField(max_length=200, blank=True)
    statistics = models.JSONField(default=dict, blank=True)
    timezone_components = models.JSONField(default=list, blank=True)
    sync_requested = models.BooleanField(default=False)

    def clean(self):
        if self.calendar_id and self.calendar.calendar_type != "external":
            raise ValidationError({"calendar": "Only external calendars can have a source."})
        if not 15 <= self.interval_minutes <= 10080:
            raise ValidationError({"interval_minutes": "Choose 15 minutes to seven days."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.calendar.name


class ExternalCalendarEvent(models.Model):
    """One remote VEVENT component, including recurrence masters and overrides."""

    source = models.ForeignKey(ExternalCalendarSource, on_delete=models.CASCADE, related_name="events")
    uid = models.TextField()
    recurrence_key = models.CharField(max_length=255, blank=True)
    title = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    location = models.CharField(max_length=500, blank=True)
    start = models.DateTimeField()
    end = models.DateTimeField()
    all_day = models.BooleanField(default=False)
    tzid = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, blank=True)
    sequence = models.PositiveIntegerField(default=0)
    last_modified = models.DateTimeField(null=True, blank=True)
    raw_component = models.TextField()
    fingerprint = models.CharField(max_length=64)
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("source", "uid", "recurrence_key"),
                                                name="plugin_calendars_external_identity")]
        indexes = [models.Index(fields=("source", "active", "start"), name="plugin_cal_ext_range_idx")]

    def __str__(self):
        return self.title or self.uid
