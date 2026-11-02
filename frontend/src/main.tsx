import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  ConfirmDialog,
  EntityActionMenu,
  EntitySearch,
  employeeFilterSources,
  PluginCalendarView,
  PluginPublishedCalendarActions,
  projectFilterSources,
  teamFilterSources,
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  registerPluginTranslations,
  usePluginTranslation,
  type EntitySearchSource,
  type PluginCalendarEvent,
} from "@labsmanager/plugin-ui";
import "./style.css";
import {
  createCalendar,
  createEvent,
  createType,
  cancelOccurrence,
  deleteEvent,
  deleteType,
  listAttachments,
  setAttachments as saveAttachments,
  listCalendars,
  listEvents,
  getEvent,
  listResources,
  listMembers,
  listTypes,
  removeMember,
  restoreOccurrence,
  resolveEligibleEmployee,
  resolveUser,
  searchEligibleEmployees,
  searchUsers,
  setMember,
  syncCalendar,
  updateCalendar,
  updateEvent,
  updateOccurrence,
  updateType,
  type Attachment,
  type Attachments,
  type Calendar,
  type CalendarContext,
  type CalendarWrite,
  type Event,
  type EventType,
  type EventWrite,
  type OccurrenceWrite,
  type Member,
  type Role,
} from "./api";
import { RecurrenceFields } from "./RecurrenceFields";
import {
  browserTimezone,
  inheritedOccurrenceSlot,
  localInput,
  recurrenceFromRule,
  recurrenceRuleEditable,
  recurrenceWrite,
  zonedInput,
} from "./recurrence";
import type { RecurrenceFormValue } from "./recurrence";
import fr from "./locales/fr.json";
import en from "./locales/en.json";

registerPluginTranslations("calendars", { fr, en });

type Range = { start: string; end: string };
type EventDraft = { event: Event | null; range: Range; allDay: boolean };
type Locale = { [K in keyof typeof fr]: string };

function presentationEvent(
  item: Event,
  calendarName: string,
  id: string,
): PluginCalendarEvent {
  return {
    id,
    title: item.title,
    start: item.start,
    end: item.end,
    allDay: item.all_day,
    color: item.color,
    calendarName,
    typeName: item.event_type_name,
    employeeNames: item.employees.map((employee) => employee.label),
    resourceIds: item.employees.map((employee) => `employee:${employee.id}`),
  };
}

function draftRange(start: string, end: string, allDay: boolean): Range {
  if (start !== end) return { start, end };
  return {
    start,
    end: allDay
      ? nextDay(start)
      : new Date(new Date(start).getTime() + 60 * 60_000).toISOString(),
  };
}

function useLabels(): Locale {
  const { t } = usePluginTranslation("calendars");
  return useMemo(
    () =>
      Object.fromEntries(Object.keys(fr).map((key) => [key, t(key)])) as Locale,
    [t],
  );
}

function errorText(error: unknown, fallback: string) {
  if (error && typeof error === "object" && "payload" in error) {
    const payload = (error as { payload: unknown }).payload;
    if (payload && typeof payload === "object") {
      const values = Object.values(payload).flatMap((value) =>
        Array.isArray(value) ? value : [value],
      );
      const message = values.find(
        (value): value is string => typeof value === "string",
      );
      if (message) return message;
    }
  }
  return fallback;
}

function dateInput(value: string, allDay: boolean) {
  if (allDay) return value.slice(0, 10);
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000)
    .toISOString()
    .slice(0, 16);
}

function eventInput(value: string, allDay: boolean) {
  return allDay ? value : new Date(value).toISOString();
}

function nextDay(value: string) {
  const [year, month, day] = value.slice(0, 10).split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day + 1))
    .toISOString()
    .slice(0, 10);
}

function CalendarForm({
  calendar,
  onSaved,
  onClose,
  labels,
}: {
  calendar: Calendar | null;
  onSaved: (calendar: Calendar) => void;
  onClose: () => void;
  labels: Locale;
}) {
  const [name, setName] = useState(calendar?.name ?? "");
  const [description, setDescription] = useState(calendar?.description ?? "");
  const [color, setColor] = useState(calendar?.color ?? "#3b82f6");
  const [accessMode, setAccessMode] = useState<Calendar["access_mode"]>(
    calendar?.access_mode ?? "restricted",
  );
  const [calendarType, setCalendarType] = useState<Calendar["calendar_type"]>(
    calendar?.calendar_type ?? "events",
  );
  const [url, setUrl] = useState("");
  const [intervalMinutes, setIntervalMinutes] = useState(
    calendar?.external?.interval_minutes ?? 60,
  );
  const [enabled, setEnabled] = useState(calendar?.external?.enabled ?? true);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setPending(true);
    setError("");
    try {
      const data: CalendarWrite = {
        name,
        description,
        color,
        access_mode: accessMode,
        calendar_type: calendarType,
        ...(calendarType === "external"
          ? {
              interval_minutes: intervalMinutes,
              enabled,
              ...(url ? { url } : {}),
            }
          : {}),
      };
      const saved = calendar
        ? await updateCalendar(calendar.id, data)
        : await createCalendar(data);
      onSaved(saved);
    } catch (reason) {
      setError(errorText(reason, labels.saveError));
    } finally {
      setPending(false);
    }
  };
  return (
    <form className="calendars-form" onSubmit={(event) => void submit(event)}>
      <label>
        {labels.name}
        <input
          required
          maxLength={150}
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      </label>
      <label>
        {labels.description}
        <textarea
          value={description}
          onChange={(event) => setDescription(event.target.value)}
        />
      </label>
      <label>
        {labels.color}
        <input
          type="color"
          value={color}
          onChange={(event) => setColor(event.target.value)}
        />
      </label>
      <label>
        {labels.calendarType}
        <select
          value={calendarType}
          disabled={Boolean(
            calendar?.has_events || calendar?.calendar_type === "external",
          )}
          onChange={(event) =>
            setCalendarType(event.target.value as Calendar["calendar_type"])
          }
        >
          <option value="events">{labels.eventsCalendar}</option>
          <option value="assignments">{labels.assignmentsCalendar}</option>
          <option value="external">{labels.externalCalendar}</option>
        </select>
      </label>
      {(calendar?.has_events || calendar?.calendar_type === "external") && (
        <p className="calendars-help">
          {calendar.calendar_type === "external"
            ? labels.externalTypeLocked
            : labels.typeLocked}
        </p>
      )}
      {calendarType === "external" && (
        <>
          <label>
            {labels.externalUrl}
            <input
              type="url"
              required={!calendar?.external?.has_url}
              value={url}
              placeholder={
                calendar?.external?.has_url
                  ? labels.externalUrlConfigured
                  : undefined
              }
              autoComplete="off"
              onChange={(event) => setUrl(event.target.value)}
            />
          </label>
          <p className="calendars-help">
            {calendar?.external?.has_url
              ? labels.externalUrlReplace
              : labels.externalUrlHelp}
          </p>
          <label>
            {labels.syncInterval}
            <input
              type="number"
              min={15}
              max={10080}
              required
              value={intervalMinutes}
              onChange={(event) =>
                setIntervalMinutes(Number(event.target.value))
              }
            />
          </label>
          <label className="calendars-check">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(event) => setEnabled(event.target.checked)}
            />
            {labels.externalEnabled}
          </label>
        </>
      )}
      <label>
        {labels.accessMode}
        <select
          value={accessMode}
          onChange={(event) =>
            setAccessMode(event.target.value as Calendar["access_mode"])
          }
        >
          <option value="restricted">{labels.restricted}</option>
          <option value="inherited">{labels.inherited}</option>
        </select>
      </label>
      <p className="calendars-help">
        {accessMode === "inherited"
          ? labels.inheritedHelp
          : labels.restrictedHelp}
      </p>
      {error && <Alert tone="danger">{error}</Alert>}
      <div className="calendars-actions">
        <Button disabled={pending} onClick={onClose}>
          {labels.cancel}
        </Button>
        <Button variant="primary" disabled={pending} type="submit">
          {labels.save}
        </Button>
      </div>
    </form>
  );
}

function EventForm({
  calendar,
  draft,
  types,
  labels,
  onSaved,
  onClose,
}: {
  calendar: Calendar;
  draft: EventDraft;
  types: EventType[];
  labels: Locale;
  onSaved: () => void;
  onClose: () => void;
}) {
  const [title, setTitle] = useState(draft.event?.title ?? "");
  const [description, setDescription] = useState(
    draft.event?.description ?? "",
  );
  const [location, setLocation] = useState(draft.event?.location ?? "");
  const [allDay, setAllDay] = useState(draft.allDay);
  const [start, setStart] = useState(
    dateInput(draft.range.start, draft.allDay),
  );
  const [end, setEnd] = useState(dateInput(draft.range.end, draft.allDay));
  const [eventType, setEventType] = useState(
    String(draft.event?.event_type ?? ""),
  );
  const [assigned, setAssigned] = useState<Attachment[]>(
    draft.event?.employees ?? [],
  );
  const recurring = Boolean(draft.event?.series_id);
  const [scope, setScope] = useState<"occurrence" | "series">(
    recurring ? "occurrence" : "series",
  );
  const [series, setSeries] = useState<Event | null>(null);
  const advancedRule = Boolean(
    series?.recurrence_rule &&
      !recurrenceRuleEditable(
        series.recurrence_rule,
        series.recurrence_timezone ?? "",
        series.start,
      ),
  );
  const [seriesLoading, setSeriesLoading] = useState(recurring);
  const [recurrence, setRecurrence] = useState<RecurrenceFormValue>(() =>
    recurrenceFromRule("", browserTimezone(), draft.range.start),
  );
  const [recurrenceDirty, setRecurrenceDirty] = useState(false);
  const [inherit, setInherit] = useState<Set<string>>(new Set());
  const [employeesExplicit, setEmployeesExplicit] = useState(false);
  const [resetRequest, setResetRequest] = useState<{
    write: Partial<EventWrite>;
    revision: number;
  } | null>(null);
  const [confirmAction, setConfirmAction] = useState<
    "point" | "series" | "occurrence" | "restore" | null
  >(null);
  const [employeeSelection, setEmployeeSelection] = useState("");
  const employeeSource = useMemo<EntitySearchSource>(
    () => ({
      async search(query, signal) {
        const response = await searchEligibleEmployees(
          calendar.id,
          query,
          signal,
        );
        return {
          options: response.results.map((item) => ({
            value: String(item.id),
            label: item.label,
          })),
          hasMore: response.has_more,
        };
      },
      async resolve(id, signal) {
        const response = await resolveEligibleEmployee(calendar.id, id, signal);
        const item = response.results[0];
        return item ? { value: String(item.id), label: item.label } : null;
      },
    }),
    [calendar.id],
  );
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const focus = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!draft.event?.series_id) return;
    const controller = new AbortController();
    setSeriesLoading(true);
    void getEvent(calendar.id, draft.event.series_id, controller.signal).then(
      (item) => {
        setSeries(item);
        setRecurrence(
          recurrenceFromRule(
            item.recurrence_rule ?? "",
            item.recurrence_timezone ?? "",
            item.start,
          ),
        );
        setStart(
          localInput(
            draft.event!.start,
            draft.event!.all_day,
            item.recurrence_timezone ?? browserTimezone(),
          ),
        );
        setEnd(
          localInput(
            draft.event!.end,
            draft.event!.all_day,
            item.recurrence_timezone ?? browserTimezone(),
          ),
        );
        setSeriesLoading(false);
      },
      (reason) => {
        if (!controller.signal.aborted) {
          setError(errorText(reason, labels.seriesError));
          setSeriesLoading(false);
        }
      },
    );
    return () => controller.abort();
  }, [calendar.id, draft.event, labels.seriesError]);
  const showValues = (item: Event) => {
    const zone = series?.recurrence_timezone || recurrence.timezone;
    setTitle(item.title);
    setDescription(item.description);
    setLocation(item.location ?? "");
    setAllDay(item.all_day);
    setStart(
      recurring
        ? localInput(item.start, item.all_day, zone)
        : dateInput(item.start, item.all_day),
    );
    setEnd(
      recurring
        ? localInput(item.end, item.all_day, zone)
        : dateInput(item.end, item.all_day),
    );
    setEventType(String(item.event_type ?? ""));
    setAssigned(item.employees);
    setInherit(new Set());
    setEmployeesExplicit(false);
    setError("");
  };
  const changeScope = (next: "occurrence" | "series") => {
    if (next === "series" && !series) return;
    setScope(next);
    showValues(next === "series" ? series! : draft.event!);
    if (next === "series" && series) {
      setRecurrence(
        recurrenceFromRule(
          series.recurrence_rule ?? "",
          series.recurrence_timezone ?? "",
          series.start,
        ),
      );
      setRecurrenceDirty(false);
    }
  };
  const inheritField = (field: string) => {
    if (!series) return;
    if (
      draft.event?.overrides?.[field as keyof NonNullable<Event["overrides"]>]
    )
      setInherit((current) => new Set(current).add(field));
    if (field === "title") setTitle(series.title);
    if (field === "description") setDescription(series.description);
    if (field === "location") setLocation(series.location ?? "");
    if (field === "event_type") setEventType(String(series.event_type ?? ""));
    if (field === "timeslot") {
      setAllDay(series.all_day);
      const original = inheritedOccurrenceSlot(
        series.start,
        series.end,
        draft.event!.recurrence_id!,
        series.all_day,
        series.recurrence_timezone ?? recurrence.timezone,
      );
      setStart(original.start);
      setEnd(original.end);
    }
    if (field === "employees") {
      setAssigned(series.employees);
      setEmployeesExplicit(false);
    }
  };
  const fieldModified = (field: keyof NonNullable<Event["overrides"]>) => {
    const initial = draft.event;
    if (!initial) return false;
    if (field === "title") return title !== initial.title;
    if (field === "description") return description !== initial.description;
    if (field === "location") return location !== (initial.location ?? "");
    if (field === "event_type")
      return eventType !== String(initial.event_type ?? "");
    if (field === "employees")
      return (
        employeesExplicit ||
        assigned
          .map((item) => item.id)
          .sort()
          .join(",") !==
          initial.employees
            .map((item) => item.id)
            .sort()
            .join(",")
      );
    const zone = series?.recurrence_timezone ?? recurrence.timezone;
    return (
      allDay !== initial.all_day ||
      start !== localInput(initial.start, initial.all_day, zone) ||
      end !== localInput(initial.end, initial.all_day, zone)
    );
  };
  const overrideIndicator = (field: keyof NonNullable<Event["overrides"]>) =>
    recurring &&
    scope === "occurrence" &&
    !seriesLoading &&
    series && (
      <div className="calendars-inheritance">
        <span>
          {!inherit.has(field) &&
          (draft.event?.overrides?.[field] || fieldModified(field))
            ? labels.valueCustom
            : labels.valueInherited}
        </span>
        {calendar.can_edit &&
          !inherit.has(field) &&
          (draft.event?.overrides?.[field] || fieldModified(field)) && (
            <Button type="button" onClick={() => inheritField(field)}>
              {labels.restoreField}
            </Button>
          )}
      </div>
    );
  const toggleAllDay = (checked: boolean) => {
    setInherit((current) => {
      const next = new Set(current);
      next.delete("timeslot");
      return next;
    });
    setAllDay(checked);
    const day = start.slice(0, 10);
    setStart(checked ? day : `${day}T09:00`);
    setEnd(
      checked
        ? end.slice(0, 10) > day
          ? end.slice(0, 10)
          : nextDay(day)
        : `${day}T10:00`,
    );
  };
  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    let write: EventWrite;
    try {
      const timezone =
        recurring && scope === "occurrence"
          ? series?.recurrence_timezone
          : recurrence.timezone;
      write = {
        title,
        description,
        location,
        start: allDay
          ? start
          : timezone && (recurring || recurrence.repeat !== "none")
            ? zonedInput(start, timezone)
            : eventInput(start, false),
        end: allDay
          ? end
          : timezone && (recurring || recurrence.repeat !== "none")
            ? zonedInput(end, timezone)
            : eventInput(end, false),
        all_day: allDay,
        event_type: eventType ? Number(eventType) : null,
        ...(calendar.calendar_type === "assignments"
          ? { employees: assigned.map((item) => item.id) }
          : {}),
      };
      if (
        !Number.isFinite(Date.parse(write.start)) ||
        !Number.isFinite(Date.parse(write.end)) ||
        new Date(write.end) <= new Date(write.start)
      ) {
        setError(labels.invalidDates);
        return;
      }
      if (
        scope === "series" &&
        (recurrenceDirty || (!draft.event && recurrence.repeat !== "none"))
      ) {
        Object.assign(write, recurrenceWrite(recurrence, start));
      }
    } catch {
      setError(
        recurring || recurrence.repeat !== "none"
          ? labels.recurrenceInvalid
          : labels.invalidDates,
      );
      return;
    }
    setPending(true);
    setError("");
    try {
      if (recurring && scope === "occurrence") {
        const initial = draft.event!;
        const changes: OccurrenceWrite = {};
        for (const field of ["title", "description", "location"] as const) {
          if (!inherit.has(field) && write[field] !== (initial[field] ?? ""))
            changes[field] = write[field];
        }
        if (
          !inherit.has("event_type") &&
          write.event_type !== initial.event_type
        )
          changes.event_type = write.event_type;
        if (
          !inherit.has("timeslot") &&
          (allDay !== initial.all_day ||
            start !==
              localInput(
                initial.start,
                initial.all_day,
                series?.recurrence_timezone ?? recurrence.timezone,
              ) ||
            end !==
              localInput(
                initial.end,
                initial.all_day,
                series?.recurrence_timezone ?? recurrence.timezone,
              ))
        ) {
          changes.start = write.start;
          changes.end = write.end;
          changes.all_day = write.all_day;
        }
        if (
          calendar.calendar_type === "assignments" &&
          !inherit.has("employees") &&
          (employeesExplicit ||
            assigned
              .map((item) => item.id)
              .sort()
              .join(",") !==
              initial.employees
                .map((item) => item.id)
                .sort()
                .join(","))
        )
          changes.employees = assigned.map((item) => item.id);
        if (inherit.size)
          changes.inherit = [...inherit].flatMap((field) =>
            field === "timeslot" ? ["start", "end", "all_day"] : [field],
          ) as OccurrenceWrite["inherit"];
        if (Object.keys(changes).length)
          await updateOccurrence(
            calendar.id,
            initial.series_id!,
            initial.recurrence_id!,
            changes,
          );
      } else if (draft.event && !recurring) {
        await updateEvent(calendar.id, Number(draft.event.id), write);
      } else if (draft.event) {
        const source = recurring ? series : draft.event;
        if (!source) return;
        const changes: Partial<EventWrite> = {};
        for (const field of [
          "title",
          "description",
          "location",
          "all_day",
          "event_type",
        ] as const) {
          if (
            field === "event_type"
              ? write.event_type !== source.event_type
              : write[field] !== (source[field] ?? "")
          )
            (changes as Record<string, unknown>)[field] = write[field];
        }
        if (
          source.all_day
            ? write.start !== source.start.slice(0, 10)
            : Date.parse(write.start) !== Date.parse(source.start)
        )
          changes.start = write.start;
        if (
          source.all_day
            ? write.end !== source.end.slice(0, 10)
            : Date.parse(write.end) !== Date.parse(source.end)
        )
          changes.end = write.end;
        if (
          calendar.calendar_type === "assignments" &&
          assigned
            .map((item) => item.id)
            .sort()
            .join(",") !==
            source.employees
              .map((item) => item.id)
              .sort()
              .join(",")
        )
          changes.employees = assigned.map((item) => item.id);
        if (recurrenceDirty)
          Object.assign(changes, recurrenceWrite(recurrence, start));
        if (Object.keys(changes).length)
          await updateEvent(calendar.id, Number(source.id), changes);
      } else await createEvent(calendar.id, write);
      onSaved();
    } catch (reason) {
      const conflict = reason as {
        status?: number;
        payload?: { code?: string; exceptions_revision?: number };
      };
      if (
        conflict.status === 409 &&
        conflict.payload?.code === "exceptions_reset_required" &&
        typeof conflict.payload.exceptions_revision === "number"
      ) {
        const source = series!;
        const changes: Partial<EventWrite> = {};
        for (const field of [
          "title",
          "description",
          "location",
          "all_day",
          "event_type",
        ] as const) {
          if (
            field === "event_type"
              ? write.event_type !== source.event_type
              : write[field] !== (source[field] ?? "")
          )
            (changes as Record<string, unknown>)[field] = write[field];
        }
        if (
          source.all_day
            ? write.start !== source.start.slice(0, 10)
            : Date.parse(write.start) !== Date.parse(source.start)
        )
          changes.start = write.start;
        if (
          source.all_day
            ? write.end !== source.end.slice(0, 10)
            : Date.parse(write.end) !== Date.parse(source.end)
        )
          changes.end = write.end;
        if (calendar.calendar_type === "assignments")
          changes.employees = assigned.map((item) => item.id);
        if (recurrenceDirty)
          Object.assign(changes, recurrenceWrite(recurrence, start));
        setResetRequest({
          write: changes,
          revision: conflict.payload.exceptions_revision,
        });
      } else
        setError(
          conflict.payload?.code === "exceptions_revision_changed"
            ? labels.revisionConflict
            : errorText(reason, labels.saveError),
        );
    } finally {
      setPending(false);
    }
  };
  const confirmReset = async () => {
    if (!series || !resetRequest) return;
    setPending(true);
    setError("");
    try {
      await updateEvent(calendar.id, Number(series.id), {
        ...resetRequest.write,
        reset_exceptions: true,
        exceptions_revision: resetRequest.revision,
      });
      setResetRequest(null);
      onSaved();
    } catch (reason) {
      const conflict = reason as {
        status?: number;
        payload?: { code?: string };
      };
      setError(
        conflict.status === 409
          ? labels.revisionConflict
          : errorText(reason, labels.saveError),
      );
      setResetRequest(null);
    } finally {
      setPending(false);
    }
  };
  const remove = async () => {
    if (!draft.event) return;
    setPending(true);
    setError("");
    try {
      if (confirmAction === "occurrence")
        await cancelOccurrence(
          calendar.id,
          draft.event.series_id!,
          draft.event.recurrence_id!,
        );
      else if (confirmAction === "restore")
        await restoreOccurrence(
          calendar.id,
          draft.event.series_id!,
          draft.event.recurrence_id!,
        );
      else
        await deleteEvent(
          calendar.id,
          recurring ? draft.event.series_id! : Number(draft.event.id),
        );
      onSaved();
    } catch (reason) {
      setError(errorText(reason, labels.saveError));
      setConfirmAction(null);
    } finally {
      setPending(false);
    }
  };
  return (
    <>
      <Sheet
        open
        onOpenChange={(open) => {
          if (!open && !pending) onClose();
        }}
      >
        <SheetContent>
          <SheetHeader>
            <SheetTitle>
              {draft.event ? labels.event : labels.newEvent}
            </SheetTitle>
            <SheetDescription>
              {calendar.name}
              {recurring
                ? ` · ${draft.event?.is_exception ? labels.modifiedOccurrence : labels.repeatedEvent}`
                : ""}
            </SheetDescription>
          </SheetHeader>
          {recurring && calendar.can_edit && (
            <label className="calendars-scope">
              {labels.editScope}
              <select
                value={scope}
                disabled={seriesLoading || pending}
                onChange={(event) =>
                  changeScope(event.target.value as "occurrence" | "series")
                }
              >
                <option value="occurrence">{labels.thisOccurrence}</option>
                <option value="series">{labels.entireSeries}</option>
              </select>
            </label>
          )}
          {seriesLoading && (
            <p className="calendars-help">{labels.seriesLoading}</p>
          )}
          <form
            className="calendars-form"
            onSubmit={(event) => void save(event)}
          >
            <label>
              {labels.titleField}
              <input
                required
                maxLength={200}
                disabled={!calendar.can_edit || seriesLoading}
                value={title}
                onChange={(event) => {
                  setTitle(event.target.value);
                  setInherit((current) => {
                    const next = new Set(current);
                    next.delete("title");
                    return next;
                  });
                }}
              />
            </label>
            {overrideIndicator("title")}
            <label>
              {labels.description}
              <textarea
                disabled={!calendar.can_edit || seriesLoading}
                value={description}
                onChange={(event) => {
                  setDescription(event.target.value);
                  setInherit((current) => {
                    const next = new Set(current);
                    next.delete("description");
                    return next;
                  });
                }}
              />
            </label>
            {overrideIndicator("description")}
            {(calendar.can_edit || location) && (
              <label>
                {labels.location}
                <input
                  maxLength={500}
                  disabled={!calendar.can_edit || seriesLoading}
                  value={location}
                  onChange={(event) => {
                    setLocation(event.target.value);
                    setInherit((current) => {
                      const next = new Set(current);
                      next.delete("location");
                      return next;
                    });
                  }}
                />
              </label>
            )}
            {overrideIndicator("location")}
            <label className="calendars-check">
              <input
                type="checkbox"
                checked={allDay}
                disabled={!calendar.can_edit || seriesLoading}
                onChange={(event) => toggleAllDay(event.target.checked)}
              />
              {labels.allDay}
            </label>
            <label>
              {labels.start}
              <input
                required
                type={allDay ? "date" : "datetime-local"}
                disabled={!calendar.can_edit || seriesLoading}
                value={start}
                onChange={(event) => {
                  setStart(event.target.value);
                  setInherit((current) => {
                    const next = new Set(current);
                    next.delete("timeslot");
                    return next;
                  });
                }}
              />
            </label>
            <label>
              {labels.end}
              <input
                required
                type={allDay ? "date" : "datetime-local"}
                disabled={!calendar.can_edit || seriesLoading}
                value={end}
                onChange={(event) => {
                  setEnd(event.target.value);
                  setInherit((current) => {
                    const next = new Set(current);
                    next.delete("timeslot");
                    return next;
                  });
                }}
              />
            </label>
            {overrideIndicator("timeslot")}
            <label>
              {labels.typeFilter}
              <select
                disabled={!calendar.can_edit || seriesLoading}
                value={eventType}
                onChange={(event) => {
                  setEventType(event.target.value);
                  setInherit((current) => {
                    const next = new Set(current);
                    next.delete("event_type");
                    return next;
                  });
                }}
              >
                <option value="">{labels.noType}</option>
                {types
                  .filter(
                    (item) =>
                      item.is_active || item.id === draft.event?.event_type,
                  )
                  .map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
              </select>
            </label>
            {overrideIndicator("event_type")}
            {(!draft.event || (recurring && scope === "series")) && (
              <>
                {advancedRule && (
                  <p className="calendars-help">{labels.advancedRule}</p>
                )}
                <RecurrenceFields
                  value={recurrence}
                  onChange={(value) => {
                    setRecurrence(value);
                    setRecurrenceDirty(true);
                  }}
                  labels={labels}
                  disabled={!calendar.can_edit || seriesLoading || advancedRule}
                />
              </>
            )}
            {calendar.calendar_type === "assignments" && (
              <div className="calendars-assignees">
                <span>{labels.assignedEmployees}</span>
                {calendar.can_edit && !seriesLoading && (
                  <div className="calendars-assignee-picker">
                    <EntitySearch
                      id="calendars-assignee"
                      label={labels.assignedEmployees}
                      value={employeeSelection}
                      source={employeeSource}
                      onChange={setEmployeeSelection}
                    />
                    <Button
                      type="button"
                      disabled={
                        !employeeSelection ||
                        assigned.some(
                          (item) => item.id === Number(employeeSelection),
                        )
                      }
                      onClick={() => {
                        const controller = new AbortController();
                        void resolveEligibleEmployee(
                          calendar.id,
                          employeeSelection,
                          controller.signal,
                        ).then(
                          (response) => {
                            const item = response.results[0];
                            if (item) {
                              setEmployeesExplicit(true);
                              setInherit((current) => {
                                const next = new Set(current);
                                next.delete("employees");
                                return next;
                              });
                              setAssigned((current) =>
                                current.some(
                                  (existing) => existing.id === item.id,
                                )
                                  ? current
                                  : [...current, item],
                              );
                            }
                            setEmployeeSelection("");
                          },
                          (reason) =>
                            setError(errorText(reason, labels.saveError)),
                        );
                      }}
                    >
                      {labels.add}
                    </Button>
                  </div>
                )}
                <ul>
                  {assigned.map((item) => (
                    <li key={item.id}>
                      <span>{item.label}</span>
                      {calendar.can_edit && (
                        <Button
                          type="button"
                          onClick={() => {
                            setEmployeesExplicit(true);
                            setInherit((current) => {
                              const next = new Set(current);
                              next.delete("employees");
                              return next;
                            });
                            setAssigned((current) =>
                              current.filter(
                                (employee) => employee.id !== item.id,
                              ),
                            );
                          }}
                        >
                          {labels.remove}
                        </Button>
                      )}
                    </li>
                  ))}
                </ul>
                {recurring && scope === "occurrence" && calendar.can_edit && (
                  <Button
                    type="button"
                    onClick={() => {
                      setAssigned([]);
                      setEmployeesExplicit(true);
                      setInherit((current) => {
                        const next = new Set(current);
                        next.delete("employees");
                        return next;
                      });
                    }}
                  >
                    {labels.emptyAssignments}
                  </Button>
                )}
                {overrideIndicator("employees")}
                {assigned.length > 0 && (
                  <p className="calendars-help">{labels.assignedHelp}</p>
                )}
              </div>
            )}
            {error && <Alert tone="danger">{error}</Alert>}
            <div className="calendars-actions">
              {draft.event && calendar.can_edit && (
                <Button
                  type="button"
                  ref={focus}
                  variant="destructive"
                  disabled={pending}
                  onClick={() =>
                    setConfirmAction(
                      recurring
                        ? scope === "series"
                          ? "series"
                          : "occurrence"
                        : "point",
                    )
                  }
                >
                  {recurring
                    ? scope === "series"
                      ? labels.deleteSeries
                      : labels.cancelOccurrence
                    : labels.delete}
                </Button>
              )}
              {recurring &&
                scope === "occurrence" &&
                draft.event?.is_exception &&
                calendar.can_edit && (
                  <Button
                    type="button"
                    disabled={pending}
                    onClick={() => setConfirmAction("restore")}
                  >
                    {labels.restoreOccurrence}
                  </Button>
                )}
              <Button type="button" onClick={onClose}>
                {calendar.can_edit ? labels.cancel : labels.close}
              </Button>
              {calendar.can_edit && (
                <Button
                  variant="primary"
                  disabled={pending || seriesLoading || (recurring && !series)}
                  type="submit"
                >
                  {labels.save}
                </Button>
              )}
            </div>
          </form>
        </SheetContent>
      </Sheet>
      {confirmAction && (
        <ConfirmDialog
          title={
            confirmAction === "series"
              ? labels.deleteSeries
              : confirmAction === "occurrence"
                ? labels.cancelOccurrence
                : confirmAction === "restore"
                  ? labels.restoreOccurrence
                  : labels.deleteEvent
          }
          description={
            confirmAction === "series"
              ? labels.deleteSeriesConfirm
              : confirmAction === "occurrence"
                ? labels.cancelOccurrenceConfirm
                : confirmAction === "restore"
                  ? labels.restoreOccurrenceConfirm
                  : labels.deleteEventDescription
          }
          pending={pending}
          error={error && <Alert tone="danger">{error}</Alert>}
          returnFocus={focus}
          onCancel={() => setConfirmAction(null)}
          onConfirm={() => void remove()}
          destructive={confirmAction !== "restore"}
        />
      )}
      {resetRequest && (
        <ConfirmDialog
          title={labels.resetExceptions}
          description={labels.resetExceptionsConfirm}
          pending={pending}
          error={error && <Alert tone="danger">{error}</Alert>}
          returnFocus={focus}
          onCancel={() => setResetRequest(null)}
          onConfirm={() => void confirmReset()}
          destructive
        />
      )}
    </>
  );
}

/** Read-only details for a synchronized event; no native event mutation is offered. */
function ExternalEventDetails({
  event,
  calendar,
  labels,
  onClose,
}: {
  event: Event;
  calendar: Calendar;
  labels: Locale;
  onClose: () => void;
}) {
  const { language } = usePluginTranslation("calendars");
  const displayDate = (value: string) =>
    event.all_day
      ? value.slice(0, 10)
      : new Date(value).toLocaleString(language);
  return (
    <Sheet
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <SheetContent>
        <SheetHeader>
          <SheetTitle>{event.title || labels.event}</SheetTitle>
          <SheetDescription>{calendar.name}</SheetDescription>
        </SheetHeader>
        <dl className="calendars-external-details">
          <div>
            <dt>{labels.start}</dt>
            <dd>{displayDate(event.start)}</dd>
          </div>
          <div>
            <dt>{labels.end}</dt>
            <dd>{displayDate(event.end)}</dd>
          </div>
          {event.location && (
            <div>
              <dt>{labels.location}</dt>
              <dd>{event.location}</dd>
            </div>
          )}
          {event.description && (
            <div>
              <dt>{labels.description}</dt>
              <dd className="calendars-event-description">
                {event.description}
              </dd>
            </div>
          )}
        </dl>
        <div className="calendars-actions">
          <Button onClick={onClose}>{labels.close}</Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}

function TypeRow({
  calendarId,
  item,
  labels,
  changed,
}: {
  calendarId: number;
  item: EventType;
  labels: Locale;
  changed: () => void;
}) {
  const [name, setName] = useState(item.name);
  const [color, setColor] = useState(item.color || "#3b82f6");
  const [customColor, setCustomColor] = useState(Boolean(item.color));
  const [active, setActive] = useState(item.is_active);
  const [pending, setPending] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState("");
  const focus = useRef<HTMLButtonElement>(null);
  const save = async () => {
    setPending(true);
    setError("");
    try {
      await updateType(calendarId, item.id, {
        name,
        color: customColor ? color : "",
        is_active: active,
      });
      changed();
    } catch (reason) {
      setError(errorText(reason, labels.saveError));
    } finally {
      setPending(false);
    }
  };
  const remove = async () => {
    setPending(true);
    setError("");
    try {
      await deleteType(calendarId, item.id);
      changed();
    } catch (reason) {
      setError(errorText(reason, labels.saveError));
      setConfirmDelete(false);
    } finally {
      setPending(false);
    }
  };
  return (
    <div className="calendars-type-row">
      <input
        aria-label={labels.name}
        value={name}
        onChange={(event) => setName(event.target.value)}
      />
      <label className="calendars-check">
        <input
          type="checkbox"
          checked={customColor}
          onChange={(event) => setCustomColor(event.target.checked)}
        />
        {labels.customColor}
      </label>
      {customColor && (
        <input
          aria-label={labels.color}
          type="color"
          value={color}
          onChange={(event) => setColor(event.target.value)}
        />
      )}
      <label className="calendars-check">
        <input
          type="checkbox"
          checked={active}
          onChange={(event) => setActive(event.target.checked)}
        />
        {labels.active}
      </label>
      <Button disabled={pending} onClick={() => void save()}>
        {labels.save}
      </Button>
      <Button
        ref={focus}
        disabled={pending}
        variant="destructive"
        onClick={() => setConfirmDelete(true)}
      >
        {labels.delete}
      </Button>
      {error && <Alert tone="danger">{error}</Alert>}
      {confirmDelete && (
        <ConfirmDialog
          title={labels.deleteType}
          description={labels.deleteTypeDescription}
          pending={pending}
          error={error && <Alert tone="danger">{error}</Alert>}
          returnFocus={focus}
          onCancel={() => setConfirmDelete(false)}
          onConfirm={() => void remove()}
          destructive
        />
      )}
    </div>
  );
}

const attachmentSources: Record<keyof Attachments, EntitySearchSource> = {
  employees: employeeFilterSources.allEmployees,
  teams: teamFilterSources.teams,
  projects: projectFilterSources.projects,
};

function AttachmentsEditor({
  calendarId,
  labels,
  onSaved,
}: {
  calendarId: number;
  labels: Locale;
  onSaved: (attachments: Attachments) => void;
}) {
  const [attachments, setAttachments] = useState<Attachments | null>(null);
  const [selection, setSelection] = useState<Record<keyof Attachments, string>>(
    { employees: "", teams: "", projects: "" },
  );
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);
  const [removal, setRemoval] = useState<{
    kind: keyof Attachments;
    item: Attachment;
  } | null>(null);
  const removeFocus = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const controller = new AbortController();
    listAttachments(calendarId, controller.signal).then(
      (result) => {
        if (!controller.signal.aborted) setAttachments(result);
      },
      (reason) => {
        if (!controller.signal.aborted)
          setError(errorText(reason, labels.attachmentError));
      },
    );
    return () => controller.abort();
  }, [calendarId, labels.attachmentError]);
  const change = async (kind: keyof Attachments, ids: number[]) => {
    setPending(true);
    setError("");
    setSuccess(false);
    try {
      const result = await saveAttachments(calendarId, { [kind]: ids });
      setAttachments(result);
      setSelection((current) => ({ ...current, [kind]: "" }));
      setRemoval(null);
      setSuccess(true);
      onSaved(result);
    } catch (reason) {
      setError(errorText(reason, labels.saveError));
      setRemoval(null);
    } finally {
      setPending(false);
    }
  };
  if (!attachments)
    return (
      <div role="status">
        {error ? <Alert tone="danger">{error}</Alert> : labels.loading}
      </div>
    );
  return (
    <section className="calendars-attachments">
      <h3>{labels.attachments}</h3>
      {(["employees", "teams", "projects"] as const).map((kind) => (
        <div key={kind} className="calendars-attachment-group">
          <label>
            {labels[kind]}
            <EntitySearch
              id={`calendars-${kind}`}
              label={labels[kind]}
              value={selection[kind]}
              source={attachmentSources[kind]}
              onChange={(value) =>
                setSelection((current) => ({ ...current, [kind]: value }))
              }
            />
          </label>
          <Button
            disabled={
              pending ||
              !selection[kind] ||
              attachments[kind].some(
                (item) => item.id === Number(selection[kind]),
              )
            }
            onClick={() =>
              void change(kind, [
                ...attachments[kind].map((item) => item.id),
                Number(selection[kind]),
              ])
            }
          >
            {labels.add}
          </Button>
          <ul>
            {attachments[kind].map((item) => (
              <li key={item.id}>
                <span>{item.label}</span>
                <Button
                  ref={removeFocus}
                  disabled={pending}
                  variant="destructive"
                  onClick={() => setRemoval({ kind, item })}
                >
                  {labels.remove}
                </Button>
              </li>
            ))}
          </ul>
        </div>
      ))}
      {success && <Alert tone="success">{labels.saved}</Alert>}
      {error && <Alert tone="danger">{error}</Alert>}
      {removal && (
        <ConfirmDialog
          title={`${labels.remove} · ${removal.item.label}`}
          description={labels.removeAttachmentDescription}
          pending={pending}
          returnFocus={removeFocus}
          onCancel={() => setRemoval(null)}
          onConfirm={() =>
            void change(
              removal.kind,
              attachments[removal.kind]
                .filter((item) => item.id !== removal.item.id)
                .map((item) => item.id),
            )
          }
          destructive
        />
      )}
    </section>
  );
}

type ManageSection = "settings" | "types" | "attachments" | "members";
function ManageSheet({
  calendar,
  types,
  typesError,
  labels,
  initialSection,
  onCalendarSaved,
  onTypesChanged,
  onRetryTypes,
  onAttachmentsChanged,
  onClose,
}: {
  calendar: Calendar;
  types: EventType[];
  labels: Locale;
  onCalendarSaved: (calendar: Calendar) => void;
  initialSection: ManageSection;
  typesError: boolean;
  onTypesChanged: () => void;
  onRetryTypes: () => void;
  onAttachmentsChanged: (attachments: Attachments) => void;
  onClose: () => void;
}) {
  const [section, setSection] = useState<ManageSection>(initialSection);
  const [currentCalendar, setCurrentCalendar] = useState(calendar);
  const [syncPending, setSyncPending] = useState(false);
  const [syncNotice, setSyncNotice] = useState("");
  const [syncError, setSyncError] = useState("");
  const [name, setName] = useState("");
  const [color, setColor] = useState("#3b82f6");
  const [customColor, setCustomColor] = useState(false);
  const [members, setMembers] = useState<Member[]>([]);
  const [owner, setOwner] = useState("");
  const [memberId, setMemberId] = useState("");
  const [memberLevel, setMemberLevel] = useState<Role>("view");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const { language } = usePluginTranslation("calendars");
  const source = currentCalendar.external;
  const refreshCalendar = useCallback(async () => {
    const response = await listCalendars(undefined, undefined, "managed");
    const refreshed = response.results.find((item) => item.id === calendar.id);
    if (refreshed) setCurrentCalendar(refreshed);
  }, [calendar.id]);
  useEffect(() => {
    if (source?.status !== "queued" && source?.status !== "running") return;
    const timer = window.setInterval(() => {
      void refreshCalendar().catch(() => setSyncError(labels.syncStatusError));
    }, 5000);
    return () => window.clearInterval(timer);
  }, [source?.status, refreshCalendar, labels.syncStatusError]);
  const synchronize = async () => {
    if (
      syncPending ||
      source?.status === "queued" ||
      source?.status === "running"
    )
      return;
    setSyncPending(true);
    setSyncError("");
    setSyncNotice("");
    try {
      await syncCalendar(calendar.id);
      setCurrentCalendar((item) => ({
        ...item,
        external: item.external
          ? { ...item.external, status: "queued" }
          : undefined,
      }));
      setSyncNotice(labels.syncQueued);
      try {
        await refreshCalendar();
      } catch {
        setSyncError(labels.syncStatusError);
      }
    } catch (reason) {
      setSyncError(errorText(reason, labels.syncError));
    } finally {
      setSyncPending(false);
    }
  };
  const statusLabels: Record<string, string> = {
    pending: labels.syncPending,
    queued: labels.syncQueuedStatus,
    running: labels.syncRunning,
    success: labels.syncSuccess,
    error: labels.syncFailed,
  };
  const refreshMembers = useCallback(async () => {
    const response = await listMembers(calendar.id);
    setOwner(response.owner.label);
    setMembers(response.results);
  }, [calendar.id]);
  useEffect(() => {
    if (section !== "members") return;
    let live = true;
    listMembers(calendar.id).then(
      (response) => {
        if (live) {
          setOwner(response.owner.label);
          setMembers(response.results);
        }
      },
      (reason) => {
        if (live) setError(errorText(reason, labels.noAccess));
      },
    );
    return () => {
      live = false;
    };
  }, [calendar.id, section, labels.noAccess]);
  const users = useMemo<EntitySearchSource>(
    () => ({
      async search(query, signal) {
        const response = await searchUsers(calendar.id, query, signal);
        return {
          options: response.results.map((user) => ({
            value: String(user.id),
            label: user.label,
          })),
          hasMore: response.has_more,
        };
      },
      async resolve(id, signal) {
        const response = await resolveUser(calendar.id, id, signal);
        const user = response.results[0];
        return user ? { value: String(user.id), label: user.label } : null;
      },
    }),
    [calendar.id],
  );
  const mutate = async (action: () => Promise<unknown>, after: () => void) => {
    setPending(true);
    setError("");
    try {
      await action();
      await refreshMembers();
      after();
    } catch (reason) {
      setError(errorText(reason, labels.saveError));
    } finally {
      setPending(false);
    }
  };
  const addType = async (event: React.FormEvent) => {
    event.preventDefault();
    setPending(true);
    setError("");
    try {
      await createType(calendar.id, {
        name,
        color: customColor ? color : "",
        is_active: true,
      });
      setName("");
      onTypesChanged();
    } catch (reason) {
      setError(errorText(reason, labels.saveError));
    } finally {
      setPending(false);
    }
  };
  return (
    <Sheet
      open
      onOpenChange={(open) => {
        if (!open && !pending) onClose();
      }}
    >
      <SheetContent className="calendars-manage-sheet">
        <SheetHeader>
          <SheetTitle>
            {labels.manage} · {calendar.name}
          </SheetTitle>
          <SheetDescription>{labels.description}</SheetDescription>
        </SheetHeader>
        <div
          className="calendars-tabs"
          role="tablist"
          aria-label={labels.manage}
        >
          {(["settings", "types", "attachments", "members"] as const)
            .filter(
              (key) => key !== "types" || calendar.calendar_type !== "external",
            )
            .map((key) => (
              <Button
                key={key}
                role="tab"
                aria-selected={section === key}
                variant={section === key ? "primary" : "ghost"}
                onClick={() => {
                  setError("");
                  setSection(key);
                }}
              >
                {labels[key]}
              </Button>
            ))}
        </div>
        {section === "settings" && (
          <>
            <CalendarForm
              calendar={currentCalendar}
              labels={labels}
              onSaved={onCalendarSaved}
              onClose={onClose}
            />
            {calendar.calendar_type === "external" &&
              source &&
              calendar.can_manage && (
                <section
                  className="calendars-sync"
                  aria-label={labels.syncStatus}
                >
                  <h3>{labels.syncStatus}</h3>
                  <p>
                    {labels.syncState}:{" "}
                    {statusLabels[source.status] ?? labels.syncUnknown}
                  </p>
                  <p>
                    {labels.lastSync}:{" "}
                    {source.last_success
                      ? new Date(source.last_success).toLocaleString(language)
                      : labels.neverSynced}
                  </p>
                  {source.last_error && (
                    <Alert tone="danger">{source.last_error}</Alert>
                  )}
                  {!!Object.keys(source.statistics).length && (
                    <p>
                      {labels.syncStatistics}: {labels.syncCreated}{" "}
                      {source.statistics.created ?? 0} · {labels.syncUpdated}{" "}
                      {source.statistics.updated ?? 0} · {labels.syncRemoved}{" "}
                      {source.statistics.deactivated ?? 0}
                    </p>
                  )}
                  {syncNotice && <Alert tone="success">{syncNotice}</Alert>}
                  {syncError && <Alert tone="danger">{syncError}</Alert>}
                  <div className="calendars-sync-actions">
                    <Button
                      variant="secondary"
                      disabled={
                        syncPending ||
                        !source.enabled ||
                        !source.has_url ||
                        source.status === "queued" ||
                        source.status === "running"
                      }
                      onClick={() => void synchronize()}
                    >
                      {labels.syncNow}
                    </Button>
                    <Button
                      disabled={syncPending}
                      onClick={() =>
                        void refreshCalendar().catch(() =>
                          setSyncError(labels.syncStatusError),
                        )
                      }
                    >
                      {labels.refreshStatus}
                    </Button>
                  </div>
                </section>
              )}
          </>
        )}
        {section === "attachments" && (
          <AttachmentsEditor
            calendarId={calendar.id}
            labels={labels}
            onSaved={onAttachmentsChanged}
          />
        )}
        {section === "types" && (
          <div className="calendars-form">
            {typesError && (
              <Alert tone="danger">
                {labels.typesError}{" "}
                <Button onClick={onRetryTypes}>{labels.retry}</Button>
              </Alert>
            )}
            <form
              className="calendars-type-row"
              onSubmit={(event) => void addType(event)}
            >
              <input
                required
                aria-label={labels.newType}
                placeholder={labels.newType}
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
              <label className="calendars-check">
                <input
                  type="checkbox"
                  checked={customColor}
                  onChange={(event) => setCustomColor(event.target.checked)}
                />
                {labels.customColor}
              </label>
              {customColor && (
                <input
                  aria-label={labels.color}
                  type="color"
                  value={color}
                  onChange={(event) => setColor(event.target.value)}
                />
              )}
              <Button type="submit" variant="primary" disabled={pending}>
                {labels.add}
              </Button>
            </form>
            {types.map((item) => (
              <TypeRow
                key={item.id}
                calendarId={calendar.id}
                item={item}
                labels={labels}
                changed={onTypesChanged}
              />
            ))}
          </div>
        )}
        {section === "members" && (
          <div className="calendars-form">
            <p>
              {labels.owner}: {owner || labels.loading}
            </p>
            <label>
              {labels.user}
              <EntitySearch
                id="calendars-user"
                label={labels.user}
                value={memberId}
                source={users}
                onChange={setMemberId}
              />
            </label>
            <label>
              {labels.level}
              <select
                value={memberLevel}
                onChange={(event) => setMemberLevel(event.target.value as Role)}
              >
                <option value="view">{labels.view}</option>
                <option value="edit">{labels.edit}</option>
                <option value="manage">{labels.manageLevel}</option>
              </select>
            </label>
            <Button
              variant="primary"
              disabled={pending || !memberId}
              onClick={() =>
                void mutate(
                  () => setMember(calendar.id, Number(memberId), memberLevel),
                  () => setMemberId(""),
                )
              }
            >
              {labels.add}
            </Button>
            <ul className="calendars-members">
              {members.map((member) => (
                <li key={member.user.id}>
                  <span>{member.user.label}</span>
                  <select
                    aria-label={`${labels.level} · ${member.user.label}`}
                    value={member.level}
                    disabled={pending}
                    onChange={(event) =>
                      void mutate(
                        () =>
                          setMember(
                            calendar.id,
                            member.user.id,
                            event.target.value as Role,
                          ),
                        () => {},
                      )
                    }
                  >
                    <option value="view">{labels.view}</option>
                    <option value="edit">{labels.edit}</option>
                    <option value="manage">{labels.manageLevel}</option>
                  </select>
                  <Button
                    disabled={pending}
                    variant="destructive"
                    onClick={() =>
                      void mutate(
                        () => removeMember(calendar.id, member.user.id),
                        () => {},
                      )
                    }
                  >
                    {labels.remove}
                  </Button>
                </li>
              ))}
            </ul>
          </div>
        )}
        {error && <Alert tone="danger">{error}</Alert>}
      </SheetContent>
    </Sheet>
  );
}

/** Shared checkbox selection for the global multi view and contextual tabs. */
function MultipleCalendarChoices({
  calendars,
  enabled,
  onChange,
  labels,
  onSingle,
}: {
  calendars: Calendar[];
  enabled: number[];
  onChange: (ids: number[]) => void;
  labels: Locale;
  onSingle?: () => void;
}) {
  return (
    <fieldset className="calendars-context-choices">
      <legend>{labels.visibleCalendars}</legend>
      <div className="calendars-selection-actions">
        <span role="status">
          {labels.selectedCalendars.replace("{count}", String(enabled.length))}
        </span>
        <Button onClick={() => onChange(calendars.map((item) => item.id))}>
          {labels.selectAll}
        </Button>
        <Button onClick={() => onChange([])}>{labels.deselectAll}</Button>
        {onSingle && (
          <Button onClick={onSingle}>{labels.singleCalendar}</Button>
        )}
      </div>
      {calendars.map((calendar) => (
        <div key={calendar.id} className="calendars-context-item">
          <label className="calendars-context-choice">
            <input
              type="checkbox"
              checked={enabled.includes(calendar.id)}
              onChange={(event) =>
                onChange(
                  event.target.checked
                    ? [...enabled, calendar.id]
                    : enabled.filter((id) => id !== calendar.id),
                )
              }
            />
            <span
              className="calendars-context-swatch"
              style={{ backgroundColor: calendar.color }}
            />
            {calendar.name}
          </label>
          <PluginPublishedCalendarActions
            providerSlug="calendars"
            calendarId={String(calendar.id)}
          />
        </div>
      ))}
    </fieldset>
  );
}

export function Page({ context }: { context: Record<string, unknown> }) {
  void context;
  const labels = useLabels();
  const [calendars, setCalendars] = useState<Calendar[]>([]);
  const [calendarId, setCalendarId] = useState<number | null>(null);
  const [multiple, setMultiple] = useState(false);
  const [enabled, setEnabled] = useState<number[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [types, setTypes] = useState<EventType[]>([]);
  const [typesError, setTypesError] = useState(false);
  const [typeFilter, setTypeFilter] = useState("");
  const [events, setEvents] = useState<Event[]>([]);
  const [range, setRange] = useState<Range | null>(null);
  const [mode, setMode] = useState<"calendar" | "list" | "resources">(
    "calendar",
  );
  const [resourceResult, setResourceResult] = useState<{
    key: string;
    rows: Attachment[];
  } | null>(null);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [eventsError, setEventsError] = useState(false);
  const [revision, setRevision] = useState(0);
  const [typesRevision, setTypesRevision] = useState(0);
  const [draft, setDraft] = useState<EventDraft | null>(null);
  const [choosing, setChoosing] = useState<EventDraft | null>(null);
  const [destinationId, setDestinationId] = useState<number | null>(null);
  const selected = calendars.find((item) => item.id === calendarId) ?? null;
  const active = multiple
    ? calendars.filter((item) => enabled.includes(item.id))
    : selected
      ? [selected]
      : [];
  const editable = active.filter(
    (item) => item.can_edit && item.calendar_type !== "external",
  );
  const formCalendar = multiple
    ? (calendars.find((item) => item.id === destinationId) ?? null)
    : selected;
  const formCalendarId = formCalendar?.id;
  const resourceKey = range
    ? `${active
        .filter((item) => item.calendar_type === "assignments")
        .map((item) => item.id)
        .join(",")}:${range.start}:${range.end}`
    : "";
  const reload = useCallback(async (signal?: AbortSignal) => {
    setLoadError(false);
    try {
      const response = await listCalendars(signal, undefined, "general");
      if (signal?.aborted) return;
      setCalendars(response.results);
      setEnabled((current) =>
        current.filter((id) => response.results.some((item) => item.id === id)),
      );
      setCalendarId((current) =>
        response.results.some((item) => item.id === current)
          ? current
          : (response.results[0]?.id ?? null),
      );
    } catch {
      if (!signal?.aborted) setLoadError(true);
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    void reload(controller.signal);
    return () => controller.abort();
  }, [reload]);
  useEffect(() => {
    if (!formCalendarId || formCalendar?.calendar_type === "external") {
      setTypes([]);
      return;
    }
    const controller = new AbortController();
    setTypesError(false);
    listTypes(formCalendarId, controller.signal).then(
      (response) => {
        if (!controller.signal.aborted) setTypes(response.results);
      },
      () => {
        if (!controller.signal.aborted) {
          setTypes([]);
          setTypesError(true);
        }
      },
    );
    return () => controller.abort();
  }, [formCalendarId, formCalendar?.calendar_type, typesRevision]);
  useEffect(() => {
    if (!active.length || !range) {
      setEvents([]);
      setEventsLoading(false);
      return;
    }
    const controller = new AbortController();
    setEvents([]);
    setEventsLoading(true);
    setEventsError(false);
    Promise.all(
      active.map((calendar) =>
        listEvents(calendar.id, range.start, range.end, controller.signal),
      ),
    ).then(
      (results) => {
        if (!controller.signal.aborted) {
          setEvents(results.flatMap((result) => result.results));
          setEventsLoading(false);
        }
      },
      () => {
        if (!controller.signal.aborted) {
          setEventsError(true);
          setEventsLoading(false);
        }
      },
    );
    return () => controller.abort();
  }, [multiple, calendarId, enabled, calendars, range, revision]);
  useEffect(() => {
    if (mode !== "resources" || !range) return;
    const assignments = active.filter(
      (item) => item.calendar_type === "assignments",
    );
    if (!assignments.length) return;
    const controller = new AbortController();
    Promise.all(
      assignments.map((item) =>
        listResources(item.id, range.start, range.end, controller.signal),
      ),
    ).then(
      (results) => {
        if (!controller.signal.aborted)
          setResourceResult({
            key: resourceKey,
            rows: [
              ...new Map(
                results
                  .flatMap((result) => result.results)
                  .map((item) => [item.id, item]),
              ).values(),
            ],
          });
      },
      () => {
        if (!controller.signal.aborted)
          setResourceResult({ key: resourceKey, rows: [] });
      },
    );
    return () => controller.abort();
  }, [mode, multiple, calendarId, enabled, calendars, range, resourceKey]);
  const visibleEvents = multiple
    ? events.filter((item) => enabled.includes(item.calendar))
    : typeFilter
      ? events.filter((item) => String(item.event_type ?? "") === typeFilter)
      : events;
  const openCreate = (info: {
    start: string;
    end: string;
    allDay: boolean;
  }) => {
    if (!editable.length) return;
    const next = {
      event: null,
      range: draftRange(info.start, info.end, info.allDay),
      allDay: info.allDay,
    };
    if (multiple) {
      setDestinationId(null);
      setChoosing(next);
    } else setDraft(next);
  };
  return (
    <section className="calendars-page">
      {/* <header className="calendars-header"><h1>{labels.title}</h1></header> */}
      {loading && <p role="status">{labels.loading}</p>}
      {loadError && (
        <Alert tone="danger">
          {labels.loadError}{" "}
          <Button onClick={() => void reload()}>{labels.retry}</Button>
        </Alert>
      )}
      {!loading && !loadError && !calendars.length && <p>{labels.empty}</p>}
      {selected && (
        <>
          <div className="calendars-toolbar">
            {!multiple && (
              <label>
                {labels.choose}
                <select
                  value={selected.id}
                  onChange={(event) => {
                    if (event.target.value === "multiple") {
                      setEnabled([selected.id]);
                      setMultiple(true);
                      setDraft(null);
                      return;
                    }
                    setCalendarId(Number(event.target.value));
                    setTypeFilter("");
                    setDraft(null);
                  }}
                >
                  {calendars.map((calendar) => (
                    <option key={calendar.id} value={calendar.id}>
                      {calendar.name}
                    </option>
                  ))}
                  {calendars.length > 1 && (
                    <option value="multiple">{labels.selectMultiple}</option>
                  )}
                </select>
              </label>
            )}
            {!multiple && selected.calendar_type !== "external" && (
              <label>
                {labels.typeFilter}
                <select
                  value={typeFilter}
                  onChange={(event) => setTypeFilter(event.target.value)}
                >
                  <option value="">{labels.allTypes}</option>
                  {types.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {!multiple && (
              <PluginPublishedCalendarActions
                providerSlug="calendars"
                calendarId={String(selected.id)}
              />
            )}
            {!!editable.length && (
              <Button
                onClick={() => {
                  const start =
                    range?.start ?? new Date().toISOString().slice(0, 10);
                  const next = {
                    event: null,
                    range: { start, end: nextDay(start) },
                    allDay: true,
                  };
                  if (multiple) {
                    setDestinationId(null);
                    setChoosing(next);
                  } else setDraft(next);
                }}
              >
                {labels.addEvent}
              </Button>
            )}
          </div>
          {multiple && (
            <MultipleCalendarChoices
              calendars={calendars}
              enabled={enabled}
              onChange={(ids) => {
                setEnabled(ids);
                setDraft(null);
                setChoosing(null);
              }}
              labels={labels}
              onSingle={() => {
                setMultiple(false);
                setDraft(null);
                setChoosing(null);
                setDestinationId(null);
              }}
            />
          )}
          {typesError && (
            <Alert tone="danger">
              {labels.typesError}{" "}
              <Button onClick={() => setTypesRevision((value) => value + 1)}>
                {labels.retry}
              </Button>
            </Alert>
          )}
          {eventsLoading && <p role="status">{labels.loading}</p>}
          {eventsError && (
            <Alert tone="danger">
              {labels.eventsError}{" "}
              <Button onClick={() => setRevision((value) => value + 1)}>
                {labels.retry}
              </Button>
            </Alert>
          )}
          <PluginCalendarView
            title={multiple ? labels.title : selected.name}
            events={visibleEvents.map((item) =>
              presentationEvent(
                item,
                calendars.find((calendar) => calendar.id === item.calendar)
                  ?.name ?? "",
                multiple ? `${item.calendar}:${item.id}` : String(item.id),
              ),
            )}
            showCalendarName={multiple}
            resources={(resourceResult?.key === resourceKey
              ? resourceResult.rows
              : []
            ).map((item) => ({ id: `employee:${item.id}`, title: item.label }))}
            allowResources={active.some(
              (item) => item.calendar_type === "assignments",
            )}
            canCreate={editable.length > 0}
            onRangeChange={setRange}
            onModeChange={setMode}
            onDateSelect={openCreate}
            onEventClick={(id) => {
              const item = events.find(
                (event) =>
                  (multiple
                    ? `${event.calendar}:${event.id}`
                    : String(event.id)) === id,
              );
              if (item) {
                setDestinationId(item.calendar);
                setDraft({
                  event: item,
                  range: { start: item.start, end: item.end },
                  allDay: item.all_day,
                });
              }
            }}
          />
        </>
      )}
      {choosing && (
        <Sheet
          open
          onOpenChange={(open) => {
            if (!open) setChoosing(null);
          }}
        >
          <SheetContent>
            <SheetHeader>
              <SheetTitle>{labels.chooseDestination}</SheetTitle>
            </SheetHeader>
            <div className="calendars-form">
              <label>
                {labels.choose}
                <select
                  value={destinationId ?? ""}
                  onChange={(event) =>
                    setDestinationId(Number(event.target.value) || null)
                  }
                >
                  <option value="">—</option>
                  {editable.map((calendar) => (
                    <option key={calendar.id} value={calendar.id}>
                      {calendar.name}
                    </option>
                  ))}
                </select>
              </label>
              <div className="calendars-actions">
                <Button onClick={() => setChoosing(null)}>
                  {labels.cancel}
                </Button>
                <Button
                  variant="primary"
                  disabled={
                    !destinationId ||
                    !editable.some((calendar) => calendar.id === destinationId)
                  }
                  onClick={() => {
                    setDraft(choosing);
                    setChoosing(null);
                  }}
                >
                  {labels.continue}
                </Button>
              </div>
            </div>
          </SheetContent>
        </Sheet>
      )}
      {formCalendar &&
        draft &&
        (formCalendar.calendar_type === "external" && draft.event ? (
          <ExternalEventDetails
            event={draft.event}
            calendar={formCalendar}
            labels={labels}
            onClose={() => setDraft(null)}
          />
        ) : (
          <EventForm
            key={`${formCalendar.id}:${draft.event?.id ?? "new"}:${draft.range.start}`}
            calendar={formCalendar}
            draft={draft}
            types={types}
            labels={labels}
            onSaved={() => {
              setDraft(null);
              setRevision((value) => value + 1);
            }}
            onClose={() => setDraft(null)}
          />
        ))}
    </section>
  );
}

export function SettingsPanel({
  context,
}: {
  context: Record<string, unknown>;
}) {
  void context;
  const labels = useLabels();
  const [calendars, setCalendars] = useState<Calendar[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<{
    calendar: Calendar;
    section: ManageSection;
  } | null>(null);
  const [types, setTypes] = useState<EventType[]>([]);
  const [typesError, setTypesError] = useState(false);
  const typesRequest = useRef(0);
  const reload = useCallback(async (signal?: AbortSignal) => {
    setLoadError(false);
    try {
      const response = await listCalendars(signal, undefined, "managed");
      if (!signal?.aborted) setCalendars(response.results);
    } catch {
      if (!signal?.aborted) setLoadError(true);
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    void reload(controller.signal);
    return () => controller.abort();
  }, [reload]);
  const refreshTypes = useCallback(async (calendarId: number) => {
    const request = ++typesRequest.current;
    setTypesError(false);
    try {
      const result = await listTypes(calendarId);
      if (request === typesRequest.current) setTypes(result.results);
    } catch {
      if (request === typesRequest.current) setTypesError(true);
    }
  }, []);
  const openManagement = (calendar: Calendar, section: ManageSection) => {
    setTypes([]);
    setEditing({ calendar, section });
    if (calendar.calendar_type !== "external") void refreshTypes(calendar.id);
  };
  const savedCalendar = () => {
    setCreateOpen(false);
    setEditing(null);
    void reload();
  };
  const attachmentLabels = (calendar: Calendar) => {
    const groups = calendar.attachments;
    if (!groups) return "—";
    const names = (["employees", "teams", "projects"] as const).flatMap(
      (key) =>
        groups[key].length
          ? [
              `${labels[key]}: ${groups[key].map((item) => item.label).join(", ")}`,
            ]
          : [],
    );
    return names.length ? names.join(" · ") : "—";
  };
  return (
    <div className="calendars-settings">
      <div className="calendars-header">
        <h3>{labels.managedCalendars}</h3>
        <Button variant="primary" onClick={() => setCreateOpen(true)}>
          {labels.createCalendar}
        </Button>
      </div>
      {loading && <p role="status">{labels.loading}</p>}
      {loadError && (
        <Alert tone="danger">
          {labels.loadError}{" "}
          <Button onClick={() => void reload()}>{labels.retry}</Button>
        </Alert>
      )}
      {!loading && !loadError && !calendars.length && (
        <p>{labels.managedEmpty}</p>
      )}
      {!!calendars.length && (
        <div className="calendars-table-scroll">
          <table className="calendars-table">
            <thead>
              <tr>
                <th scope="col">{labels.name}</th>
                <th scope="col">{labels.calendarType}</th>
                <th scope="col">{labels.attachments}</th>
                <th scope="col">{labels.actions}</th>
              </tr>
            </thead>
            <tbody>
              {calendars.map((calendar) => (
                <tr key={calendar.id}>
                  <td>
                    <span className="calendars-name">
                      <span
                        className="calendars-swatch"
                        style={{ backgroundColor: calendar.color }}
                        aria-hidden="true"
                      />
                      {calendar.name}
                    </span>
                  </td>
                  <td>
                    {calendar.calendar_type === "assignments"
                      ? labels.assignmentsCalendar
                      : calendar.calendar_type === "external"
                        ? labels.externalCalendar
                        : labels.eventsCalendar}
                  </td>
                  <td>{attachmentLabels(calendar)}</td>
                  <td>
                    {calendar.can_manage && (
                      <EntityActionMenu
                        label={`${labels.actions} · ${calendar.name}`}
                        groups={[
                          [
                            {
                              id: "settings",
                              label: labels.settings,
                              onSelect: () =>
                                openManagement(calendar, "settings"),
                            },
                            ...(calendar.calendar_type === "external"
                              ? []
                              : [
                                  {
                                    id: "types",
                                    label: labels.types,
                                    onSelect: () =>
                                      openManagement(
                                        calendar,
                                        "types" as const,
                                      ),
                                  },
                                ]),
                            {
                              id: "attachments",
                              label: labels.attachments,
                              onSelect: () =>
                                openManagement(calendar, "attachments"),
                            },
                            {
                              id: "members",
                              label: labels.members,
                              onSelect: () =>
                                openManagement(calendar, "members"),
                            },
                          ],
                        ]}
                      />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {createOpen && (
        <Sheet
          open
          onOpenChange={(open) => {
            if (!open) setCreateOpen(false);
          }}
        >
          <SheetContent>
            <SheetHeader>
              <SheetTitle>{labels.createCalendar}</SheetTitle>
            </SheetHeader>
            <CalendarForm
              calendar={null}
              labels={labels}
              onSaved={savedCalendar}
              onClose={() => setCreateOpen(false)}
            />
          </SheetContent>
        </Sheet>
      )}
      {editing && (
        <ManageSheet
          key={`${editing.calendar.id}:${editing.section}`}
          calendar={editing.calendar}
          types={types}
          typesError={typesError}
          labels={labels}
          initialSection={editing.section}
          onCalendarSaved={savedCalendar}
          onTypesChanged={() => void refreshTypes(editing.calendar.id)}
          onRetryTypes={() => void refreshTypes(editing.calendar.id)}
          onAttachmentsChanged={() => void reload()}
          onClose={() => {
            typesRequest.current += 1;
            setEditing(null);
          }}
        />
      )}
    </div>
  );
}

/** One contextual view for Employee, Team and Project tabs. */
export function ContextTab({ context }: { context: Record<string, unknown> }) {
  const labels = useLabels();
  const entity = context.entity;
  const objectId = context.objectId;
  const valid =
    (entity === "employee" || entity === "team" || entity === "project") &&
    Number.isInteger(objectId) &&
    Number(objectId) > 0;
  const scope = useMemo(
    () =>
      valid
        ? ({ entity, objectId: Number(objectId) } as CalendarContext)
        : null,
    [entity, objectId, valid],
  );
  const [calendars, setCalendars] = useState<Calendar[]>([]);
  const [enabled, setEnabled] = useState<number[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [loadRevision, setLoadRevision] = useState(0);
  const [range, setRange] = useState<Range | null>(null);
  const [mode, setMode] = useState<"calendar" | "list" | "resources">(
    "calendar",
  );
  const [resourceResult, setResourceResult] = useState<{
    key: string;
    rows: Attachment[];
  } | null>(null);
  const [events, setEvents] = useState<Event[]>([]);
  const [eventsError, setEventsError] = useState(false);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [revision, setRevision] = useState(0);
  const [draft, setDraft] = useState<EventDraft | null>(null);
  const [destinationId, setDestinationId] = useState<number | null>(null);
  const [choosing, setChoosing] = useState<EventDraft | null>(null);
  const [types, setTypes] = useState<EventType[]>([]);
  const [typesError, setTypesError] = useState(false);
  const [typesRevision, setTypesRevision] = useState(0);
  const selected = calendars.find((item) => item.id === destinationId) ?? null;
  const selectedId = selected?.id;
  const draftId = draft?.event?.id;
  const draftStart = draft?.range.start;
  const editable = calendars.filter(
    (item) =>
      enabled.includes(item.id) &&
      item.can_edit &&
      item.calendar_type !== "external",
  );
  const resourceKey = range
    ? `${enabled.join(",")}:${range.start}:${range.end}`
    : "";

  useEffect(() => {
    // Clear stale data before loading a different detail context.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (!scope) {
      setLoading(false);
      setLoadError(true);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setLoadError(false);
    setCalendars([]);
    setEnabled([]);
    setEvents([]);
    setDraft(null);
    setChoosing(null);
    setDestinationId(null);
    listCalendars(controller.signal, scope).then(
      (result) => {
        if (!controller.signal.aborted) {
          setCalendars(result.results);
          setEnabled(result.results.map((item) => item.id));
          setLoading(false);
        }
      },
      () => {
        if (!controller.signal.aborted) {
          setLoadError(true);
          setLoading(false);
        }
      },
    );
    return () => controller.abort();
  }, [scope, loadRevision]);

  useEffect(() => {
    // The previous period must disappear as soon as the visible calendars change.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (!range || !enabled.length) {
      setEvents([]);
      setEventsLoading(false);
      return;
    }
    const controller = new AbortController();
    setEvents([]);
    setEventsLoading(true);
    setEventsError(false);
    Promise.all(
      enabled.map((id) =>
        listEvents(id, range.start, range.end, controller.signal),
      ),
    ).then(
      (results) => {
        if (!controller.signal.aborted) {
          setEvents(results.flatMap((result) => result.results));
          setEventsLoading(false);
        }
      },
      () => {
        if (!controller.signal.aborted) {
          setEventsError(true);
          setEventsLoading(false);
        }
      },
    );
    return () => controller.abort();
  }, [range, enabled, revision]);

  useEffect(() => {
    // Keep the type selector tied to the event currently being edited.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (!selectedId || !draftStart || selected?.calendar_type === "external") {
      setTypes([]);
      return;
    }
    const controller = new AbortController();
    setTypesError(false);
    listTypes(selectedId, controller.signal).then(
      (result) => {
        if (!controller.signal.aborted) setTypes(result.results);
      },
      () => {
        if (!controller.signal.aborted) {
          setTypes([]);
          setTypesError(true);
        }
      },
    );
    return () => controller.abort();
  }, [selectedId, selected?.calendar_type, draftId, draftStart, typesRevision]);

  useEffect(() => {
    if (mode !== "resources" || !range) return;
    const assignments = calendars.filter(
      (item) =>
        enabled.includes(item.id) && item.calendar_type === "assignments",
    );
    if (!assignments.length) return;
    const controller = new AbortController();
    Promise.all(
      assignments.map((item) =>
        listResources(item.id, range.start, range.end, controller.signal),
      ),
    ).then(
      (results) => {
        if (!controller.signal.aborted)
          setResourceResult({
            key: resourceKey,
            rows: [
              ...new Map(
                results
                  .flatMap((result) => result.results)
                  .map((item) => [item.id, item]),
              ).values(),
            ],
          });
      },
      () => {
        if (!controller.signal.aborted)
          setResourceResult({ key: resourceKey, rows: [] });
      },
    );
    return () => controller.abort();
  }, [mode, range, enabled, calendars, resourceKey]);

  const beginCreate = (next: EventDraft) => {
    if (!editable.length) return;
    setDestinationId(null);
    setChoosing(next);
  };
  const openCreate = (info: { start: string; end: string; allDay: boolean }) =>
    beginCreate({
      event: null,
      range: draftRange(info.start, info.end, info.allDay),
      allDay: info.allDay,
    });
  const openEvent = (id: string) => {
    const item = events.find((event) => `${event.calendar}:${event.id}` === id);
    if (item) {
      setDestinationId(item.calendar);
      setDraft({
        event: item,
        range: { start: item.start, end: item.end },
        allDay: item.all_day,
      });
    }
  };
  return (
    <section className="calendars-page calendars-context">
      {loading && <p role="status">{labels.loading}</p>}
      {loadError && (
        <Alert tone="danger">
          {labels.loadError}{" "}
          <Button onClick={() => setLoadRevision((value) => value + 1)}>
            {labels.retry}
          </Button>
        </Alert>
      )}
      {!loading && !loadError && !calendars.length && (
        <p>{labels.contextualEmpty}</p>
      )}
      {!!calendars.length && (
        <>
          {!!editable.length && (
            <div className="calendars-toolbar">
              <Button
                onClick={() => {
                  const start =
                    range?.start ?? new Date().toISOString().slice(0, 10);
                  beginCreate({
                    event: null,
                    range: { start, end: nextDay(start) },
                    allDay: true,
                  });
                }}
              >
                {labels.addEvent}
              </Button>
            </div>
          )}
          <MultipleCalendarChoices
            calendars={calendars}
            enabled={enabled}
            onChange={setEnabled}
            labels={labels}
          />
          {eventsLoading && <p role="status">{labels.loading}</p>}
          {eventsError && (
            <Alert tone="danger">
              {labels.eventsError}{" "}
              <Button onClick={() => setRevision((value) => value + 1)}>
                {labels.retry}
              </Button>
            </Alert>
          )}
          <PluginCalendarView
            title={labels.title}
            events={events
              .filter((item) => enabled.includes(item.calendar))
              .map((item) => {
                const name =
                  calendars.find((calendar) => calendar.id === item.calendar)
                    ?.name ?? "";
                return presentationEvent(
                  item,
                  name,
                  `${item.calendar}:${item.id}`,
                );
              })}
            showCalendarName
            allowResources={calendars.some(
              (item) =>
                enabled.includes(item.id) &&
                item.calendar_type === "assignments",
            )}
            resources={(resourceResult?.key === resourceKey
              ? resourceResult.rows
              : []
            ).map((item) => ({ id: `employee:${item.id}`, title: item.label }))}
            canCreate={editable.length > 0}
            onRangeChange={setRange}
            onModeChange={setMode}
            onDateSelect={openCreate}
            onEventClick={openEvent}
          />
        </>
      )}
      {choosing && (
        <Sheet
          open
          onOpenChange={(open) => {
            if (!open) setChoosing(null);
          }}
        >
          <SheetContent>
            <SheetHeader>
              <SheetTitle>{labels.chooseDestination}</SheetTitle>
            </SheetHeader>
            <div className="calendars-form">
              <label>
                {labels.choose}
                <select
                  value={destinationId ?? ""}
                  onChange={(event) =>
                    setDestinationId(Number(event.target.value) || null)
                  }
                >
                  <option value="">—</option>
                  {editable.map((calendar) => (
                    <option key={calendar.id} value={calendar.id}>
                      {calendar.name}
                    </option>
                  ))}
                </select>
              </label>
              <div className="calendars-actions">
                <Button onClick={() => setChoosing(null)}>
                  {labels.cancel}
                </Button>
                <Button
                  variant="primary"
                  disabled={
                    !destinationId ||
                    !editable.some((calendar) => calendar.id === destinationId)
                  }
                  onClick={() => {
                    setDraft(choosing);
                    setChoosing(null);
                  }}
                >
                  {labels.continue}
                </Button>
              </div>
            </div>
          </SheetContent>
        </Sheet>
      )}
      {typesError && (
        <Alert tone="danger">
          {labels.typesError}{" "}
          <Button onClick={() => setTypesRevision((value) => value + 1)}>
            {labels.retry}
          </Button>
        </Alert>
      )}
      {selected &&
        draft &&
        (selected.calendar_type === "external" && draft.event ? (
          <ExternalEventDetails
            event={draft.event}
            calendar={selected}
            labels={labels}
            onClose={() => setDraft(null)}
          />
        ) : (
          <EventForm
            key={`${selected.id}:${draft.event?.id ?? "new"}:${draft.range.start}`}
            calendar={selected}
            draft={draft}
            types={types}
            labels={labels}
            onSaved={() => {
              setDraft(null);
              setRevision((value) => value + 1);
            }}
            onClose={() => setDraft(null)}
          />
        ))}
    </section>
  );
}
