import { ArrowUpRight, Database, FileSearch } from "lucide-react";
import { Link } from "react-router-dom";
import type { RequirementDependencyDTO } from "../../types/api";

const kindLabels: Record<RequirementDependencyDTO["kind"], string> = {
  project_fact: "项目事实",
  fact_confirmation: "事实确认",
  evidence_requirement: "材料要求",
  evidence_query: "材料查询",
  material: "证明材料",
};

export function RequirementDependencies({ facts, materials, projectId }: { facts: RequirementDependencyDTO[]; materials: RequirementDependencyDTO[]; projectId: string }) {
  if (!facts.length && !materials.length) return <p className="text-sm text-slate-400">当前没有显式事实或材料依赖。</p>;
  return <div className="space-y-3">{[...facts, ...materials].map((item) => {
    const parameter = item.kind === "project_fact" || item.kind === "fact_confirmation" ? "fact" : "material";
    return <div key={`${item.kind}-${item.trace_id}`} className="flex items-start gap-3 rounded-lg border border-slate-200 bg-white p-4"><div className="rounded-md bg-slate-100 p-2 text-slate-500">{parameter === "fact" ? <Database size={16} /> : <FileSearch size={16} />}</div><div className="min-w-0 flex-1"><div className="text-[11px] font-medium uppercase tracking-wide text-slate-400">{kindLabels[item.kind]}</div><div className="mt-1 font-medium text-slate-800">{item.title}</div><div className={`mt-1 text-sm ${item.status === "query_not_executed" ? "font-medium text-amber-700" : "text-slate-500"}`}>{item.status_label}</div>{item.description ? <p className="mt-1 text-xs leading-5 text-slate-500">{item.description}</p> : null}</div>{item.kind !== "evidence_requirement" ? <Link to={`/projects/${encodeURIComponent(projectId)}/facts-materials?${parameter}=${encodeURIComponent(item.trace_id)}`} className="inline-flex items-center gap-1 text-xs font-medium text-brand-600">定位<ArrowUpRight size={13} /></Link> : null}</div>;
  })}</div>;
}
