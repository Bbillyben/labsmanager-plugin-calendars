"""Bounded, read-only synchronization of remote iCalendar feeds."""

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone as utc
from hashlib import sha256
from http.client import HTTPSConnection
from ipaddress import ip_address
from socket import getaddrinfo, create_connection, SOCK_STREAM
from ssl import create_default_context
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Calendar, ExternalCalendarEvent, ExternalCalendarSource
from .recurrence import make_rules


MAX_BYTES = 5 * 1024 * 1024
MAX_COMPONENTS = 10000
MAX_OCCURRENCES = 5000


class ExternalFeedError(ValueError):
    """An intentionally redacted feed or network validation error."""


def validate_url(value):
    """Accept only public HTTPS hosts; no URL or query is copied to errors."""
    if not isinstance(value, str) or len(value) > 4096 or any(ord(ch) < 32 for ch in value):
        raise ExternalFeedError("Invalid subscription URL.")
    try:
        parsed = urlsplit(value)
        if (parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.fragment or parsed.port not in (None, 443)):
            raise ValueError
        host = parsed.hostname.encode("idna").decode("ascii")
        if host.endswith(".") or len(host) > 253:
            raise ValueError
        return parsed, host
    except (ValueError, UnicodeError) as exc:
        raise ExternalFeedError("A public HTTPS URL is required.") from exc


def _public_addresses(host):
    """Resolve once and return only routable addresses for a pinned connection."""
    try:
        infos = getaddrinfo(host, 443, type=SOCK_STREAM)
        addresses = {entry[4][0] for entry in infos}
        if not addresses or any(not ip_address(address).is_global for address in addresses):
            raise ExternalFeedError("The subscription host is unavailable.")
        return sorted(addresses)
    except (OSError, ValueError) as exc:
        raise ExternalFeedError("The subscription host is unavailable.") from exc


class _PinnedHTTPS(HTTPSConnection):
    """Use the validated address while checking TLS against the original host."""

    def __init__(self, host, address):
        super().__init__(host, port=443, timeout=10, context=create_default_context())
        self.address = address

    def connect(self):
        self.sock = create_connection((self.address, 443), self.timeout)
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


def download(url):
    """Fetch a complete bounded response, with redirects and proxies disabled."""
    parsed, host = validate_url(url)
    address = _public_addresses(host)[0]
    connection = _PinnedHTTPS(host, address)
    try:
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        connection.request("GET", path, headers={"Host": host, "Accept": "text/calendar",
                                                   "Accept-Encoding": "identity", "User-Agent": "LabsManager Calendars"})
        response = connection.getresponse()
        if response.status != 200 or response.getheader("Content-Encoding", "identity").lower() != "identity":
            raise ExternalFeedError("The subscription did not return a complete calendar.")
        length = response.getheader("Content-Length")
        if length and (not length.isdecimal() or int(length) > MAX_BYTES):
            raise ExternalFeedError("The calendar exceeds the download limit.")
        data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ExternalFeedError("The calendar exceeds the download limit.")
        return data
    except ExternalFeedError:
        raise
    except (OSError, TimeoutError) as exc:
        raise ExternalFeedError("The subscription host is unavailable.") from exc
    finally:
        connection.close()


def _datetime(value, tzid=""):
    """Normalize a DATE or DATE-TIME without losing the source's local zone."""
    if isinstance(value, datetime):
        if timezone.is_aware(value):
            return value
        try:
            zone = ZoneInfo(tzid or settings.TIME_ZONE)
        except ZoneInfoNotFoundError as exc:
            raise ExternalFeedError("The calendar uses an unknown timezone.") from exc
        return value.replace(tzinfo=zone)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=ZoneInfo(tzid or settings.TIME_ZONE))
    raise ExternalFeedError("The calendar contains an invalid date.")


def _date_values(component, name):
    properties = component.get(name)
    if properties is None:
        return ()
    if not isinstance(properties, list):
        properties = [properties]
    return tuple(value.dt for prop in properties for value in prop.dts)


def _component_record(component):
    """Validate and normalize one VEVENT before any database mutation."""
    uid = str(component.get("UID", "")).strip()
    if not uid or len(uid) > 1000 or "DTSTART" not in component:
        raise ExternalFeedError("The calendar contains an incomplete event.")
    start_property = component["DTSTART"]
    tzid = str(start_property.params.get("TZID", ""))
    start_value = component.decoded("DTSTART")
    start = _datetime(start_value, tzid)
    all_day = isinstance(start_value, date) and not isinstance(start_value, datetime)
    if "DTEND" in component:
        end = _datetime(component.decoded("DTEND"), tzid)
    elif "DURATION" in component:
        end = start + component.decoded("DURATION")
    else:
        end = start + (timedelta(days=1) if all_day else timedelta(hours=1))
    if end <= start:
        raise ExternalFeedError("The calendar contains an invalid event period.")
    recurrence_value = component.decoded("RECURRENCE-ID") if "RECURRENCE-ID" in component else None
    recurrence_key = recurrence_value.isoformat() if recurrence_value is not None else ""
    if len(recurrence_key) > 255:
        raise ExternalFeedError("The calendar contains an invalid recurrence identifier.")
    # dateutil validates the RFC recurrence grammar; expansion remains bounded at read time.
    if "RRULE" in component:
        from dateutil.rrule import rrulestr
        try:
            rrulestr(component["RRULE"].to_ical().decode("ascii"), dtstart=start)
        except (ValueError, TypeError) as exc:
            raise ExternalFeedError("The calendar contains an invalid recurrence rule.") from exc
    for key in ("RDATE", "EXDATE"):
        for value in _date_values(component, key):
            _datetime(value, tzid)
    relevant = ("UID", "RECURRENCE-ID", "SUMMARY", "DESCRIPTION", "LOCATION", "DTSTART",
                "DTEND", "DURATION", "RRULE", "RDATE", "EXDATE", "STATUS", "SEQUENCE", "LAST-MODIFIED")
    fingerprint = sha256(b"\n".join(
        key.encode() + b":" + value.to_ical()
        for key in relevant if key in component
        for value in (component[key] if isinstance(component[key], list) else [component[key]])
    )).hexdigest()
    modified = component.decoded("LAST-MODIFIED") if "LAST-MODIFIED" in component else None
    if modified is not None:
        modified = _datetime(modified)
    return {
        "uid": uid, "recurrence_key": recurrence_key, "title": str(component.get("SUMMARY", ""))[:200],
        "description": str(component.get("DESCRIPTION", "")), "location": str(component.get("LOCATION", ""))[:500],
        "start": start, "end": end, "all_day": all_day, "tzid": tzid,
        "status": str(component.get("STATUS", ""))[:20],
        "sequence": int(component.get("SEQUENCE", 0)), "last_modified": modified,
        "raw_component": component.to_ical().decode("utf-8"), "fingerprint": fingerprint,
    }


def parse_feed(data):
    """Reject incomplete feeds and ambiguous identities before applying changes."""
    from icalendar import Calendar as ICalendar
    if not data.strip().startswith(b"BEGIN:VCALENDAR") or not data.strip().endswith(b"END:VCALENDAR"):
        raise ExternalFeedError("The subscription is not a complete iCalendar feed.")
    try:
        calendar = ICalendar.from_ical(data)
        if calendar.name != "VCALENDAR" or str(calendar.get("VERSION", "")) != "2.0":
            raise ExternalFeedError("The subscription is not a valid iCalendar feed.")
        components = list(calendar.walk("VEVENT"))
        if len(components) > MAX_COMPONENTS:
            raise ExternalFeedError("The calendar contains too many events.")
        records = {}
        for component in components:
            record = _component_record(component)
            key = (record["uid"], record["recurrence_key"])
            if key in records:
                raise ExternalFeedError("The calendar contains duplicate event identities.")
            records[key] = record
        timezones = [item.to_ical().decode("utf-8") for item in calendar.walk("VTIMEZONE")]
        return records, timezones
    except ExternalFeedError:
        raise
    except (ValueError, TypeError, UnicodeError, KeyError) as exc:
        raise ExternalFeedError("The subscription is not a valid iCalendar feed.") from exc


def _locked_source(source_id):
    """Lock calendar before source, matching state changes and deletion."""
    calendar_id = ExternalCalendarSource.objects.filter(pk=source_id).values_list("calendar_id", flat=True).first()
    if calendar_id is None or not Calendar.objects.select_for_update().filter(pk=calendar_id).exists():
        return None
    return ExternalCalendarSource.objects.select_for_update().select_related("calendar").filter(pk=source_id).first()


def sync_calendar(source_id):
    """Synchronize one source atomically; retain valid rows after any failure."""
    now = timezone.now()
    with transaction.atomic():
        source = _locked_source(source_id)
        if source is None or not source.enabled or not source.calendar.is_active or source.calendar.calendar_type != "external":
            return {"status": "disabled"}
        if source.status == "running" and source.last_attempt and now - source.last_attempt < timedelta(minutes=15):
            return {"status": "running"}
        source.status = "running"
        source.last_attempt = now
        source.sync_requested = False
        source.save(update_fields=("status", "last_attempt", "sync_requested"))
        url = source.url
    try:
        records, timezones = parse_feed(download(url))
        with transaction.atomic():
            source = _locked_source(source_id)
            if source is None or not source.calendar.is_active:
                return {"status": "disabled"}
            if source.url != url or not source.enabled:
                source.status = "pending"
                source.save(update_fields=("status",))
                return {"status": "changed"}
            existing = {(event.uid, event.recurrence_key): event for event in
                        ExternalCalendarEvent.objects.select_for_update().filter(source=source)}
            counts = {"created": 0, "updated": 0, "deactivated": 0}
            for key, values in records.items():
                item = existing.pop(key, None)
                if item is None:
                    ExternalCalendarEvent.objects.create(source=source, **values)
                    counts["created"] += 1
                elif item.fingerprint != values["fingerprint"] or not item.active:
                    for field, value in values.items():
                        setattr(item, field, value)
                    item.active = True
                    item.save(update_fields=(*values, "active"))
                    counts["updated"] += 1
            for item in existing.values():
                if item.active:
                    item.active = False
                    item.save(update_fields=("active",))
                    counts["deactivated"] += 1
            source.status = "success"
            source.last_success = timezone.now()
            source.last_error = ""
            source.statistics = counts
            source.timezone_components = timezones
            source.save(update_fields=("status", "last_success", "last_error", "statistics", "timezone_components"))
            return {"status": "success", **counts}
    except Exception as exc:
        message = str(exc) if isinstance(exc, ExternalFeedError) else "The calendar could not be synchronized."
        ExternalCalendarSource.objects.filter(pk=source_id, url=url, enabled=True, calendar__is_active=True).update(
            status="error", last_error=message[:200])
        return {"status": "error"}


def sync_due_calendars():
    """One ScheduleMixin task scans all enabled sources without sharing failures."""
    now = timezone.now()
    for source in ExternalCalendarSource.objects.filter(enabled=True, calendar__is_active=True).only(
            "pk", "last_attempt", "interval_minutes", "status", "sync_requested").iterator():
        due = not source.last_attempt or now - source.last_attempt >= timedelta(minutes=source.interval_minutes)
        if source.sync_requested or due:
            sync_calendar(source.pk)


def occurrences(source, start, end):
    """Expand locally stored series only within a requested, bounded window."""
    from icalendar import Event as IEvent

    grouped = defaultdict(list)
    for item in source.events.filter(active=True):
        grouped[item.uid].append(item)
    results = []
    for items in grouped.values():
        master = next((item for item in items if not item.recurrence_key), None)
        overrides = {item.recurrence_key: item for item in items if item.recurrence_key}
        if master is None:
            candidates = [(item, item.recurrence_key) for item in overrides.values()]
        elif master.status.upper() == "CANCELLED":
            candidates = []
        else:
            component = IEvent.from_ical(master.raw_component)
            first = _datetime(component.decoded("DTSTART"), master.tzid)
            recurring = any(name in component for name in ("RRULE", "RDATE", "EXDATE"))
            if not recurring:
                candidates = [(master, "")]
            else:
                rules = make_rules(
                    first, component["RRULE"].to_ical().decode("ascii") if "RRULE" in component else "",
                    (_datetime(value, master.tzid) for value in _date_values(component, "RDATE")),
                    (_datetime(value, master.tzid) for value in _date_values(component, "EXDATE")),
                )
                lower = start - (master.end - master.start)
                candidates = []
                for occurrence in rules.xafter(lower, count=MAX_OCCURRENCES + 1, inc=True):
                    if occurrence >= end:
                        break
                    if len(candidates) >= MAX_OCCURRENCES:
                        raise ExternalFeedError("The requested period contains too many occurrences.")
                    key = occurrence.date().isoformat() if master.all_day else occurrence.isoformat()
                    item = overrides.pop(key, master)
                    candidates.append((item, key, occurrence))
                candidates.extend((item, key, item.start) for key, item in overrides.items())
        for candidate in candidates:
            item, key = candidate[:2]
            original_start = candidate[2] if len(candidate) > 2 else item.start
            actual_start = item.start if item is not master else original_start
            actual_end = item.end if item is not master else original_start + (master.end - master.start)
            if item.status.upper() != "CANCELLED" and actual_start < end and actual_end > start:
                identity = f"{master.pk if master else item.pk}:{key}" if key else str(item.pk)
                results.append((identity, item, actual_start, actual_end))
                if len(results) > MAX_OCCURRENCES:
                    raise ExternalFeedError("The requested period contains too many occurrences.")
    return sorted(results, key=lambda row: (row[2], row[0]))
