import type { ReviewHistoryEntryDTO } from "../../../types/api";
import { formatLocalDateTime } from "../../../utils/dateTime";

export function ReviewHistory({ entries, updatedAt }: { entries: ReviewHistoryEntryDTO[]; updatedAt: string | null }) {
  if (!entries.length && !updatedAt) return null;
  return (
    <details className="rounded-lg border border-slate-200 bg-slate-50 p-4">
      <summary className="cursor-pointer text-sm font-medium text-slate-700">审核记录 · {entries.length}</summary>
      <div className="mt-3 space-y-3 text-xs text-slate-500">
        {entries.length ? entries.slice().reverse().map((entry, index) => (
          <div key={`${entry.changed_at ?? entry.updated_at ?? "history"}-${index}`} className="border-l-2 border-slate-200 pl-3">
            <div>{formatLocalDateTime(entry.changed_at ?? entry.updated_at, "未记录时间")}</div>
            <div className="mt-1 text-slate-700">{entry.change_reason ?? "人工审核更新"}</div>
          </div>
        )) : <div>最近更新：{formatLocalDateTime(updatedAt, "未记录时间")}</div>}
      </div>
    </details>
  );
}
