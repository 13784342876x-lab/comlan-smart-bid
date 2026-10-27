import { ArrowUpRight } from "lucide-react";
import { Link } from "react-router-dom";
import type { RequirementReviewStateDTO } from "../../types/api";
import { ActionabilityIndicator } from "./ActionabilityIndicator";
import { ResponseLocation } from "./ResponseLocation";

export function RequirementReviewState({ items, projectId }: { items: RequirementReviewStateDTO[]; projectId: string }) {
  if (!items.length) return <p className="text-sm text-slate-400">当前 Requirement 没有关联人工审核事项。</p>;
  return <div className="space-y-3">{items.map((item) => <div key={item.trace_id} className="rounded-lg border border-slate-200 bg-white p-4"><div className="flex flex-wrap items-center justify-between gap-3"><div className="flex flex-wrap items-center gap-2"><ActionabilityIndicator value={item.actionability} />{item.release_blocking ? <span className="text-xs font-medium text-red-600">影响放行</span> : null}</div><Link to={`/projects/${encodeURIComponent(projectId)}/review?item=${encodeURIComponent(item.trace_id)}`} className="inline-flex items-center gap-1 text-xs font-medium text-brand-600">查看审核事项<ArrowUpRight size={13} /></Link></div><h4 className="mt-3 font-medium text-slate-800">{item.title}</h4><p className="mt-2 text-sm leading-6 text-slate-600">{item.machine_recommendation ?? "等待人工审核。"}</p><div className="mt-3 text-xs text-slate-500">当前人工决定：<span className="font-medium text-slate-700">{item.current_decision === "pending" ? "待处理" : item.current_decision}</span></div>{item.revision_instruction ? <div className="mt-2 text-sm text-slate-600">人工修改意见：{item.revision_instruction}</div> : null}{item.risk_acceptance_ref ? <div className="mt-2 text-sm text-slate-600">已记录风险确认</div> : null}{item.revision_locations.map((location) => <div className="mt-3" key={location.technical_ref ?? location.section_id ?? "location"}><ResponseLocation value={location} projectId={projectId} label="建议修改位置" /></div>)}</div>)}</div>;
}
