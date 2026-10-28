from django.contrib import admin
from .models import Calendar, CalendarAccess, Event, EventType



admin.site.register(Calendar)
admin.site.register(CalendarAccess)
admin.site.register(Event)
admin.site.register(EventType)