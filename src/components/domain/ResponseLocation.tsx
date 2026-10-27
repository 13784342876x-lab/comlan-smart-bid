import { ArrowUpRight, MapPin } from "lucide-react";
import { Link } from "react-router-dom";
import type { ReviewLocationDTO } from "../../types/api";

export function ResponseLocation({ value, projectId, label, trace }: { value: ReviewLocationDTO; projectId: string; label?: string; trace?: { requirement?: string; finding?: string; reviewItem?: string; scoreItem?: string } }) {
  const section = value.section_id;
  const params = new URLSearchParams(); if (section) params.set("section", section); if (value.paragraph_id) params.set("paragraph", value.paragraph_id); Object.entries(trace ?? {}).forEach(([key, item]) => { if (item) params.set(key, item); });
  const fulltextUrl = section ? `/projects/${encodeURIComponent(projectId)}/fulltext?${params.toString()}` : null;
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><div className="flex items-center gap-2 text-xs font-medium text-slate-400"><MapPin size={13} />{label ?? "响应位置"}</div><div className="mt-1 text-sm font-medium text-slate-800">{[value.chapter_title, value.section_heading].filter(Boolean).join(" > ") || "正文位置"}</div></div>
        {fulltextUrl ? <Link to={fulltextUrl} className="inline-flex items-center gap-1 text-xs font-medium text-brand-600 hover:text-brand-700">查看正文位置<ArrowUpRight size={13} /></Link> : null}
      </div>
      {value.excerpt ? <p className="mt-3 whitespace-pre-wrap text-sm leading-7 text-slate-600">{value.excerpt}</p> : <p className="mt-3 text-sm text-slate-400">当前位置尚无可展示正文。</p>}
    </div>
  );
}
