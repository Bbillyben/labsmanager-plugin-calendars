import { describe, expect, it } from "vitest";
import {
  inheritedOccurrenceSlot,
  localInput,
  recurrenceFromRule,
  recurrenceRuleEditable,
  recurrenceWrite,
  zonedInput,
} from "./recurrence";

const base = recurrenceFromRule("", "Europe/Paris", "2026-11-02T09:00:00Z");

describe("native recurrence form contract", () => {
  it("keeps point events without a recurrence", () => {
    expect(recurrenceWrite(base, "2026-11-02T10:00")).toEqual({
      recurrence_rule: "",
      recurrence_timezone: "",
      rdates: [],
      exdates: [],
    });
  });

  it.each([
    ["daily", "FREQ=DAILY"],
    ["weekly", "FREQ=WEEKLY"],
    ["monthly", "FREQ=MONTHLY"],
    ["yearly", "FREQ=YEARLY"],
  ] as const)("writes %s repetition", (repeat, rule) => {
    expect(
      recurrenceWrite({ ...base, repeat }, "2026-11-02T10:00").recurrence_rule,
    ).toBe(rule);
  });

  it("writes interval, weekdays, monthly date and position without expanding occurrences", () => {
    expect(
      recurrenceWrite(
        {
          ...base,
          repeat: "custom",
          frequency: "WEEKLY",
          interval: 2,
          weekdays: ["MO", "WE"],
        },
        "2026-11-02T10:00",
      ).recurrence_rule,
    ).toBe("FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE");
    expect(
      recurrenceWrite(
        { ...base, repeat: "custom", frequency: "MONTHLY", monthDay: 2 },
        "2026-11-02T10:00",
      ).recurrence_rule,
    ).toBe("FREQ=MONTHLY;BYMONTHDAY=2");
    expect(
      recurrenceWrite(
        {
          ...base,
          repeat: "custom",
          frequency: "MONTHLY",
          monthlyMode: "position",
          monthPosition: -1,
          monthWeekday: "FR",
        },
        "2026-11-27T10:00",
      ).recurrence_rule,
    ).toBe("FREQ=MONTHLY;BYDAY=FR;BYSETPOS=-1");
  });

  it("writes count and the end of a local day in UTC", () => {
    expect(
      recurrenceWrite(
        { ...base, repeat: "daily", endMode: "count", count: 3 },
        "2026-11-02T10:00",
      ).recurrence_rule,
    ).toBe("FREQ=DAILY;COUNT=3");
    expect(
      recurrenceWrite(
        { ...base, repeat: "daily", endMode: "until", until: "2026-11-03" },
        "2026-11-02T10:00",
      ).recurrence_rule,
    ).toBe("FREQ=DAILY;UNTIL=20261103T225959Z");
  });

  it("rejects missing DST times and keeps an exact all-day date", () => {
    expect(() => zonedInput("2026-03-29T02:30", "Europe/Paris")).toThrow();
    expect(zonedInput("2026-10-25T02:30", "Europe/Paris")).toBe(
      "2026-10-25T00:30:00.000Z",
    );
    expect(localInput("2026-10-25", true, "America/New_York")).toBe(
      "2026-10-25",
    );
  });

  it("preserves editable rule parameters read from Django", () => {
    const form = recurrenceFromRule(
      "FREQ=MONTHLY;BYDAY=FR;BYSETPOS=-1;COUNT=4",
      "Europe/Paris",
      "2026-11-27T09:00:00Z",
    );
    expect(form).toMatchObject({
      repeat: "custom",
      frequency: "MONTHLY",
      monthlyMode: "position",
      monthWeekday: "FR",
      monthPosition: -1,
      endMode: "count",
      count: 4,
    });
  });

  it("recognizes backend modifiers the form cannot safely rewrite", () => {
    expect(
      recurrenceRuleEditable(
        "FREQ=WEEKLY;BYDAY=MO,WE;COUNT=4",
        "Europe/Paris",
        "2026-11-02T09:00:00Z",
      ),
    ).toBe(true);
    expect(
      recurrenceRuleEditable(
        "FREQ=WEEKLY;BYDAY=WE,MO;COUNT=4",
        "Europe/Paris",
        "2026-11-02T09:00:00Z",
      ),
    ).toBe(true);
    expect(
      recurrenceRuleEditable(
        "FREQ=YEARLY;BYMONTH=11;COUNT=4",
        "Europe/Paris",
        "2026-11-02T09:00:00Z",
      ),
    ).toBe(false);
    expect(
      recurrenceRuleEditable(
        "FREQ=WEEKLY;WKST=SU;COUNT=4",
        "Europe/Paris",
        "2026-11-02T09:00:00Z",
      ),
    ).toBe(false);
    expect(
      recurrenceRuleEditable(
        "FREQ=WEEKLY;BYDAY=MO",
        "Asia/Tokyo",
        "2026-11-01T15:00:00Z",
      ),
    ).toBe(true);
  });

  it("restores the selected occurrence's original slot rather than the first slot", () => {
    expect(
      inheritedOccurrenceSlot(
        "2026-10-19T08:00:00Z",
        "2026-10-19T09:00:00Z",
        "2026-10-26T09:00:00Z",
        false,
        "Europe/Paris",
      ),
    ).toEqual({ start: "2026-10-26T10:00", end: "2026-10-26T11:00" });
    expect(
      inheritedOccurrenceSlot(
        "2026-10-18",
        "2026-10-20",
        "2026-10-24T15:00:00Z",
        true,
        "Asia/Tokyo",
      ),
    ).toEqual({ start: "2026-10-25", end: "2026-10-27" });
  });
});
