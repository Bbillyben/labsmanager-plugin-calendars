from django.apps import AppConfig


class CalendarsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "plugins.Calendars"
    label = "plugin_calendars"
    verbose_name = "Calendars plugin"

    def ready(self):
        from django.core.exceptions import ValidationError
        from django.db.models.signals import m2m_changed
        from .models import Event

        def prevent_simple_event_assignments(sender, instance, action, reverse, pk_set, **kwargs):
            if action != "pre_add" or not pk_set:
                return
            if reverse:
                if Event.objects.filter(pk__in=pk_set, calendar__calendar_type="events").exists():
                    raise ValidationError("Simple calendar events cannot have employee assignments.")
            elif instance.calendar.calendar_type == "events":
                raise ValidationError("Simple calendar events cannot have employee assignments.")

        m2m_changed.connect(prevent_simple_event_assignments, sender=Event.employees.through,
                            dispatch_uid="plugin_calendars_prevent_simple_event_assignments")
