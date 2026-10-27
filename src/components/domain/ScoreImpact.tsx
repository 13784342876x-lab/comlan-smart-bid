import { ArrowUpRight } from "lucide-react";
import { Link } from "react-router-dom";
import type { RequirementScoreImpactDTO } from "../../types/api";
import { ScoreRange } from "./ScoreRange";

export function ScoreImpact({ items, projectId }: { items: RequirementScoreImpactDTO[]; projectId: string }) {
  if (!items.length) return <p className="text-sm text-slate-400">没有直接关联评分项。</p>;
  return <div className="space-y-3">{items.map((item) => <div key={item.trace_id} className="rounded-lg border border-slate-200 bg-white p-4"><div className="flex items-start justify-between gap-3"><div><div className="font-medium text-slate-800">{item.title}</div><div className="mt-1 text-xs text-slate-400">满分 {item.max_score} 分 · 当前区间 <ScoreRange value={item.score_range} /> · 潜在提升 <ScoreRange value={item.potential_gain} /></div></div><Link to={`/projects/${encodeURIComponent(projectId)}/scoring?scoreItem=${encodeURIComponent(item.trace_id)}`} className="inline-flex items-center gap-1 text-xs font-medium text-brand-600">查看评分项<ArrowUpRight size={13} /></Link></div><p className="mt-3 line-clamp-3 text-sm leading-6 text-slate-500">{item.criteria_text}</p>{item.pending_conditions.length ? <div className="mt-3 text-xs leading-6 text-slate-500"><span className="font-medium text-slate-700">待确认条件：</span>{item.pending_conditions.join("；")}</div> : null}{item.pending_evidence.length ? <div className="mt-3 border-t border-slate-100 pt-3 text-xs leading-6 text-slate-500">{item.pending_evidence.map((evidence) => <div key={evidence.evidence_type}><span className="font-medium text-slate-700">{evidence.evidence_type}</span>：<span className={evidence.status === "query_not_executed" ? "text-amber-700" : ""}>{evidence.status_label}</span></div>)}</div> : null}</div>)}</div>;
}
