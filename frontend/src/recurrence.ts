/** Form mapping for the backend RRULE subset; occurrence expansion stays in Django. */
export const weekdays = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"] as const;
export type Weekday = (typeof weekdays)[number];
export type Frequency = "DAILY" | "WEEKLY" | "MONTHLY" | "YEARLY";
export type Repeat =
  | "none"
  | "daily"
  | "weekly"
  | "monthly"
  | "yearly"
  | "custom";
export type EndMode = "never" | "until" | "count";
export type MonthlyMode = "date" | "position";
export type RecurrenceFormValue = {
  repeat: Repeat;
  frequency: Frequency;
  interval: number;
  weekdays: Weekday[];
  monthlyMode: MonthlyMode;
  monthDay: number;
  monthPosition: number;
  monthWeekday: Weekday;
  endMode: EndMode;
  until: string;
  count: number;
  timezone: string;
};

const field = (instant: Date, timeZone: string, includeTime = true) => {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    ...(includeTime
      ? {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          hourCycle: "h23" as const,
        }
      : {}),
  }).formatToParts(instant);
  const value = (type: string) =>
    parts.find((part) => part.type === type)?.value ?? "00";
  return `${value("year")}-${value("month")}-${value("day")}${includeTime ? `T${value("hour")}:${value("minute")}:${value("second")}` : ""}`;
};

export const browserTimezone = () =>
  Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

export function localInput(
  value: string,
  allDay: boolean,
  timeZone: string,
): string {
  return allDay
    ? value.slice(0, 10)
    : field(new Date(value), timeZone).slice(0, 16);
}

/** Use the original recurrence start and the series wall-clock duration. */
export function inheritedOccurrenceSlot(
  seriesStart: string,
  seriesEnd: string,
  recurrenceId: string,
  allDay: boolean,
  timeZone: string,
): { start: string; end: string } {
  const first = localInput(seriesStart, allDay, timeZone);
  const last = localInput(seriesEnd, allDay, timeZone);
  const original = allDay
    ? field(new Date(recurrenceId), timeZone, false)
    : localInput(recurrenceId, false, timeZone);
  const suffix = allDay ? "T00:00:00Z" : ":00Z";
  const duration =
    Date.parse(`${last}${suffix}`) - Date.parse(`${first}${suffix}`);
  const end = new Date(Date.parse(`${original}${suffix}`) + duration)
    .toISOString()
    .slice(0, allDay ? 10 : 16);
  return { start: original, end };
}

/** Resolve a wall-clock input in its IANA zone; reject missing DST times. */
export function zonedInput(value: string, timeZone: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(
    value,
  );
  if (!match) throw new Error("Invalid local date-time");
  const target = `${match[1]}-${match[2]}-${match[3]}T${match[4]}:${match[5]}:${match[6] ?? "00"}`;
  const naive = Date.UTC(
    +match[1],
    +match[2] - 1,
    +match[3],
    +match[4],
    +match[5],
    +(match[6] ?? 0),
  );
  let guess = naive;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const local = field(new Date(guess), timeZone);
    const apparent = Date.parse(`${local}Z`);
    guess += naive - apparent;
  }
  const matches: number[] = [];
  for (let offset = -180; offset <= 180; offset += 15) {
    const candidate = guess + offset * 60_000;
    if (field(new Date(candidate), timeZone) === target)
      matches.push(candidate);
  }
  if (!matches.length) throw new Error("Nonexistent local date-time");
  return new Date(Math.min(...matches)).toISOString();
}

export function recurrenceFromRule(
  rule: string,
  timezone: string,
  start: string,
): RecurrenceFormValue {
  const entries = Object.fromEntries(
    rule
      .split(";")
      .filter(Boolean)
      .map((part) => part.split("=", 2)),
  );
  const frequency = (
    ["DAILY", "WEEKLY", "MONTHLY", "YEARLY"].includes(entries.FREQ)
      ? entries.FREQ
      : "WEEKLY"
  ) as Frequency;
  const startDate =
    start.length === 10
      ? start
      : field(new Date(start), timezone || browserTimezone(), false);
  const day =
    weekdays[(new Date(`${startDate}T12:00:00Z`).getUTCDay() + 6) % 7];
  const modifiers = [
    "INTERVAL",
    "BYDAY",
    "BYMONTHDAY",
    "BYSETPOS",
    "BYMONTH",
    "WKST",
  ].some((key) => key in entries);
  const endMode: EndMode = entries.COUNT
    ? "count"
    : entries.UNTIL
      ? "until"
      : "never";
  return {
    repeat: !rule
      ? "none"
      : modifiers
        ? "custom"
        : (frequency.toLowerCase() as Repeat),
    frequency,
    interval: Number(entries.INTERVAL) || 1,
    weekdays: entries.BYDAY
      ? entries.BYDAY.split(",").filter((item: string): item is Weekday =>
          weekdays.includes(item as Weekday),
        )
      : [day],
    monthlyMode: entries.BYSETPOS ? "position" : "date",
    monthDay: Number(entries.BYMONTHDAY) || Number(startDate.slice(8, 10)),
    monthPosition:
      Number(entries.BYSETPOS) || Math.ceil(Number(startDate.slice(8, 10)) / 7),
    monthWeekday: (entries.BYDAY?.split(",")[0] as Weekday) || day,
    endMode,
    until: entries.UNTIL
      ? field(
          new Date(
            entries.UNTIL.replace(
              /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/,
              "$1-$2-$3T$4:$5:$6Z",
            ),
          ),
          timezone,
          false,
        )
      : "",
    count: Number(entries.COUNT) || 1,
    timezone: timezone || browserTimezone(),
  };
}

export function recurrenceWrite(
  form: RecurrenceFormValue,
  start: string,
): {
  recurrence_rule: string;
  recurrence_timezone: string;
  rdates?: string[];
  exdates?: string[];
} {
  if (form.repeat === "none")
    return {
      recurrence_rule: "",
      recurrence_timezone: "",
      rdates: [],
      exdates: [],
    };
  const frequency =
    form.repeat === "custom"
      ? form.frequency
      : (form.repeat.toUpperCase() as Frequency);
  const parts = [`FREQ=${frequency}`];
  if (form.repeat === "custom") {
    if (!Number.isInteger(form.interval) || form.interval < 1)
      throw new Error("interval");
    if (form.interval > 1) parts.push(`INTERVAL=${form.interval}`);
    if (frequency === "WEEKLY") {
      const day =
        weekdays[
          (new Date(`${start.slice(0, 10)}T12:00:00Z`).getUTCDay() + 6) % 7
        ];
      if (!form.weekdays.includes(day)) throw new Error("weekdays");
      parts.push(
        `BYDAY=${weekdays.filter((item) => form.weekdays.includes(item)).join(",")}`,
      );
    }
    if (frequency === "MONTHLY") {
      if (form.monthlyMode === "date")
        parts.push(`BYMONTHDAY=${form.monthDay}`);
      else
        parts.push(
          `BYDAY=${form.monthWeekday}`,
          `BYSETPOS=${form.monthPosition}`,
        );
    }
  }
  if (form.endMode === "count") {
    if (!Number.isInteger(form.count) || form.count < 1)
      throw new Error("count");
    parts.push(`COUNT=${form.count}`);
  }
  if (form.endMode === "until") {
    if (!form.until) throw new Error("until");
    const last = zonedInput(`${form.until}T23:59:59`, form.timezone);
    parts.push(`UNTIL=${last.replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "Z")}`);
  }
  return {
    recurrence_rule: parts.join(";"),
    recurrence_timezone: form.timezone,
  };
}

/** Do not silently drop valid backend RRULE parts that this form cannot edit. */
export function recurrenceRuleEditable(
  rule: string,
  timezone: string,
  start: string,
): boolean {
  if (!rule) return true;
  try {
    const form = recurrenceFromRule(rule, timezone, start);
    const localStart =
      start.length === 10 ? start : localInput(start, false, timezone);
    const generated = recurrenceWrite(
      { ...form, endMode: "never" },
      localStart,
    ).recurrence_rule;
    const parts = (value: string) =>
      value
        .split(";")
        .filter(
          (part) =>
            part && !/^(COUNT|UNTIL)=/.test(part) && part !== "INTERVAL=1",
        )
        .map((part) =>
          part.startsWith("BYDAY=")
            ? `BYDAY=${part.slice(6).split(",").sort().join(",")}`
            : part,
        )
        .sort()
        .join(";");
    return parts(rule) === parts(generated);
  } catch {
    return false;
  }
}
