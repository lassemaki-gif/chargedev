"use client";

export interface DaySlot { available: boolean; start: string; end: string; }
export type WeeklyAvailability = Record<string, DaySlot>;

export const DEFAULT_AVAILABILITY: WeeklyAvailability = {
  mon: { available: true, start: "08:00", end: "22:00" },
  tue: { available: true, start: "08:00", end: "22:00" },
  wed: { available: true, start: "08:00", end: "22:00" },
  thu: { available: true, start: "08:00", end: "22:00" },
  fri: { available: true, start: "08:00", end: "22:00" },
  sat: { available: true, start: "08:00", end: "22:00" },
  sun: { available: true, start: "08:00", end: "22:00" },
};

const DAYS = [
  { key: "mon", label: "Mon" },
  { key: "tue", label: "Tue" },
  { key: "wed", label: "Wed" },
  { key: "thu", label: "Thu" },
  { key: "fri", label: "Fri" },
  { key: "sat", label: "Sat" },
  { key: "sun", label: "Sun" },
];

interface Props {
  value: WeeklyAvailability;
  onChange: (v: WeeklyAvailability) => void;
  readonly?: boolean;
}

export function AvailabilityGrid({ value, onChange, readonly = false }: Props) {
  const set = (day: string, field: keyof DaySlot, val: string | boolean) => {
    onChange({ ...value, [day]: { ...value[day], [field]: val } });
  };

  if (readonly) return (
    <div className="grid grid-cols-7 gap-1 text-center text-xs">
      {DAYS.map(({ key, label }) => {
        const slot = value[key];
        return (
          <div key={key} className={`rounded p-2 ${slot?.available ? "bg-volt/10 text-volt" : "bg-border text-ash"}`}>
            <div className="font-semibold">{label}</div>
            {slot?.available
              ? <div className="mt-0.5 text-[10px]">{slot.start}–{slot.end}</div>
              : <div className="mt-0.5 text-[10px]">Closed</div>
            }
          </div>
        );
      })}
    </div>
  );

  return (
    <div className="space-y-2">
      {DAYS.map(({ key, label }) => {
        const slot = value[key] ?? DEFAULT_AVAILABILITY[key];
        return (
          <div key={key} className="flex items-center gap-3">
            <label className="flex items-center gap-2 w-24 shrink-0 cursor-pointer">
              <input
                type="checkbox"
                checked={slot.available}
                onChange={(e) => set(key, "available", e.target.checked)}
                className="accent-volt w-4 h-4"
              />
              <span className="text-sm font-medium text-white">{label}</span>
            </label>
            {slot.available ? (
              <div className="flex items-center gap-2">
                <input
                  type="time"
                  value={slot.start}
                  onChange={(e) => set(key, "start", e.target.value)}
                  className="input text-sm py-1 px-2 w-28"
                />
                <span className="text-ash text-sm">–</span>
                <input
                  type="time"
                  value={slot.end}
                  onChange={(e) => set(key, "end", e.target.value)}
                  className="input text-sm py-1 px-2 w-28"
                />
              </div>
            ) : (
              <span className="text-ash text-sm">Closed</span>
            )}
          </div>
        );
      })}
    </div>
  );
}
