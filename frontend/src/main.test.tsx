import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import type { Calendar, Event } from "./api";

vi.mock("@labsmanager/plugin-ui", () => ({
  registerPluginTranslations: vi.fn(),
  usePluginTranslation: () => ({
    language: "en",
    t: (key: string) =>
      key === "selectedCalendars" ? "Selected calendars: {count}" : key,
  }),
  Alert: ({ children }: { children: React.ReactNode }) => (
    <div role="alert">{children}</div>
  ),
  Button: ({
    children,
    variant: _variant,
    ...props
  }: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: string }) => {
    void _variant;
    return <button {...props}>{children}</button>;
  },
  ConfirmDialog: ({
    title,
    description,
    onConfirm,
    onCancel,
  }: {
    title: string;
    description: string;
    onConfirm: () => void;
    onCancel: () => void;
  }) => (
    <div role="alertdialog" aria-label={title}>
      <p>{description}</p>
      <button onClick={onCancel}>cancel confirmation</button>
      <button onClick={onConfirm}>confirm action</button>
    </div>
  ),
  EntityActionMenu: ({
    groups,
  }: {
    groups: { id: string; label: string; onSelect: () => void }[][];
  }) => (
    <div>
      {groups.flat().map((action) => (
        <button key={action.id} onClick={action.onSelect}>
          {action.label}
        </button>
      ))}
    </div>
  ),
  EntitySearch: () => null,
  employeeFilterSources: {},
  projectFilterSources: {},
  teamFilterSources: {},
  PluginPublishedCalendarActions: ({ calendarId }: { calendarId: string }) => (
    <span data-testid="subscription">{calendarId}</span>
  ),
  PluginCalendarView: ({
    events,
    onRangeChange,
    onEventClick,
  }: {
    events: {
      id: string;
      title: string;
      color?: string;
      calendarName?: string;
    }[];
    onRangeChange: (range: { start: string; end: string }) => void;
    onEventClick: (id: string) => void;
  }) => (
    <div>
      <button
        onClick={() =>
          onRangeChange({ start: "2026-11-01", end: "2026-11-08" })
        }
      >
        load range
      </button>
      <button
        onClick={() =>
          onRangeChange({ start: "2026-11-08", end: "2026-11-15" })
        }
      >
        next range
      </button>
      {events.map((event) => (
        <button
          key={event.id}
          data-event-id={event.id}
          data-color={event.color}
          data-calendar-name={event.calendarName}
          onClick={() => onEventClick(event.id)}
        >
          {event.title}
        </button>
      ))}
    </div>
  ),
  Sheet: ({ children }: { children: React.ReactNode }) => (
    <div role="dialog">{children}</div>
  ),
  SheetContent: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  SheetHeader: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  SheetTitle: ({ children }: { children: React.ReactNode }) => (
    <h2>{children}</h2>
  ),
  SheetDescription: ({ children }: { children: React.ReactNode }) => (
    <p>{children}</p>
  ),
}));

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api")>()),
  listCalendars: vi.fn(),
  listTypes: vi.fn(),
  listEvents: vi.fn(),
  createCalendar: vi.fn(),
  createEvent: vi.fn(),
  getEvent: vi.fn(),
  updateEvent: vi.fn(),
  updateOccurrence: vi.fn(),
  cancelOccurrence: vi.fn(),
  restoreOccurrence: vi.fn(),
  deleteEvent: vi.fn(),
  updateCalendar: vi.fn(),
  syncCalendar: vi.fn(),
}));

import {
  createCalendar,
  createEvent,
  getEvent,
  updateOccurrence,
  cancelOccurrence,
  restoreOccurrence,
  deleteEvent,
  listCalendars,
  listEvents,
  listTypes,
  syncCalendar,
  updateCalendar,
  updateEvent,
} from "./api";
import { ContextTab, Page, SettingsPanel } from "./main";
import en from "./locales/en.json";
import fr from "./locales/fr.json";

const external: Calendar = {
  id: 7,
  name: "Remote",
  description: "",
  color: "#123456",
  created_by: 1,
  access_mode: "restricted",
  calendar_type: "external",
  has_events: true,
  role: "manage",
  can_edit: false,
  can_manage: true,
  external: {
    has_url: true,
    enabled: true,
    interval_minutes: 60,
    status: "success",
    last_attempt: "2026-11-01T10:00:00Z",
    last_success: "2026-11-01T10:00:00Z",
    last_error: "",
    statistics: { created: 2, updated: 1, deactivated: 0 },
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(listCalendars).mockResolvedValue({ results: [external] });
  vi.mocked(listTypes).mockResolvedValue({ results: [] });
  vi.mocked(listEvents).mockResolvedValue({ results: [] });
  vi.mocked(syncCalendar).mockResolvedValue({ status: "queued" });
});
afterEach(cleanup);

describe("native event location", () => {
  const native = {
    ...external,
    calendar_type: "events" as const,
    can_edit: true,
    external: undefined,
  };
  const existing: Event = {
    id: 12,
    calendar: 7,
    event_type: null,
    title: "Meeting",
    description: "",
    start: "2026-11-01",
    end: "2026-11-02",
    all_day: true,
    color: "#123456",
    created_by: 1,
    employees: [],
    location: "Room A",
  };

  it("creates events with and without a location", async () => {
    vi.mocked(listCalendars).mockResolvedValue({ results: [native] });
    vi.mocked(createEvent).mockResolvedValue(existing);
    render(<Page context={{}} />);
    fireEvent.click(await screen.findByRole("button", { name: "addEvent" }));
    fireEvent.change(screen.getByLabelText("titleField"), {
      target: { value: "Meeting" },
    });
    fireEvent.change(screen.getByLabelText("location"), {
      target: { value: "Room A" },
    });
    fireEvent.click(screen.getByRole("button", { name: "save" }));
    await waitFor(() =>
      expect(createEvent).toHaveBeenCalledWith(
        7,
        expect.objectContaining({ location: "Room A" }),
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "addEvent" }));
    fireEvent.change(screen.getByLabelText("titleField"), {
      target: { value: "No room" },
    });
    fireEvent.click(screen.getByRole("button", { name: "save" }));
    await waitFor(() =>
      expect(createEvent).toHaveBeenLastCalledWith(
        7,
        expect.objectContaining({ location: "" }),
      ),
    );
  });

  it("shows the saved value and can clear it for assignments", async () => {
    vi.mocked(listCalendars).mockResolvedValue({
      results: [{ ...native, calendar_type: "assignments" }],
    });
    vi.mocked(listEvents).mockResolvedValue({ results: [existing] });
    vi.mocked(updateEvent).mockResolvedValue({ ...existing, location: "" });
    render(<Page context={{}} />);
    fireEvent.click(await screen.findByRole("button", { name: "load range" }));
    fireEvent.click(await screen.findByRole("button", { name: "Meeting" }));
    const location = screen.getByLabelText("location") as HTMLInputElement;
    expect(location.value).toBe("Room A");
    fireEvent.change(location, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "save" }));
    await waitFor(() =>
      expect(updateEvent).toHaveBeenCalledWith(
        7,
        12,
        expect.objectContaining({ location: "", employees: [] }),
      ),
    );
  });

  it("omits an empty location from read-only native details", async () => {
    vi.mocked(listCalendars).mockResolvedValue({
      results: [{ ...native, can_edit: false }],
    });
    vi.mocked(listEvents).mockResolvedValue({
      results: [{ ...existing, location: "" }],
    });
    render(<Page context={{}} />);
    fireEvent.click(await screen.findByRole("button", { name: "load range" }));
    fireEvent.click(await screen.findByRole("button", { name: "Meeting" }));
    expect(screen.queryByLabelText("location")).toBeNull();
  });
});

describe("native recurrence form", () => {
  const calendar: Calendar = {
    ...external,
    calendar_type: "assignments",
    can_edit: true,
    external: undefined,
  };
  const series: Event = {
    id: 20,
    calendar: 7,
    event_type: null,
    title: "Weekly meeting",
    description: "Agenda",
    start: "2026-11-02T09:00:00Z",
    end: "2026-11-02T10:00:00Z",
    all_day: false,
    color: "#123456",
    created_by: 1,
    employees: [],
    location: "Room A",
    recurrence_rule: "FREQ=WEEKLY;COUNT=5",
    recurrence_timezone: "Europe/Paris",
    rdates: [],
    exdates: [],
    exceptions_revision: 1,
  };
  const occurrence: Event = {
    ...series,
    id: "20:2026-11-02T09:00:00+00:00",
    series_id: 20,
    recurrence_id: "2026-11-02T09:00:00+00:00",
    is_exception: true,
    overrides: {
      title: false,
      description: false,
      location: true,
      event_type: false,
      timeslot: false,
      employees: false,
    },
    location: "Room B",
  };
  const open = async (item: Event = occurrence) => {
    vi.mocked(listCalendars).mockResolvedValue({ results: [calendar] });
    vi.mocked(listEvents).mockResolvedValue({ results: [item] });
    vi.mocked(getEvent).mockResolvedValue(series);
    render(<Page context={{}} />);
    fireEvent.click(await screen.findByRole("button", { name: "load range" }));
    fireEvent.click(await screen.findByRole("button", { name: item.title }));
    await waitFor(() =>
      expect(getEvent).toHaveBeenCalledWith(7, 20, expect.anything()),
    );
    await waitFor(() =>
      expect(screen.getByLabelText("editScope")).not.toHaveProperty(
        "disabled",
        true,
      ),
    );
  };

  it("creates a daily series with an explicit timezone and count", async () => {
    vi.mocked(listCalendars).mockResolvedValue({ results: [calendar] });
    vi.mocked(createEvent).mockResolvedValue(series);
    render(<Page context={{}} />);
    fireEvent.click(await screen.findByRole("button", { name: "addEvent" }));
    fireEvent.change(screen.getByLabelText("titleField"), {
      target: { value: "Daily meeting" },
    });
    fireEvent.change(screen.getByLabelText("repeat"), {
      target: { value: "daily" },
    });
    fireEvent.change(screen.getByLabelText("repeatTimezone"), {
      target: { value: "Europe/Paris" },
    });
    fireEvent.change(screen.getByLabelText("repeatEnds"), {
      target: { value: "count" },
    });
    fireEvent.change(screen.getByLabelText("repeatCount"), {
      target: { value: "4" },
    });
    fireEvent.click(screen.getByRole("button", { name: "save" }));
    await waitFor(() =>
      expect(createEvent).toHaveBeenCalledWith(
        7,
        expect.objectContaining({
          recurrence_rule: "FREQ=DAILY;COUNT=4",
          recurrence_timezone: "Europe/Paris",
        }),
      ),
    );
  });

  it("sends only the changed occurrence field and can explicitly inherit a field", async () => {
    await open();
    fireEvent.change(screen.getByLabelText("titleField"), {
      target: { value: "Personal title" },
    });
    fireEvent.click(screen.getByRole("button", { name: "save" }));
    await waitFor(() =>
      expect(updateOccurrence).toHaveBeenCalledWith(
        7,
        20,
        occurrence.recurrence_id,
        { title: "Personal title" },
      ),
    );
    cleanup();
    vi.clearAllMocks();
    await open();
    fireEvent.click(screen.getAllByRole("button", { name: "restoreField" })[0]);
    fireEvent.click(screen.getByRole("button", { name: "save" }));
    await waitFor(() =>
      expect(updateOccurrence).toHaveBeenLastCalledWith(
        7,
        20,
        occurrence.recurrence_id,
        { inherit: ["location"] },
      ),
    );
  });

  it("edits series details and confirms a structural reset with its revision", async () => {
    await open();
    fireEvent.change(screen.getByLabelText("editScope"), {
      target: { value: "series" },
    });
    fireEvent.change(screen.getByLabelText("titleField"), {
      target: { value: "New title" },
    });
    fireEvent.click(screen.getByRole("button", { name: "save" }));
    await waitFor(() =>
      expect(updateEvent).toHaveBeenCalledWith(7, 20, { title: "New title" }),
    );
    cleanup();
    vi.clearAllMocks();
    vi.mocked(updateEvent).mockRejectedValueOnce({
      status: 409,
      payload: { code: "exceptions_reset_required", exceptions_revision: 4 },
    });
    vi.mocked(updateEvent).mockResolvedValue(series);
    await open();
    fireEvent.change(screen.getByLabelText("editScope"), {
      target: { value: "series" },
    });
    fireEvent.change(screen.getByLabelText("repeat"), {
      target: { value: "daily" },
    });
    fireEvent.click(screen.getByRole("button", { name: "save" }));
    expect(
      await screen.findByRole("alertdialog", { name: "resetExceptions" }),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "confirm action" }));
    await waitFor(() =>
      expect(updateEvent).toHaveBeenLastCalledWith(
        7,
        20,
        expect.objectContaining({
          recurrence_rule: "FREQ=DAILY;COUNT=5",
          reset_exceptions: true,
          exceptions_revision: 4,
        }),
      ),
    );
  });

  it("uses distinct confirmed cancellation and restoration endpoints", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: "cancelOccurrence" }));
    expect(cancelOccurrence).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "confirm action" }));
    await waitFor(() =>
      expect(cancelOccurrence).toHaveBeenCalledWith(
        7,
        20,
        occurrence.recurrence_id,
      ),
    );
    cleanup();
    vi.clearAllMocks();
    await open();
    fireEvent.click(screen.getByRole("button", { name: "restoreOccurrence" }));
    fireEvent.click(screen.getByRole("button", { name: "confirm action" }));
    await waitFor(() =>
      expect(restoreOccurrence).toHaveBeenCalledWith(
        7,
        20,
        occurrence.recurrence_id,
      ),
    );
    expect(deleteEvent).not.toHaveBeenCalled();
  });

  it("sends an explicitly empty assignment override and a changed empty text", async () => {
    await open({
      ...occurrence,
      is_exception: false,
      location: "Room A",
      overrides: { ...occurrence.overrides!, location: false },
    });
    fireEvent.click(screen.getByRole("button", { name: "emptyAssignments" }));
    fireEvent.change(screen.getByLabelText("description"), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByRole("button", { name: "save" }));
    await waitFor(() =>
      expect(updateOccurrence).toHaveBeenCalledWith(
        7,
        20,
        occurrence.recurrence_id,
        { description: "", employees: [] },
      ),
    );
  });

  it("restores the selected occurrence's original time slot", async () => {
    const moved = {
      ...occurrence,
      start: "2026-11-03T09:00:00Z",
      end: "2026-11-03T10:00:00Z",
      overrides: { ...occurrence.overrides!, timeslot: true },
    };
    await open(moved);
    fireEvent.click(screen.getAllByRole("button", { name: "restoreField" })[1]);
    expect(screen.getByLabelText("start")).toHaveProperty(
      "value",
      "2026-11-02T10:00",
    );
    fireEvent.click(screen.getByRole("button", { name: "save" }));
    await waitFor(() =>
      expect(updateOccurrence).toHaveBeenCalledWith(
        7,
        20,
        occurrence.recurrence_id,
        { inherit: ["start", "end", "all_day"] },
      ),
    );
  });

  it("keeps the sheet open after a stale revision rejects reset", async () => {
    vi.mocked(updateEvent).mockRejectedValueOnce({
      status: 409,
      payload: { code: "exceptions_reset_required", exceptions_revision: 4 },
    });
    vi.mocked(updateEvent).mockRejectedValueOnce({
      status: 409,
      payload: { code: "exceptions_revision_changed", exceptions_revision: 5 },
    });
    await open();
    fireEvent.change(screen.getByLabelText("editScope"), {
      target: { value: "series" },
    });
    fireEvent.change(screen.getByLabelText("repeat"), {
      target: { value: "daily" },
    });
    fireEvent.click(screen.getByRole("button", { name: "save" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "confirm action" }),
    );
    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "revisionConflict",
    );
    expect(screen.getByRole("button", { name: "save" })).toBeTruthy();
    expect(updateEvent).toHaveBeenCalledTimes(2);
  });

  it("deletes the series only after a distinct confirmation", async () => {
    await open();
    fireEvent.change(screen.getByLabelText("editScope"), {
      target: { value: "series" },
    });
    fireEvent.click(screen.getByRole("button", { name: "deleteSeries" }));
    expect(deleteEvent).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "confirm action" }));
    await waitFor(() => expect(deleteEvent).toHaveBeenCalledWith(7, 20));
    expect(cancelOccurrence).not.toHaveBeenCalled();
  });

  it("does not offer recurrence mutations to a read-only viewer", async () => {
    vi.mocked(listCalendars).mockResolvedValue({
      results: [{ ...calendar, can_edit: false }],
    });
    vi.mocked(listEvents).mockResolvedValue({ results: [occurrence] });
    vi.mocked(getEvent).mockResolvedValue(series);
    render(<Page context={{}} />);
    fireEvent.click(await screen.findByRole("button", { name: "load range" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Weekly meeting" }),
    );
    expect(screen.queryByRole("button", { name: "save" })).toBeNull();
    expect(
      screen.queryByRole("button", { name: "cancelOccurrence" }),
    ).toBeNull();
  });
});

describe("multiple calendar selection", () => {
  const first: Calendar = {
    ...external,
    id: 1,
    name: "First",
    color: "#111111",
    calendar_type: "events",
    can_edit: true,
    external: undefined,
  };
  const second: Calendar = {
    ...first,
    id: 2,
    name: "Second",
    color: "#222222",
    calendar_type: "assignments",
  };
  const remote: Calendar = { ...external, id: 3, name: "Remote" };
  const eventFor = (calendar: Calendar): Event => ({
    id: 10,
    calendar: calendar.id,
    event_type: null,
    title: `${calendar.name} event`,
    description: `${calendar.name} details`,
    start: "2026-11-01",
    end: "2026-11-02",
    all_day: true,
    color: calendar.color,
    created_by: calendar.calendar_type === "external" ? null : 1,
    employees: [],
    read_only: calendar.calendar_type === "external",
  });

  it("keeps simple selection and loads one calendar", async () => {
    vi.mocked(listCalendars).mockResolvedValue({ results: [first, second] });
    render(<Page context={{}} />);
    expect(
      await screen.findByRole("option", { name: "selectMultiple" }),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "load range" }));
    await waitFor(() => expect(listEvents).toHaveBeenCalledTimes(1));
    expect(listEvents).toHaveBeenCalledWith(
      1,
      "2026-11-01",
      "2026-11-08",
      expect.anything(),
    );
  });

  it("combines distinct events, retains colors and selection across periods", async () => {
    vi.mocked(listCalendars).mockResolvedValue({
      results: [first, second, remote],
    });
    vi.mocked(listEvents).mockImplementation(async (id) => ({
      results: [
        eventFor([first, second, remote].find((item) => item.id === id)!),
      ],
    }));
    render(<Page context={{}} />);
    fireEvent.change(await screen.findByLabelText("choose"), {
      target: { value: "multiple" },
    });
    expect(screen.getByText("Selected calendars: 1")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "selectAll" }));
    expect(screen.getByText("Selected calendars: 3")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "load range" }));
    const firstButton = await screen.findByRole("button", {
      name: "First event",
    });
    const secondButton = screen.getByRole("button", { name: "Second event" });
    expect(firstButton.getAttribute("data-event-id")).toBe("1:10");
    expect(secondButton.getAttribute("data-event-id")).toBe("2:10");
    expect(secondButton.getAttribute("data-calendar-name")).toBe("Second");
    expect(secondButton.getAttribute("data-color")).toBe("#222222");
    fireEvent.click(screen.getByLabelText("Second"));
    expect(screen.getByText("Selected calendars: 2")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Second event" })).toBeNull();
    fireEvent.click(screen.getByLabelText("Second"));
    fireEvent.click(
      await screen.findByRole("button", { name: "Remote event" }),
    );
    expect(screen.queryByRole("button", { name: "save" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "next range" }));
    await waitFor(() =>
      expect(listEvents).toHaveBeenCalledWith(
        3,
        "2026-11-08",
        "2026-11-15",
        expect.anything(),
      ),
    );
    expect(screen.getByLabelText("Remote") as HTMLInputElement).toHaveProperty(
      "checked",
      true,
    );
    fireEvent.click(screen.getByRole("button", { name: "deselectAll" }));
    expect(screen.queryByRole("button", { name: "First event" })).toBeNull();
    expect(screen.getByText("Selected calendars: 0")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "singleCalendar" }));
    expect(screen.getByLabelText("choose")).toBeTruthy();
  });

  it("requires an explicit editable destination for creation", async () => {
    vi.mocked(listCalendars).mockResolvedValue({
      results: [first, second, remote],
    });
    vi.mocked(createEvent).mockResolvedValue(eventFor(second));
    render(<Page context={{}} />);
    fireEvent.change(await screen.findByLabelText("choose"), {
      target: { value: "multiple" },
    });
    fireEvent.click(screen.getByRole("button", { name: "selectAll" }));
    fireEvent.click(screen.getByRole("button", { name: "addEvent" }));
    expect(screen.getByRole("button", { name: "continue" })).toHaveProperty(
      "disabled",
      true,
    );
    expect(screen.queryByRole("option", { name: "Remote" })).toBeNull();
    fireEvent.change(screen.getByLabelText("choose"), {
      target: { value: "2" },
    });
    fireEvent.click(screen.getByRole("button", { name: "continue" }));
    fireEvent.change(screen.getByLabelText("titleField"), {
      target: { value: "New event" },
    });
    fireEvent.click(screen.getByRole("button", { name: "save" }));
    await waitFor(() =>
      expect(createEvent).toHaveBeenCalledWith(
        2,
        expect.objectContaining({ title: "New event", employees: [] }),
      ),
    );
  });

  it.each(["employee", "team", "project"])(
    "keeps the %s context scoped and combined",
    async (entity) => {
      vi.mocked(listCalendars).mockResolvedValue({ results: [first, remote] });
      vi.mocked(listEvents).mockImplementation(async (id) => ({
        results: [eventFor(id === 1 ? first : remote)],
      }));
      render(<ContextTab context={{ entity, objectId: 4 }} />);
      expect(await screen.findByText("Selected calendars: 2")).toBeTruthy();
      expect(listCalendars).toHaveBeenCalledWith(expect.anything(), {
        entity,
        objectId: 4,
      });
      fireEvent.click(screen.getByRole("button", { name: "load range" }));
      expect(
        await screen.findByRole("button", { name: "First event" }),
      ).toBeTruthy();
      expect(screen.getByRole("button", { name: "Remote event" })).toBeTruthy();
    },
  );

  it("hides multiple mode when only one calendar is visible and handles none", async () => {
    vi.mocked(listCalendars).mockResolvedValue({ results: [first] });
    render(<Page context={{}} />);
    await screen.findByRole("option", { name: "First" });
    expect(screen.queryByRole("option", { name: "selectMultiple" })).toBeNull();
    cleanup();
    vi.mocked(listCalendars).mockResolvedValue({ results: [] });
    render(<Page context={{}} />);
    expect(await screen.findByText("empty")).toBeTruthy();
  });

  it("does not offer creation when selected calendars are read-only", async () => {
    vi.mocked(listCalendars).mockResolvedValue({
      results: [{ ...first, can_edit: false }, remote],
    });
    render(<Page context={{}} />);
    fireEvent.change(await screen.findByLabelText("choose"), {
      target: { value: "multiple" },
    });
    fireEvent.click(screen.getByRole("button", { name: "selectAll" }));
    expect(screen.queryByRole("button", { name: "addEvent" })).toBeNull();
  });
});

describe("external calendar UI", () => {
  it("has matching French and English translation keys", () => {
    expect(Object.keys(fr).sort()).toEqual(Object.keys(en).sort());
  });

  it("creates an external calendar with its private URL and interval", async () => {
    vi.mocked(listCalendars).mockResolvedValue({ results: [] });
    vi.mocked(createCalendar).mockResolvedValue(external);
    render(<SettingsPanel context={{}} />);
    fireEvent.click(
      await screen.findByRole("button", { name: "createCalendar" }),
    );
    fireEvent.change(screen.getByLabelText("name"), {
      target: { value: "Remote" },
    });
    fireEvent.change(screen.getByLabelText("calendarType"), {
      target: { value: "external" },
    });
    fireEvent.change(screen.getByLabelText("externalUrl"), {
      target: { value: "https://example.org/private.ics" },
    });
    fireEvent.change(screen.getByLabelText("syncInterval"), {
      target: { value: "90" },
    });
    fireEvent.click(screen.getByRole("button", { name: "save" }));
    await waitFor(() =>
      expect(createCalendar).toHaveBeenCalledWith(
        expect.objectContaining({
          calendar_type: "external",
          url: "https://example.org/private.ics",
          interval_minutes: 90,
        }),
      ),
    );
  });

  it("keeps native calendar creation unchanged", async () => {
    vi.mocked(listCalendars).mockResolvedValue({ results: [] });
    vi.mocked(createCalendar).mockResolvedValue({
      ...external,
      calendar_type: "events",
      external: undefined,
    });
    render(<SettingsPanel context={{}} />);
    fireEvent.click(
      await screen.findByRole("button", { name: "createCalendar" }),
    );
    fireEvent.change(screen.getByLabelText("name"), {
      target: { value: "Native" },
    });
    expect(screen.queryByLabelText("externalUrl")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "save" }));
    await waitFor(() =>
      expect(createCalendar).toHaveBeenCalledWith(
        expect.objectContaining({ calendar_type: "events" }),
      ),
    );
    expect(vi.mocked(createCalendar).mock.calls[0][0]).not.toHaveProperty(
      "url",
    );
  });

  it("keeps the saved URL hidden and sends a replacement only when entered", async () => {
    vi.mocked(updateCalendar).mockResolvedValue(external);
    render(<SettingsPanel context={{}} />);
    fireEvent.click(await screen.findByRole("button", { name: "settings" }));
    const url = screen.getByLabelText("externalUrl") as HTMLInputElement;
    expect(url.value).toBe("");
    expect(url.placeholder).toBe("externalUrlConfigured");
    expect(document.body.textContent).not.toContain(
      "https://example.org/private.ics",
    );
    fireEvent.click(screen.getByRole("button", { name: "save" }));
    await waitFor(() =>
      expect(updateCalendar).toHaveBeenCalledWith(
        7,
        expect.not.objectContaining({ url: expect.anything() }),
      ),
    );
    fireEvent.click(await screen.findByRole("button", { name: "settings" }));
    fireEvent.change(screen.getByLabelText("externalUrl"), {
      target: { value: "https://example.org/new.ics" },
    });
    fireEvent.click(screen.getByRole("button", { name: "save" }));
    await waitFor(() =>
      expect(updateCalendar).toHaveBeenLastCalledWith(
        7,
        expect.objectContaining({ url: "https://example.org/new.ics" }),
      ),
    );
  });

  it("shows status and queues synchronization once for a manager", async () => {
    const queued = {
      ...external,
      external: { ...external.external!, status: "queued" },
    };
    vi.mocked(listCalendars)
      .mockResolvedValueOnce({ results: [external] })
      .mockResolvedValue({ results: [queued] });
    render(<SettingsPanel context={{}} />);
    fireEvent.click(await screen.findByRole("button", { name: "settings" }));
    expect(screen.getByText("syncStatistics:", { exact: false })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "syncNow" }));
    await waitFor(() => expect(syncCalendar).toHaveBeenCalledWith(7));
    await waitFor(() =>
      expect(
        screen
          .getByRole("button", { name: "syncNow" })
          .hasAttribute("disabled"),
      ).toBe(true),
    );
    fireEvent.click(screen.getByRole("button", { name: "syncNow" }));
    expect(syncCalendar).toHaveBeenCalledTimes(1);
    vi.mocked(listCalendars).mockResolvedValue({ results: [external] });
    fireEvent.click(screen.getByRole("button", { name: "refreshStatus" }));
    await screen.findByText("syncState: syncSuccess");
  });

  it("shows redacted source and queue errors without closing management", async () => {
    vi.mocked(listCalendars).mockResolvedValue({
      results: [
        {
          ...external,
          external: {
            ...external.external!,
            status: "error",
            last_error: "The subscription host is unavailable.",
          },
        },
      ],
    });
    vi.mocked(syncCalendar).mockRejectedValue(new Error("network"));
    render(<SettingsPanel context={{}} />);
    fireEvent.click(await screen.findByRole("button", { name: "settings" }));
    expect(
      screen.getByText("The subscription host is unavailable."),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "syncNow" }));
    await screen.findByText("syncError");
    expect(screen.getByRole("button", { name: "syncNow" })).toBeTruthy();
  });

  it("does not offer management to a user without manage access", async () => {
    vi.mocked(listCalendars).mockResolvedValue({
      results: [{ ...external, can_manage: false, role: "view" }],
    });
    render(<SettingsPanel context={{}} />);
    await screen.findByText("Remote");
    expect(screen.queryByRole("button", { name: "settings" })).toBeNull();
    expect(screen.queryByRole("button", { name: "syncNow" })).toBeNull();
  });

  it("renders external events as read-only and keeps the shared subscription action", async () => {
    const event: Event = {
      id: "42:2026-11-01T09:00:00+00:00",
      calendar: 7,
      event_type: null,
      title: "Remote meeting",
      description: "Read-only details",
      start: "2026-11-01T09:00:00Z",
      end: "2026-11-01T10:00:00Z",
      all_day: false,
      color: "#123456",
      created_by: null,
      employees: [],
      location: "Room A",
      read_only: true,
    };
    vi.mocked(listEvents).mockResolvedValue({ results: [event] });
    render(<Page context={{}} />);
    await screen.findByTestId("subscription");
    fireEvent.click(screen.getByRole("button", { name: "load range" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Remote meeting" }),
    );
    expect(screen.getByText("Read-only details")).toBeTruthy();
    expect(screen.getByText("Room A")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "save" })).toBeNull();
    expect(screen.queryByRole("button", { name: "delete" })).toBeNull();
    expect(listTypes).not.toHaveBeenCalled();
  });

  it("shows the same read-only event in an attached Employee calendar", async () => {
    vi.mocked(listEvents).mockResolvedValue({
      results: [
        {
          id: "42:occurrence",
          calendar: 7,
          event_type: null,
          title: "Attached event",
          description: "From feed",
          start: "2026-11-01",
          end: "2026-11-02",
          all_day: true,
          color: "#123456",
          created_by: null,
          employees: [],
          read_only: true,
        },
      ],
    });
    render(<ContextTab context={{ entity: "employee", objectId: 3 }} />);
    await screen.findByTestId("subscription");
    fireEvent.click(screen.getByRole("button", { name: "load range" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Attached event" }),
    );
    expect(screen.getByText("From feed")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "delete" })).toBeNull();
    expect(listTypes).not.toHaveBeenCalled();
  });
});
