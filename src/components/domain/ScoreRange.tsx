import type { ScoreRangeDTO } from "../../types/api";

export function ScoreRange({ value, suffix = "分" }: { value: ScoreRangeDTO; suffix?: string }) {
  const same = value.minimum === value.maximum;
  return <span className="font-semibold tabular-nums text-slate-900">{same ? value.minimum : `${value.minimum}–${value.maximum}`}{suffix}</span>;
}
