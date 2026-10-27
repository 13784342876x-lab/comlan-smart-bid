import { ArrowRight, CheckSquare2, FileWarning, Scale } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { getTenderScoringRules } from "../../api/analysis";
import { useApiQuery } from "../../api/useApiQuery";
import { useProject } from "../../app/providers/ProjectProvider";
import { SourceReference } from "../../components/domain/SourceReference";
import { Drawer } from "../../components/ui/Drawer";
import { EmptyState, ErrorState, LoadingState } from "../../components/ui/PageState";
import { Panel } from "../../components/ui/Panel";

export function AnalysisScoringPage() {
  const { projectId } = useProject();
  const [params, setParams] = useSearchParams();
  const query = useApiQuery((signal) => getTenderScoringRules(projectId, signal), [projectId]);
  if (query.loading && !query.data) return <LoadingState label="正在读取招标评分规则…" />;
  if (query.error) return <ErrorState error={query.error} onRetry={query.reload} />;
  if (!query.data?.rules.length) return <EmptyState title="未识别到写作前评分规则" description="页面不会使用机器审核估分补造招标评分规则。" />;
  const selected = query.data.rules.find((item) => item.score_id === params.get("scoreItem")) ?? null;
  const open = (scoreId: string | null) => {
    const next = new URLSearchParams(params);
    if (scoreId) next.set("scoreItem", scoreId); else next.delete("scoreItem");
    setParams(next, { replace: true });
  };
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-4"><div><h3 className="text-lg font-semibold text-slate-950">招标评分规则</h3><p className="mt-1 text-sm text-slate-500">回答“招标人怎么给分”，不显示当前标书机器估分。</p></div><div className="text-right"><div className="text-2xl font-semibold text-slate-900">{query.data.rule_count} 项</div><div className="text-xs text-slate-500">可争取分值 {score(query.data.available_score_total)} 分</div></div></div>
      <div className="flex justify-end"><Link to={`/projects/${encodeURIComponent(projectId)}/writing-preparation?view=material`} className="text-sm font-medium text-brand-700">查看评分相关事实与材料准备 <ArrowRight size={14} className="ml-1 inline" /></Link></div>
      <Panel>
        <div className="sticky top-0 grid min-w-[920px] grid-cols-[minmax(180px,0.7fr)_minmax(360px,1.6fr)_90px_minmax(190px,0.8fr)_70px] gap-4 border-b border-slate-200 bg-slate-50 px-5 py-3 text-xs font-semibold text-slate-500"><div>评分项</div><div>评分要求</div><div>满分</div><div>准备事项</div><div></div></div>
        <div className="overflow-x-auto">
          <div className="min-w-[920px] divide-y divide-slate-100">
            {query.data.rules.map((item) => <button type="button" key={item.score_id} onClick={() => open(item.score_id)} className="grid w-full grid-cols-[minmax(180px,0.7fr)_minmax(360px,1.6fr)_90px_minmax(190px,0.8fr)_70px] gap-4 px-5 py-4 text-left hover:bg-slate-50"><div><div className="font-medium text-slate-900">{item.title}</div>{item.score_group ? <div className="mt-1 text-xs text-slate-400">{item.score_group}</div> : null}</div><div className="line-clamp-3 text-sm leading-6 text-slate-600">{item.criteria_text}</div><div className="font-semibold text-slate-900">{item.available_score == null ? item.score_text || "—" : `${score(item.available_score)} 分`}</div><div className="text-xs leading-5 text-slate-500">{item.material_needs.length ? `${item.material_needs.length} 项材料` : item.preparation_text || "按评分原文准备"}</div><div className="text-xs font-medium text-brand-700">详情</div></button>)}
          </div>
        </div>
      </Panel>
      {selected ? <Drawer onClose={() => open(null)} title={selected.title}>
        <div className="space-y-6"><section><div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-400"><Scale size={14} />评分规则原文</div><p className="mt-3 whitespace-pre-wrap text-sm leading-7 text-slate-700">{selected.criteria_text}</p><div className="mt-3 text-lg font-semibold text-slate-900">满分：{selected.score_text || `${score(selected.available_score)} 分`}</div></section><SourceReference value={selected.source_reference} /><DetailList icon={CheckSquare2} title="需要的响应" values={[...(selected.response_suggestions ?? []), ...(selected.preparation_text ? [selected.preparation_text] : [])]} empty="当前 artifact 未给出结构化写作行动，保留评分原文。" /><DetailList icon={FileWarning} title="需要的事实 / 材料" values={[...selected.fact_needs, ...selected.fact_preparation.map((item) => `${item.title} · ${item.status_label}`), ...selected.material_needs.map((item) => `${item.title} · ${item.status_label}`)]} empty="当前写作前 artifact 未登记依赖，不据此推断材料不存在。" />{selected.fact_preparation.length ? <section><div className="flex flex-wrap gap-2">{selected.fact_preparation.map((item) => <Link key={item.trace_id} to={item.target_url} className="text-sm font-medium text-brand-700">{item.title} · {item.status_label}</Link>)}</div></section> : null}{selected.requirement_ids.length ? <section><h4 className="text-sm font-semibold text-slate-900">关联招标要求</h4><div className="mt-2 flex flex-wrap gap-2">{selected.requirement_ids.map((id, index) => <Link key={id} to={`/projects/${encodeURIComponent(projectId)}/analysis/requirements/${encodeURIComponent(id)}`} className="inline-flex items-center gap-1 text-sm font-medium text-brand-700">查看关联要求 {index + 1}<ArrowRight size={13} /></Link>)}</div></section> : null}</div>
      </Drawer> : null}
    </div>
  );
}

function DetailList({ icon: Icon, title, values, empty }: { icon: typeof CheckSquare2; title: string; values: string[]; empty: string }) { return <section><div className="flex items-center gap-2 text-sm font-semibold text-slate-900"><Icon size={16} className="text-brand-600" />{title}</div>{values.length ? <ul className="mt-2 space-y-2 text-sm leading-6 text-slate-600">{values.map((value, index) => <li key={`${value}-${index}`} className="rounded-md bg-slate-50 px-3 py-2">{value}</li>)}</ul> : <p className="mt-2 text-sm leading-6 text-slate-500">{empty}</p>}</section>; }
function score(value: number | null) { return value == null ? "—" : Number.isInteger(value) ? value.toFixed(0) : value.toFixed(1); }
