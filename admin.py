from django.contrib import admin
from .models import Calendar, CalendarAccess, Event, EventType, ExternalCalendarSource, ExternalCalendarEvent



admin.site.register(Calendar)
admin.site.register(CalendarAccess)
admin.site.register(Event)
admin.site.register(EventType)


@admin.register(ExternalCalendarSource)
class ExternalCalendarSourceAdmin(admin.ModelAdmin):
    """Read-only diagnostics; the private URL is intentionally never rendered."""

    list_display = ("calendar", "enabled", "status", "last_attempt", "last_success", "has_url")
    list_filter = ("enabled", "status")
    search_fields = ("calendar__name",)
    readonly_fields = ("calendar", "enabled", "interval_minutes", "last_attempt",
                       "last_success", "status", "last_error", "statistics", "has_url")
    fields = readonly_fields

    @admin.display(boolean=True, description="URL configured")
    def has_url(self, obj):
        return bool(obj.url)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ExternalCalendarEvent)
class ExternalCalendarEventAdmin(admin.ModelAdmin):
    """Inspect synchronized components without permitting local divergence."""

    list_display = ("source", "uid", "recurrence_key", "title", "start", "active", "status")
    list_filter = ("active", "status", "source__calendar")
    search_fields = ("uid", "title", "source__calendar__name")
    readonly_fields = ("source", "uid", "recurrence_key", "title", "description",
                       "location", "start", "end", "all_day", "tzid", "status",
                       "sequence", "last_modified", "fingerprint", "active")
    fields = readonly_fields

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
