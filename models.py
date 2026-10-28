"""Calendar data and object-level access owned exclusively by this plugin."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone


color_validator = RegexValidator(r"^#[0-9a-fA-F]{6}$", "Use a six-digit hexadecimal color.")


class Calendar(models.Model):
    ACCESS_MODES = (("inherited", "Inherited"), ("restricted", "Restricted"))
    CALENDAR_TYPES = (("events", "Events"), ("assignments", "Employee assignments"))
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    color = models.CharField(max_length=7, default="#3b82f6", validators=[color_validator])
    access_mode = models.CharField(max_length=10, choices=ACCESS_MODES, default="restricted")
    calendar_type = models.CharField(max_length=11, choices=CALENDAR_TYPES, default="events")
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
        if self.pk and Calendar.objects.filter(pk=self.pk).exclude(calendar_type=self.calendar_type).exists() and self.events.exists():
            raise ValidationError({"calendar_type": "The type cannot change after events have been created."})

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
    start = models.DateTimeField()
    end = models.DateTimeField()
    all_day = models.BooleanField(default=False)
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
            elif self.all_day and (timezone.localtime(self.start).time().isoformat() != "00:00:00"
                                   or timezone.localtime(self.end).time().isoformat() != "00:00:00"):
                errors["all_day"] = "All-day events use local midnight and an exclusive end date."
        if self.event_type_id and self.calendar_id and self.event_type.calendar_id != self.calendar_id:
            errors["event_type"] = "The event type belongs to another calendar."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.title
