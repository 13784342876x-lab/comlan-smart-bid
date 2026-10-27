import { ArrowRight, Boxes, FileStack, ListTree, Scale, TableProperties } from "lucide-react";
import { Link, useOutletContext } from "react-router-dom";
import type { TenderAnalysisOverviewDTO } from "../../types/api";
import { useProject } from "../../app/providers/ProjectProvider";
import { Panel } from "../../components/ui/Panel";

export function TenderAnalysisPage() {
  const { projectId } = useProject();
  const { overview } = useOutletContext<{ overview: TenderAnalysisOverviewDTO }>();
  const base = `/projects/${encodeURIComponent(projectId)}`;
  return (
    <div className="space-y-5">
      <section>
        <h3 className="text-lg font-semibold text-slate-950">这次招标最重要的是什么？</h3>
        <p className="mt-1 text-sm text-slate-500">先理解采购目标、评分规则、技术参数和准备缺口，再进入写作准备。</p>
      </section>

      {overview.project_information.length ? (
        <Panel title="项目关键信息">
          <dl className="grid gap-x-8 gap-y-0 px-5 py-2 md:grid-cols-2 xl:grid-cols-3">
            {overview.project_information.map((item) => (
              <div key={item.key} className="border-b border-slate-100 py-3">
                <dt className="text-xs text-slate-400">{item.label}</dt>
                <dd className="mt-1 text-sm leading-6 text-slate-800">{item.value}</dd>
                {item.source_reference ? <div className="mt-1 text-[11px] text-slate-400">来源：{item.source_reference.clause_path.join(" > ") || item.source_reference.file_name}</div> : null}
              </div>
            ))}
          </dl>
        </Panel>
      ) : null}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <SummaryLink icon={FileStack} label="招标资料" value={`${overview.source_file_count} 份`} detail="文件角色与解析状态" to={`${base}/analysis/files`} />
        <SummaryLink icon={ListTree} label="招标要求" value={`${overview.requirement_count} 条`} detail={categorySummary(overview)} to={`${base}/analysis/requirements`} />
        <SummaryLink icon={Scale} label="招标评分规则" value={`${overview.score_rule_count} 项`} detail={overview.available_score_total == null ? "保留招标评分原文" : `可争取分值 ${formatScore(overview.available_score_total)} 分`} to={`${base}/analysis/scoring`} />
        <SummaryLink icon={TableProperties} label="技术参数" value={`${overview.parameter_row_count} 行`} detail={`${overview.parameter_table_count} 个参数表，可搜索`} to={`${base}/analysis/parameters`} />
      </div>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.4fr)_minmax(320px,0.8fr)]">
        <Panel title="优先关注要求">
          {overview.priority_items.length ? (
            <div className="divide-y divide-slate-100">
              {overview.priority_items.map((item) => (
                <div key={item.trace_id} className="px-5 py-4">
                  <div className="flex items-start justify-between gap-4">
                    <div><h4 className="font-medium text-slate-900">{item.title}</h4><p className="mt-1 line-clamp-3 text-sm leading-6 text-slate-500">{item.description}</p></div>
                    <span className="shrink-0 text-xs font-medium text-amber-700">{item.action_label}</span>
                  </div>
                  {item.requirement_ids[0] ? <Link className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-brand-700" to={`${base}/analysis/requirements/${encodeURIComponent(item.requirement_ids[0])}`}>查看招标要求<ArrowRight size={12} /></Link> : null}
                </div>
              ))}
            </div>
          ) : <div className="px-5 py-8 text-sm text-slate-500">当前写作前 artifact 没有给出可稳定映射的优先级，不做额外风险推断。</div>}
        </Panel>
        <Panel className="p-5">
          <div className="flex items-center gap-2 text-sm font-semibold text-slate-900"><Boxes size={17} className="text-brand-600" />写作前准备</div>
          <dl className="mt-4 space-y-3 text-sm">
            <PrepRow label="项目事实" value={overview.fact_need_count} />
            <PrepRow label="需要人工确认" value={overview.confirmation_count} />
            <PrepRow label="证明材料" value={overview.material_need_count} />
          </dl>
          <p className="mt-4 text-xs leading-5 text-slate-500">“招标分析完成”和“写作准备已齐全”是两个状态。没有 canonical blocking 语义时，页面只提示优先准备，不阻断后续流程。</p>
          <div className="mt-4 flex flex-wrap gap-3">
            <Link to={`${base}/analysis/risks`} className="inline-flex items-center gap-1 rounded-md bg-brand-700 px-3 py-2 text-sm font-medium text-white">查看准备事项<ArrowRight size={14} /></Link>
            <Link to={`${base}/facts-materials`} className="inline-flex items-center gap-1 rounded-md border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700">事实与材料工作区</Link>
          </div>
        </Panel>
      </div>
    </div>
  );
}

function SummaryLink({ icon: Icon, label, value, detail, to }: { icon: typeof FileStack; label: string; value: string; detail: string; to: string }) {
  return <Link to={to} className="rounded-xl border border-slate-200 bg-white p-4 shadow-panel transition hover:border-brand-200 hover:shadow-sm"><div className="flex items-center gap-2 text-xs font-medium text-slate-500"><Icon size={15} className="text-brand-600" />{label}</div><div className="mt-3 text-2xl font-semibold text-slate-900">{value}</div><div className="mt-1 min-h-5 text-xs text-slate-400">{detail}</div></Link>;
}

function PrepRow({ label, value }: { label: string; value: number }) {
  return <div className="flex items-center justify-between border-b border-slate-100 pb-3"><dt className="text-slate-500">{label}</dt><dd className="font-semibold text-slate-900">{value} 项</dd></div>;
}

function categorySummary(value: TenderAnalysisOverviewDTO) {
  return value.requirement_categories.slice(0, 2).map((item) => `${item.label} ${item.count}`).join(" · ") || "查看全部原始要求";
}

function formatScore(value: number) {
  return Number.isInteger(value) ? value.toFixed(0) : value.toFixed(1);
}
