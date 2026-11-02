import type { RecurrenceFormValue, Weekday } from "./recurrence";
import { weekdays } from "./recurrence";

type Labels = Record<
  | "repeat"
  | "repeatNone"
  | "repeatDaily"
  | "repeatWeekly"
  | "repeatMonthly"
  | "repeatYearly"
  | "repeatCustom"
  | "repeatFrequency"
  | "repeatInterval"
  | "repeatWeekdays"
  | "repeatMonthlyMode"
  | "repeatMonthDate"
  | "repeatMonthPosition"
  | "repeatMonthDay"
  | "repeatPosition"
  | "repeatEnds"
  | "repeatNever"
  | "repeatUntil"
  | "repeatCount"
  | "repeatTimezone"
  | "weekdayMo"
  | "weekdayTu"
  | "weekdayWe"
  | "weekdayTh"
  | "weekdayFr"
  | "weekdaySa"
  | "weekdaySu"
  | "positionFirst"
  | "positionSecond"
  | "positionThird"
  | "positionFourth"
  | "positionLast",
  string
>;

export function RecurrenceFields({
  value,
  onChange,
  labels,
  disabled,
}: {
  value: RecurrenceFormValue;
  onChange: (value: RecurrenceFormValue) => void;
  labels: Labels;
  disabled: boolean;
}) {
  const set = <K extends keyof RecurrenceFormValue>(
    key: K,
    field: RecurrenceFormValue[K],
  ) => onChange({ ...value, [key]: field });
  const weekdayLabels = [
    labels.weekdayMo,
    labels.weekdayTu,
    labels.weekdayWe,
    labels.weekdayTh,
    labels.weekdayFr,
    labels.weekdaySa,
    labels.weekdaySu,
  ];
  return (
    <fieldset className="calendars-recurrence" disabled={disabled}>
      <legend>{labels.repeat}</legend>
      <label>
        {labels.repeat}
        <select
          value={value.repeat}
          onChange={(event) =>
            set("repeat", event.target.value as RecurrenceFormValue["repeat"])
          }
        >
          <option value="none">{labels.repeatNone}</option>
          <option value="daily">{labels.repeatDaily}</option>
          <option value="weekly">{labels.repeatWeekly}</option>
          <option value="monthly">{labels.repeatMonthly}</option>
          <option value="yearly">{labels.repeatYearly}</option>
          <option value="custom">{labels.repeatCustom}</option>
        </select>
      </label>
      {value.repeat !== "none" && (
        <>
          {value.repeat === "custom" && (
            <>
              <label>
                {labels.repeatFrequency}
                <select
                  value={value.frequency}
                  onChange={(event) =>
                    set(
                      "frequency",
                      event.target.value as RecurrenceFormValue["frequency"],
                    )
                  }
                >
                  <option value="DAILY">{labels.repeatDaily}</option>
                  <option value="WEEKLY">{labels.repeatWeekly}</option>
                  <option value="MONTHLY">{labels.repeatMonthly}</option>
                  <option value="YEARLY">{labels.repeatYearly}</option>
                </select>
              </label>
              <label>
                {labels.repeatInterval}
                <input
                  type="number"
                  min={1}
                  step={1}
                  required
                  value={value.interval}
                  onChange={(event) =>
                    set("interval", Number(event.target.value))
                  }
                />
              </label>
              {value.frequency === "WEEKLY" && (
                <fieldset className="calendars-weekdays">
                  <legend>{labels.repeatWeekdays}</legend>
                  {weekdays.map((day, index) => (
                    <label key={day} className="calendars-check">
                      <input
                        type="checkbox"
                        checked={value.weekdays.includes(day)}
                        onChange={(event) =>
                          set(
                            "weekdays",
                            event.target.checked
                              ? [...value.weekdays, day]
                              : value.weekdays.filter((item) => item !== day),
                          )
                        }
                      />
                      {weekdayLabels[index]}
                    </label>
                  ))}
                </fieldset>
              )}
              {value.frequency === "MONTHLY" && (
                <>
                  <label>
                    {labels.repeatMonthlyMode}
                    <select
                      value={value.monthlyMode}
                      onChange={(event) =>
                        set(
                          "monthlyMode",
                          event.target
                            .value as RecurrenceFormValue["monthlyMode"],
                        )
                      }
                    >
                      <option value="date">{labels.repeatMonthDate}</option>
                      <option value="position">
                        {labels.repeatMonthPosition}
                      </option>
                    </select>
                  </label>
                  {value.monthlyMode === "date" ? (
                    <label>
                      {labels.repeatMonthDay}
                      <input
                        type="number"
                        min={1}
                        max={31}
                        step={1}
                        required
                        value={value.monthDay}
                        onChange={(event) =>
                          set("monthDay", Number(event.target.value))
                        }
                      />
                    </label>
                  ) : (
                    <div className="calendars-month-position">
                      <label>
                        {labels.repeatPosition}
                        <select
                          value={value.monthPosition}
                          onChange={(event) =>
                            set("monthPosition", Number(event.target.value))
                          }
                        >
                          <option value={1}>{labels.positionFirst}</option>
                          <option value={2}>{labels.positionSecond}</option>
                          <option value={3}>{labels.positionThird}</option>
                          <option value={4}>{labels.positionFourth}</option>
                          <option value={-1}>{labels.positionLast}</option>
                        </select>
                      </label>
                      <label>
                        {labels.repeatWeekdays}
                        <select
                          value={value.monthWeekday}
                          onChange={(event) =>
                            set("monthWeekday", event.target.value as Weekday)
                          }
                        >
                          {weekdays.map((day, index) => (
                            <option key={day} value={day}>
                              {weekdayLabels[index]}
                            </option>
                          ))}
                        </select>
                      </label>
                    </div>
                  )}
                </>
              )}
            </>
          )}
          <label>
            {labels.repeatEnds}
            <select
              value={value.endMode}
              onChange={(event) =>
                set(
                  "endMode",
                  event.target.value as RecurrenceFormValue["endMode"],
                )
              }
            >
              <option value="never">{labels.repeatNever}</option>
              <option value="until">{labels.repeatUntil}</option>
              <option value="count">{labels.repeatCount}</option>
            </select>
          </label>
          {value.endMode === "until" && (
            <label>
              {labels.repeatUntil}
              <input
                type="date"
                required
                value={value.until}
                onChange={(event) => set("until", event.target.value)}
              />
            </label>
          )}
          {value.endMode === "count" && (
            <label>
              {labels.repeatCount}
              <input
                type="number"
                min={1}
                step={1}
                required
                value={value.count}
                onChange={(event) => set("count", Number(event.target.value))}
              />
            </label>
          )}
          <label>
            {labels.repeatTimezone}
            <input
              required
              value={value.timezone}
              onChange={(event) => set("timezone", event.target.value)}
            />
          </label>
        </>
      )}
    </fieldset>
  );
}
