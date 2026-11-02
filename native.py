"""Effective native occurrences, with explicit exceptions and bounded expansion."""

from datetime import timedelta
from zoneinfo import ZoneInfo

from django.db.models import Prefetch, Q

from .models import Event, EventOccurrenceException
from .recurrence import MAX_OCCURRENCES, RecurrenceError, occurrence_starts


def native_occurrences(calendar, start, end):
    """Load relations in batches and include exceptions moved into the window."""
    exceptions = EventOccurrenceException.objects.select_related("event_type").prefetch_related("employees")
    events = (Event.objects.filter(calendar=calendar)
              .filter(Q(recurrence_rule__gt="") | Q(start__lt=end, end__gt=start))
              .select_related("event_type", "calendar")
              .prefetch_related("employees", Prefetch("exceptions", queryset=exceptions, to_attr="loaded_exceptions")))
    results = []
    for event in events:
        if not event.recurrence_rule:
            results.append((event, None, event.start, event.end, None))
            continue
        zone = ZoneInfo(event.recurrence_timezone)
        # Wall-clock duration preserves local meeting times across DST changes.
        local_duration = (event.end.astimezone(zone).replace(tzinfo=None)
                          - event.start.astimezone(zone).replace(tzinfo=None))
        if local_duration <= timedelta(0):
            local_duration = event.end - event.start
        exceptions_by_id = {item.recurrence_id: item for item in event.loaded_exceptions}
        seen = set()
        lower = start - max(local_duration, event.end - event.start)
        for original in occurrence_starts(event.start, event.recurrence_rule, event.rdates,
                                          event.exdates, event.recurrence_timezone, lower, end):
            exception = exceptions_by_id.get(original)
            seen.add(original)
            local_end = (original.astimezone(zone).replace(tzinfo=None) + local_duration).replace(tzinfo=zone)
            actual_start = exception.start if exception and exception.timeslot_override else original
            actual_end = exception.end if exception and exception.timeslot_override else local_end
            if not (exception and exception.cancelled) and actual_start < end and actual_end > start:
                results.append((event, exception, actual_start, actual_end, original))
        for exception in event.loaded_exceptions:
            if exception.recurrence_id in seen or exception.cancelled or not exception.timeslot_override:
                continue
            if exception.start < end and exception.end > start:
                results.append((event, exception, exception.start, exception.end, exception.recurrence_id))
        if len(results) > MAX_OCCURRENCES:
            raise RecurrenceError("The requested period contains too many occurrences.")
    return sorted(results, key=lambda row: (row[2], row[0].pk, str(row[4])))


def effective_employees(event, exception):
    """An explicit empty list is different from inherited assignments."""
    if exception and exception.employees_override:
        return list(exception.employees.all())
    return list(event.employees.all())
