import { ChevronRight, Filter, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { getProjectRequirements } from "../../api/projects";
import { useApiQuery } from "../../api/useApiQuery";
import { useProject } from "../../app/providers/ProjectProvider";
import { RequirementStatus, requirementStatusLabel } from "../../components/domain/RequirementStatus";
import { SourceReference } from "../../components/domain/SourceReference";
import { EmptyState, ErrorState, LoadingState } from "../../components/ui/PageState";
import type { ActionabilityKey, RequirementResponseDTO } from "../../types/api";
import { RequirementDetailDrawer } from "./RequirementDetailDrawer";

const reviewLabels: Record<ActionabilityKey, string> = { must_decide: "需要我决定", verification: "待验证", no_action: "无需操作", informational: "背景信息" };

export function RequirementMatrixPage() {
  const { projectId } = useProject();
  const { requirementId } = useParams();
  const navigate = useNavigate();
  const query = useApiQuery((signal) => getProjectRequirements(projectId, signal), [projectId]);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const [category, setCategory] = useState("all");
  const [review, setReview] = useState("all");
  const [scoring, setScoring] = useState("all");
  const [dependency, setDependency] = useState("all");
  const rows = useMemo(() => filterRequirements(query.data?.requirements ?? [], { search, status, category, review, scoring, dependency }), [query.data, search, status, category, review, scoring, dependency]);
  const statuses = useMemo(() => Array.from(new Set((query.data?.requirements ?? []).map((item) => item.response_status))), [query.data]);
  const categories = useMemo(() => Array.from(new Set((query.data?.requirements ?? []).map((item) => item.category).filter((value): value is string => Boolean(value)))), [query.data]);
  const base = `/projects/${encodeURIComponent(projectId)}/analysis/requirements`;
  if (query.loading) return <LoadingState label="正在读取招标要求…" />;
  if (query.error) return <ErrorState error={query.error} onRetry={query.reload} />;
  if (!query.data) return <EmptyState title="暂无招标要求" description="当前审核版本没有 Requirement / Response Matrix。" />;
  const preWriting = query.data.reviewed_version.status === "analysis_ready";
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-4"><div><div className="text-xs font-medium text-brand-600">招标分析</div><h2 className="mt-1 text-xl font-semibold text-slate-900">招标要求与响应</h2><p className="mt-1 text-sm text-slate-500">{preWriting ? "写作前查看招标原文、来源、评分关系和材料需求；形成正文后在同一页面追加审核链路。" : "从招标原文追踪到正文响应、审核、评分和事实材料依赖。"}</p></div><div className="text-right text-xs text-slate-400">当前{preWriting ? "分析" : "审核"}版本<br /><span className="font-medium text-slate-600">{query.data.reviewed_version.label}</span></div></div>

      <div className="rounded-xl border border-slate-200 bg-white p-3 shadow-panel">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <label className="relative min-w-[260px] flex-1"><Search className="absolute left-3 top-2.5 text-slate-400" size={16} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索要求、来源或响应章节" className="h-9 w-full rounded-md border border-slate-200 bg-slate-50 pl-9 pr-3 text-sm text-slate-800 placeholder:text-slate-400" /></label>
          <FilterSelect value={status} onChange={setStatus} label="响应状态"><option value="all">全部状态</option>{statuses.map((value) => <option key={value} value={value}>{requirementStatusLabel(value)}</option>)}</FilterSelect>
          <FilterSelect value={category} onChange={setCategory} label="要求类别"><option value="all">全部类别</option>{categories.map((value) => <option key={value} value={value}>{value}</option>)}</FilterSelect>
          <FilterSelect value={review} onChange={setReview} label="审核"><option value="all">全部审核</option><option value="must_decide">需要我决定</option><option value="verification">待验证</option><option value="no_action">无需操作</option><option value="completed">已处理</option><option value="none">无审核事项</option></FilterSelect>
          <FilterSelect value={scoring} onChange={setScoring} label="评分"><option value="all">全部评分关系</option><option value="with">有评分影响</option><option value="without">无直接评分</option></FilterSelect>
          <FilterSelect value={dependency} onChange={setDependency} label="事实材料"><option value="all">全部依赖</option><option value="fact">需要事实</option><option value="material">需要材料</option><option value="query_not_executed">尚未查询</option><option value="none">无依赖</option></FilterSelect>
        </div>
        <div className="mt-3 flex items-center justify-between border-t border-slate-100 pt-3 text-xs text-slate-500"><span>显示 <strong className="text-slate-800">{rows.length}</strong> / {query.data.requirements.length} 条</span><span><Filter size={13} className="mr-1 inline" />筛选仅使用真实 DTO 状态</span></div>
      </div>

      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-panel">
        <div className="scrollbar-thin overflow-x-auto">
          <table className="w-full min-w-[1210px] table-fixed text-left">
            <thead className="sticky top-[97px] z-10 bg-slate-50 text-[11px] font-semibold uppercase tracking-wide text-slate-500"><tr><th className="w-[310px] px-4 py-3">招标要求</th><th className="w-[210px] px-4 py-3">来源</th><th className="w-[210px] px-4 py-3">当前响应</th><th className="w-[105px] px-4 py-3">响应状态</th><th className="w-[110px] px-4 py-3">评分影响</th><th className="w-[125px] px-4 py-3">审核状态</th><th className="w-[120px] px-4 py-3">事实 / 材料</th><th className="w-[70px] px-4 py-3"></th></tr></thead>
            <tbody className="divide-y divide-slate-100">{rows.map((item) => <RequirementRow key={item.trace_id} item={item} onOpen={() => navigate(`${base}/${encodeURIComponent(item.trace_id)}`)} />)}</tbody>
          </table>
        </div>
        {!rows.length ? <div className="p-5"><EmptyState title="没有符合条件的 Requirement" description="调整搜索词或筛选条件后重试。" /></div> : null}
      </div>
      {requirementId ? <RequirementDetailDrawer projectId={projectId} requirementId={requirementId} onClose={() => navigate(base)} /> : null}
    </div>
  );
}

function RequirementRow({ item, onOpen }: { item: RequirementResponseDTO; onOpen: () => void }) {
  const location = item.response_locations.find((value) => value.excerpt) ?? item.primary_response_location ?? item.response_locations[0];
  const reviewLabel = item.review.completed_count > 0 && item.review.pending_count === 0 ? "已处理" : item.review.actionability ? reviewLabels[item.review.actionability] : "无审核事项";
  return <tr onClick={onOpen} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") onOpen(); }} tabIndex={0} className="h-[92px] cursor-pointer align-top text-sm hover:bg-brand-50/40 focus:bg-brand-50/60"><td className="px-4 py-4"><div className="line-clamp-2 font-medium leading-6 text-slate-900">{item.title}</div>{item.category ? <div className="mt-1 text-[11px] text-slate-400">{item.category}</div> : null}</td><td className="px-4 py-4"><SourceReference value={item.source_reference} compact /></td><td className="px-4 py-4"><div className="line-clamp-2 text-xs leading-5 text-slate-600">{[location?.chapter_title, location?.section_heading].filter(Boolean).join(" / ") || (item.response_status === "not_started" ? "尚未形成正文响应" : "未定位正文响应")}</div>{item.response_locations.length ? <div className="mt-1 text-[11px] text-slate-400">{item.response_locations.length} 个响应位置</div> : null}</td><td className="px-4 py-4"><RequirementStatus value={item.response_status} /></td><td className="px-4 py-4 text-xs text-slate-600">{item.related_score_count ? <>关联 <strong className="text-slate-900">{item.related_score_count}</strong> 项</> : "—"}</td><td className="px-4 py-4"><div className={`text-xs font-medium ${item.review.actionability === "must_decide" ? "text-red-700" : "text-slate-600"}`}>{reviewLabel}</div>{item.related_finding_count ? <div className="mt-1 text-[11px] text-slate-400">{item.related_finding_count} 条机器引用</div> : null}</td><td className="px-4 py-4 text-xs leading-5 text-slate-600">{item.fact_dependency_count ? <div>事实 {item.fact_dependency_count}</div> : null}{item.material_dependency_count ? <div>{item.query_not_executed_count ? `尚未查询 ${item.query_not_executed_count}` : `材料 ${item.material_dependency_count}`}</div> : null}{!item.fact_dependency_count && !item.material_dependency_count ? "—" : null}</td><td className="px-4 py-4 text-right"><button onClick={(event) => { event.stopPropagation(); onOpen(); }} className="rounded-md p-2 text-slate-400 hover:bg-white hover:text-brand-600" aria-label={`查看 ${item.title}`}><ChevronRight size={17} /></button></td></tr>;
}

function FilterSelect({ value, onChange, label, children }: { value: string; onChange: (value: string) => void; label: string; children: React.ReactNode }) {
  return <label><span className="sr-only">{label}</span><select value={value} onChange={(event) => onChange(event.target.value)} className="h-9 rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-600">{children}</select></label>;
}

function filterRequirements(items: RequirementResponseDTO[], filters: { search: string; status: string; category: string; review: string; scoring: string; dependency: string }) {
  const search = filters.search.trim().toLocaleLowerCase();
  return items.filter((item) => {
    const locations = item.response_locations.flatMap((value) => [value.chapter_title, value.section_heading, value.technical_ref]).filter(Boolean).join(" ");
    const haystack = [item.requirement_text, item.trace_id, item.source_location, locations].filter(Boolean).join(" ").toLocaleLowerCase();
    if (search && !haystack.includes(search)) return false;
    if (filters.status !== "all" && item.response_status !== filters.status) return false;
    if (filters.category !== "all" && item.category !== filters.category) return false;
    if (filters.review === "completed" && !(item.review.completed_count > 0 && item.review.pending_count === 0)) return false;
    if (filters.review === "none" && item.review.actionability !== null) return false;
    if (!["all", "completed", "none"].includes(filters.review) && item.review.actionability !== filters.review) return false;
    if (filters.scoring === "with" && item.related_score_count === 0) return false;
    if (filters.scoring === "without" && item.related_score_count > 0) return false;
    if (filters.dependency === "fact" && item.fact_dependency_count === 0) return false;
    if (filters.dependency === "material" && item.material_dependency_count === 0) return false;
    if (filters.dependency === "query_not_executed" && item.query_not_executed_count === 0) return false;
    if (filters.dependency === "none" && (item.fact_dependency_count > 0 || item.material_dependency_count > 0)) return false;
    return true;
  });
}
