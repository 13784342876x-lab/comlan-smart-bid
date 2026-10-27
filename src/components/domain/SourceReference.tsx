import { FileText, LockKeyhole } from "lucide-react";
import type { SourceReferenceDTO } from "../../types/api";

function pageLabel(value: SourceReferenceDTO) {
  if (value.page_start == null && value.page_end == null) return null;
  if (value.page_start === value.page_end || value.page_end == null) return `P${value.page_start}`;
  return `P${value.page_start}–${value.page_end}`;
}

export function SourceReference({ value, compact = false }: { value: SourceReferenceDTO; compact?: boolean }) {
  const page = pageLabel(value);
  if (compact) {
    return <div className="text-xs leading-5 text-slate-500"><div className="truncate font-medium text-slate-700">{value.file_name ?? "来源文件"}</div><div className="line-clamp-2">{value.clause_path.join(" > ") || "未提供章节路径"}</div><div className="text-slate-400">{[value.table_name, page].filter(Boolean).join(" · ") || "—"}</div></div>;
  }
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex items-start gap-3"><FileText className="mt-0.5 text-brand-500" size={18} /><div className="min-w-0 flex-1"><div className="font-medium text-slate-800">{value.file_name ?? "来源文件"}</div><div className="mt-1 text-sm leading-6 text-slate-500">{value.clause_path.join(" > ") || "未提供章节路径"}</div></div></div>
      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 border-t border-slate-100 pt-3 text-xs">
        <div><dt className="text-slate-400">页码</dt><dd className="mt-1 text-slate-700">{page ?? "—"}</dd></div>
        <div><dt className="text-slate-400">来源类型</dt><dd className="mt-1 text-slate-700">{value.source_kind === "table" ? "表格" : "正文段落"}</dd></div>
        {value.table_name ? <div><dt className="text-slate-400">表格</dt><dd className="mt-1 text-slate-700">{value.table_name}</dd></div> : null}
        {value.table_range || value.row_or_cell ? <div><dt className="text-slate-400">表格 / 行列</dt><dd className="mt-1 text-slate-700">{[value.table_range, value.row_or_cell].filter(Boolean).join(" · ")}</dd></div> : null}
      </dl>
      <button disabled className="mt-4 inline-flex cursor-not-allowed items-center gap-2 text-xs text-slate-400" title="Tender Source Viewer 尚未提供"><LockKeyhole size={13} />查看招标原文（待 Source Viewer）</button>
    </div>
  );
}
