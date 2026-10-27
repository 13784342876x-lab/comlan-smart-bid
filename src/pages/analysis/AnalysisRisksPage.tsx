import { ArrowRight, Boxes, CircleAlert, FileQuestion, ListChecks, ShieldAlert } from "lucide-react";
import { Link } from "react-router-dom";
import { getTenderRisksPreparation } from "../../api/analysis";
import { useApiQuery } from "../../api/useApiQuery";
import { useProject } from "../../app/providers/ProjectProvider";
import { SourceReference } from "../../components/domain/SourceReference";
import { EmptyState, ErrorState, LoadingState } from "../../components/ui/PageState";
import { Panel } from "../../components/ui/Panel";
import type { PreparationNeedDTO, TenderRiskDTO } from "../../types/api";

export function AnalysisRisksPage() {
  const { projectId } = useProject();
  const query = useApiQuery((signal) => getTenderRisksPreparation(projectId, signal), [projectId]);
  if (query.loading && !query.data) return <LoadingState label="正在读取风险与准备事项…" />;
  if (query.error) return <ErrorState error={query.error} onRetry={query.reload} />;
  if (!query.data) return null;
  const value = query.data;
  return (
    <div className="space-y-5">
      <div><h3 className="text-lg font-semibold text-slate-950">风险与准备事项</h3><p className="mt-1 text-sm text-slate-500">仅呈现招标文件分析已识别的人工确认、事实、材料与高优先级响应，不混入全文审核问题。</p></div>
      {!value.blocking_semantics_available ? <div className="flex gap-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-900"><CircleAlert className="mt-0.5 shrink-0" size={18} /><span>当前写作前 contract 没有稳定的 blocking / deferred 枚举，因此这些项目表示“建议优先准备”，不会被 Web 判断为禁止继续。</span></div> : null}
      <RiskSection values={value.risks} />
      <div className="grid gap-5 xl:grid-cols-2">
        <NeedSection title="项目事实" description="项目本次投标需要确认的真实信息。" icon={Boxes} values={value.facts} />
        <NeedSection title="需要人工确认" description="解析结果无法可靠替代人的业务判断。" icon={FileQuestion} values={value.confirmations} />
        <NeedSection title="证明材料" description="证书、合同、验收证明等外部材料；尚未查询不等于不存在。" icon={ShieldAlert} values={value.materials} />
        <NeedSection title="写作响应事项" description="后续写作准备需要覆盖的原始招标要求。" icon={ListChecks} values={value.writing_responses} limit={20} />
      </div>
      <div className="flex justify-end"><Link to={`/projects/${encodeURIComponent(projectId)}/facts-materials`} className="inline-flex items-center gap-2 rounded-md bg-brand-700 px-4 py-2 text-sm font-medium text-white">进入事实与材料工作区<ArrowRight size={15} /></Link></div>
    </div>
  );
}

function RiskSection({ values }: { values: TenderRiskDTO[] }) {
  if (!values.length) return <EmptyState title="没有额外的写作前风险项" description="这不代表全文已通过审核，只表示招标文件分析当前没有识别出准备风险。" />;
  return <Panel title="优先关注"><div className="divide-y divide-slate-100">{values.slice(0, 20).map((item) => <div key={item.trace_id} className="px-5 py-4"><div className="text-xs font-medium text-amber-700">{item.category_label}</div><h4 className="mt-1 font-medium text-slate-900">{item.title}</h4><p className="mt-1 line-clamp-4 text-sm leading-6 text-slate-600">{item.description}</p>{item.source_references[0] ? <div className="mt-3"><SourceReference compact value={item.source_references[0]} /></div> : null}</div>)}</div></Panel>;
}

function NeedSection({ title, description, icon: Icon, values, limit = 10 }: { title: string; description: string; icon: typeof Boxes; values: PreparationNeedDTO[]; limit?: number }) {
  return <Panel><div className="border-b border-slate-100 px-5 py-4"><div className="flex items-center gap-2 font-semibold text-slate-900"><Icon size={17} className="text-brand-600" />{title}<span className="text-xs font-normal text-slate-400">{values.length} 项</span></div><p className="mt-1 text-xs leading-5 text-slate-500">{description}</p></div>{values.length ? <div className="divide-y divide-slate-100">{values.slice(0, limit).map((item) => <div key={item.trace_id} className="px-5 py-3"><div className="flex items-start justify-between gap-3"><div><div className="font-medium text-slate-800">{item.title}</div><p className="mt-1 line-clamp-3 text-xs leading-5 text-slate-500">{item.description}</p></div><span className={`shrink-0 text-xs font-medium ${item.status === "query_not_executed" ? "text-amber-700" : "text-slate-500"}`}>{item.status_label}</span></div></div>)}</div> : <div className="px-5 py-8 text-sm text-slate-500">当前 artifact 未登记此类准备项。</div>}{values.length > limit ? <div className="border-t border-slate-100 px-5 py-3 text-xs text-slate-400">另有 {values.length - limit} 项，可在后续 Writing Preparation 工作区继续管理。</div> : null}</Panel>;
}
