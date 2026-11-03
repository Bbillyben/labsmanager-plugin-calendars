import { apiRequest } from "@labsmanager/plugin-ui";

export type Role = "view" | "edit" | "manage";
export type Calendar = {
  id: number;
  name: string;
  description: string;
  color: string;
  created_by: number;
  access_mode: "inherited" | "restricted";
  calendar_type: "events" | "assignments" | "external";
  is_active: boolean;
  has_events: boolean;
  role: Role;
  can_edit: boolean;
  can_manage: boolean;
  attachments?: Attachments;
  external?: ExternalSource;
};
export type ExternalSource = {
  has_url: boolean;
  enabled: boolean;
  interval_minutes: number;
  status: string;
  last_attempt: string | null;
  last_success: string | null;
  last_error: string;
  statistics: { created?: number; updated?: number; deactivated?: number };
};
export type CalendarWrite = Pick<
  Calendar,
  "name" | "description" | "color" | "access_mode" | "calendar_type"
> & {
  is_active?: boolean;
  url?: string;
  interval_minutes?: number;
  enabled?: boolean;
};
export type Attachment = { id: number; label: string };
export type Attachments = {
  employees: Attachment[];
  teams: Attachment[];
  projects: Attachment[];
};
export type EventType = {
  id: number;
  calendar: number;
  name: string;
  color: string;
  is_active: boolean;
};
export type Event = {
  id: number | string;
  calendar: number;
  event_type: number | null;
  event_type_name?: string;
  title: string;
  description: string;
  start: string;
  end: string;
  all_day: boolean;
  color: string;
  created_by: number | null;
  employees: Attachment[];
  location?: string;
  read_only?: boolean;
  series_id?: number;
  recurrence_id?: string;
  is_exception?: boolean;
  overrides?: EventOverrides;
  recurrence_rule?: string;
  recurrence_timezone?: string;
  rdates?: string[];
  exdates?: string[];
  exceptions_revision?: number;
};
export type EventOverrides = Record<
  | "title"
  | "description"
  | "location"
  | "event_type"
  | "timeslot"
  | "employees",
  boolean
>;
export type User = { id: number; label: string };
export type Member = { user: User; level: Role };
export type EventWrite = Pick<
  Event,
  | "title"
  | "description"
  | "location"
  | "start"
  | "end"
  | "all_day"
  | "event_type"
> & {
  employees?: number[];
  recurrence_rule?: string;
  recurrence_timezone?: string;
  rdates?: string[];
  exdates?: string[];
};
export type OccurrenceWrite = Partial<
  Pick<
    EventWrite,
    | "title"
    | "description"
    | "location"
    | "event_type"
    | "start"
    | "end"
    | "all_day"
    | "employees"
  >
> & {
  inherit?: (
    | Exclude<keyof EventOverrides, "timeslot">
    | "start"
    | "end"
    | "all_day"
  )[];
};

const root = "/plugin/calendars/api/calendars/";
const json = { "Content-Type": "application/json" };
const path = (id: number) => `${root}${id}/`;
const body = (data: unknown) => JSON.stringify(data);

export type CalendarContext = {
  entity: "employee" | "team" | "project";
  objectId: number;
};
export type CalendarScope = "general" | "managed";
export const listCalendars = (
  signal?: AbortSignal,
  context?: CalendarContext,
  scope?: CalendarScope,
) =>
  apiRequest<{ results: Calendar[] }>(
    context
      ? `${root}?${new URLSearchParams({ [`${context.entity}_id`]: String(context.objectId) })}`
      : scope
        ? `${root}?${new URLSearchParams({ scope })}`
        : root,
    { signal },
  );
export const createCalendar = (data: CalendarWrite) =>
  apiRequest<Calendar>(root, {
    method: "POST",
    headers: json,
    body: body(data),
  });
export const updateCalendar = (id: number, data: Partial<CalendarWrite>) =>
  apiRequest<Calendar>(path(id), {
    method: "PATCH",
    headers: json,
    body: body(data),
  });
export const deleteCalendar = (id: number) =>
  apiRequest<void>(path(id), { method: "DELETE" });
export const syncCalendar = (id: number) =>
  apiRequest<{ status: string }>(`${path(id)}sync/`, { method: "POST" });
export const searchEligibleEmployees = (
  id: number,
  q: string,
  signal: AbortSignal,
) =>
  apiRequest<{ results: Attachment[]; has_more: boolean }>(
    `${path(id)}eligible-employees/?${new URLSearchParams({ q })}`,
    { signal },
  );
export const resolveEligibleEmployee = (
  id: number,
  employeeId: string,
  signal: AbortSignal,
) =>
  apiRequest<{ results: Attachment[] }>(
    `${path(id)}eligible-employees/?${new URLSearchParams({ id: employeeId })}`,
    { signal },
  );
export const listAttachments = (id: number, signal?: AbortSignal) =>
  apiRequest<Attachments>(`${path(id)}attachments/`, { signal });
export const setAttachments = (
  id: number,
  data: Partial<Record<keyof Attachments, number[]>>,
) =>
  apiRequest<Attachments>(`${path(id)}attachments/`, {
    method: "PATCH",
    headers: json,
    body: body(data),
  });
export const listTypes = (id: number, signal?: AbortSignal) =>
  apiRequest<{ results: EventType[] }>(`${path(id)}types/`, { signal });
export const createType = (
  id: number,
  data: Pick<EventType, "name" | "color" | "is_active">,
) =>
  apiRequest<EventType>(`${path(id)}types/`, {
    method: "POST",
    headers: json,
    body: body(data),
  });
export const updateType = (
  id: number,
  typeId: number,
  data: Pick<EventType, "name" | "color" | "is_active">,
) =>
  apiRequest<EventType>(`${path(id)}types/${typeId}/`, {
    method: "PATCH",
    headers: json,
    body: body(data),
  });
export const deleteType = (id: number, typeId: number) =>
  apiRequest<void>(`${path(id)}types/${typeId}/`, { method: "DELETE" });
export const listEvents = (
  id: number,
  start: string,
  end: string,
  signal?: AbortSignal,
) =>
  apiRequest<{ results: Event[] }>(
    `${path(id)}events/?${new URLSearchParams({ start, end })}`,
    { signal },
  );
export const listResources = (
  id: number,
  start: string,
  end: string,
  signal?: AbortSignal,
) =>
  apiRequest<{ results: Attachment[] }>(
    `${path(id)}resources/?${new URLSearchParams({ start, end })}`,
    { signal },
  );
export const createEvent = (id: number, data: EventWrite) =>
  apiRequest<Event>(`${path(id)}events/`, {
    method: "POST",
    headers: json,
    body: body(data),
  });
export const getEvent = (id: number, eventId: number, signal?: AbortSignal) =>
  apiRequest<Event>(`${path(id)}events/${eventId}/`, { signal });
export const updateEvent = (
  id: number,
  eventId: number,
  data: Partial<EventWrite> & {
    reset_exceptions?: boolean;
    exceptions_revision?: number;
  },
) =>
  apiRequest<Event>(`${path(id)}events/${eventId}/`, {
    method: "PATCH",
    headers: json,
    body: body(data),
  });
export const deleteEvent = (id: number, eventId: number) =>
  apiRequest<void>(`${path(id)}events/${eventId}/`, { method: "DELETE" });
const occurrencePath = (id: number, seriesId: number, recurrenceId: string) =>
  `${path(id)}events/${seriesId}/occurrences/${encodeURIComponent(recurrenceId)}/`;
export const updateOccurrence = (
  id: number,
  seriesId: number,
  recurrenceId: string,
  data: OccurrenceWrite,
) =>
  apiRequest<{ series_id: number; recurrence_id: string }>(
    occurrencePath(id, seriesId, recurrenceId),
    {
      method: "PATCH",
      headers: json,
      body: body(data),
    },
  );
export const cancelOccurrence = (
  id: number,
  seriesId: number,
  recurrenceId: string,
) =>
  apiRequest<void>(occurrencePath(id, seriesId, recurrenceId), {
    method: "DELETE",
  });
export const restoreOccurrence = (
  id: number,
  seriesId: number,
  recurrenceId: string,
) =>
  apiRequest<void>(`${occurrencePath(id, seriesId, recurrenceId)}restore/`, {
    method: "POST",
  });
export const listMembers = (id: number, signal?: AbortSignal) =>
  apiRequest<{ owner: User; results: Member[] }>(`${path(id)}members/`, {
    signal,
  });
export const setMember = (id: number, user_id: number, level: Role) =>
  apiRequest<Member>(`${path(id)}members/`, {
    method: "POST",
    headers: json,
    body: body({ user_id, level }),
  });
export const removeMember = (id: number, userId: number) =>
  apiRequest<void>(`${path(id)}members/${userId}/`, { method: "DELETE" });
export const searchUsers = (id: number, q: string, signal: AbortSignal) =>
  apiRequest<{ results: User[]; has_more: boolean }>(
    `${path(id)}users/?${new URLSearchParams({ q })}`,
    { signal },
  );
export const resolveUser = (id: number, userId: string, signal: AbortSignal) =>
  apiRequest<{ results: User[] }>(
    `${path(id)}users/?${new URLSearchParams({ id: userId })}`,
    { signal },
  );
