"""Bounded recurrence calculation shared by native and imported calendars."""

from datetime import datetime, timedelta, timezone as utc
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import rruleset, rrulestr
from dateutil.tz import datetime_exists


MAX_OCCURRENCES = 5000
ALLOWED_RULE_PARTS = {"FREQ", "INTERVAL", "BYDAY", "BYMONTHDAY", "BYSETPOS", "BYMONTH", "COUNT", "UNTIL", "WKST"}
ALLOWED_FREQUENCIES = {"DAILY", "WEEKLY", "MONTHLY", "YEARLY"}


class RecurrenceError(ValueError):
    """A recurrence is malformed or exceeds the bounded expansion limit."""


def make_rules(first, rule, rdates=(), exdates=()):
    """Build an RFC recurrence set from already normalized local date-times."""
    rules = rruleset()
    if rule:
        rules.rrule(rrulestr(rule, dtstart=first))
    else:
        rules.rdate(first)
    for value in rdates:
        rules.rdate(value)
    for value in exdates:
        rules.exdate(value)
    return rules


def validate_rule(rule, first, zone_name, rdates=(), exdates=()):
    """Validate the supported RRULE subset and local recurrence identifiers."""
    try:
        zone = ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, TypeError, ValueError) as exc:
        raise RecurrenceError("Use an IANA timezone for recurring events.") from exc
    if not rule or not isinstance(rule, str):
        raise RecurrenceError("A recurrence rule is required.")
    try:
        parts = dict(part.split("=", 1) for part in rule.split(";"))
        if len(parts) != len(rule.split(";")) or set(parts) - ALLOWED_RULE_PARTS:
            raise ValueError
        if parts.get("FREQ") not in ALLOWED_FREQUENCIES:
            raise ValueError
        if "COUNT" in parts and "UNTIL" in parts:
            raise ValueError
        if "INTERVAL" in parts and int(parts["INTERVAL"]) < 1:
            raise ValueError
        if "COUNT" in parts and int(parts["COUNT"]) < 1:
            raise ValueError
        if "BYSETPOS" in parts and not ("BYDAY" in parts or "BYMONTHDAY" in parts):
            raise ValueError
        local_first = first.astimezone(zone)
        if not datetime_exists(local_first):
            raise ValueError
        rule_object = rrulestr(rule, dtstart=local_first)
        if rule_object[0] != local_first:
            raise ValueError
        for collection in (rdates, exdates):
            if not isinstance(collection, list):
                raise ValueError
            for raw in collection:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if parsed.tzinfo is None or not datetime_exists(parsed.astimezone(zone)):
                    raise ValueError
        if "UNTIL" in parts:
            limit = datetime.strptime(parts["UNTIL"], "%Y%m%dT%H%M%SZ").replace(tzinfo=utc.utc)
            if limit < first:
                raise ValueError
    except (ValueError, TypeError, IndexError, KeyError, AttributeError) as exc:
        raise RecurrenceError("Invalid or inconsistent recurrence rule or dates.") from exc


def occurrence_starts(first, rule, rdates, exdates, zone_name, lower, upper, *, limit=MAX_OCCURRENCES):
    """Yield original starts in [lower, upper); never expand an infinite rule unbounded."""
    zone = ZoneInfo(zone_name)
    rules = make_rules(first.astimezone(zone), rule,
                       (datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(zone) for raw in rdates),
                       (datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(zone) for raw in exdates))
    count = 0
    for candidate in rules.xafter(lower.astimezone(zone), count=limit + 1, inc=True):
        if candidate >= upper:
            break
        # RFC 5545 discards nonexistent local times; ambiguous times use the first fold.
        if not datetime_exists(candidate):
            continue
        count += 1
        if count > limit:
            raise RecurrenceError("The requested period contains too many occurrences.")
        yield candidate.astimezone(utc.utc)


def occurrence_exists(event, recurrence_id):
    """Check one original start without expanding the whole series."""
    if not event.recurrence_rule or recurrence_id.tzinfo is None:
        return False
    instant = recurrence_id.astimezone(utc.utc)
    return any(occurrence_starts(event.start, event.recurrence_rule, event.rdates,
                                 event.exdates, event.recurrence_timezone,
                                 instant, instant + timedelta(microseconds=1), limit=1))
