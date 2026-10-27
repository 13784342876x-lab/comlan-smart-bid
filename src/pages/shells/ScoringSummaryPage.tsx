import { getProjectScoring } from "../../api/projects";
import { Link, useSearchParams } from "react-router-dom";
import { MapPin } from "lucide-react";
import { useApiQuery } from "../../api/useApiQuery";
import { useProject } from "../../app/providers/ProjectProvider";
import { ScoreRange } from "../../components/domain/ScoreRange";
import { EmptyState, ErrorState, LoadingState } from "../../components/ui/PageState";
import { Panel } from "../../components/ui/Panel";

export function ScoringSummaryPage() {
  const { projectId } = useProject();
  const query = useApiQuery((signal) => getProjectScoring(projectId, signal), [projectId]);
  const [searchParams] = useSearchParams();
  const focusedItem = searchParams.get("scoreItem");
  if (query.loading) return <LoadingState label="正在读取评分评估…" />;
  if (query.error) return <ErrorState error={query.error} onRetry={query.reload} />;
  if (!query.data) return <EmptyState title="暂无评分数据" description="当前审核版本没有可用的评分评估。" />;
  const { headline, item_counts: counts } = query.data;
  const displayItems = focusedItem
    ? [...query.data.items].sort((left, right) => Number(right.trace_id === focusedItem) - Number(left.trace_id === focusedItem))
    : query.data.items;
  return (
    <div className="space-y-5">
      <div><h2 className="text-xl font-semibold">评分中心</h2><p className="mt-1 text-sm text-slate-500">保留 15 项 / 70 分原始颗粒度；存在待确认条件时只展示区间。</p></div>
      <div className="grid gap-4 md:grid-cols-4">
        <Metric label="技术评分满分" value={`${headline.technical_score_max} 分`} />
        <Metric label="已确认" value={`${headline.confirmed} 分`} />
        <Panel className="p-4"><div className="text-xs text-slate-500">机器估计</div><div className="mt-2 text-xl"><ScoreRange value={headline.estimated_range} /></div></Panel>
        <Metric label="尚待确认" value={`${headline.pending_exposure} 分`} />
      </div>
      <Panel>
        <div className="grid grid-cols-3 border-b border-slate-100 bg-slate-50 px-5 py-3 text-sm"><span>已确认 {counts.confirmed ?? 0}</span><span>机器估计 {counts.estimated ?? 0}</span><span>待确认 {counts.pending ?? 0}</span></div>
        <div className="divide-y divide-slate-100">
          {displayItems.map((item) => (
            <div id={`score-${item.trace_id}`} key={item.trace_id} className={`grid gap-3 px-5 py-4 md:grid-cols-[minmax(0,1fr)_100px_130px] ${item.trace_id === focusedItem ? "bg-brand-50 ring-1 ring-inset ring-brand-200" : ""}`}>
              <div><div className="font-medium text-slate-800">{item.title}</div><p className="mt-1 line-clamp-2 text-sm leading-6 text-slate-500">{item.criteria_text}</p>{item.response_locations.length ? <div className="mt-2 flex flex-wrap gap-2">{item.response_locations.map((location, index) => location.section_id ? <Link key={`${location.section_id}-${index}`} to={`/projects/${encodeURIComponent(projectId)}/fulltext?section=${encodeURIComponent(location.section_id)}${location.paragraph_id ? `&paragraph=${encodeURIComponent(location.paragraph_id)}` : ""}&scoreItem=${encodeURIComponent(item.trace_id)}`} className="inline-flex items-center gap-1 text-xs font-medium text-brand-700"><MapPin size={12} />查看评分对应正文{item.response_locations.length > 1 ? ` ${index + 1}` : ""}</Link> : null)}</div> : null}</div>
              <div className="text-sm text-slate-500">满分 <strong className="text-slate-800">{item.max_score}</strong></div>
              <div className="text-sm"><span className="mr-2 text-slate-400">区间</span><ScoreRange value={item.score_range} /></div>
            </div>
          ))}
        </div>
      </Panel>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return <Panel className="p-4"><div className="text-xs text-slate-500">{label}</div><div className="mt-2 text-xl font-semibold">{value}</div></Panel>;
}
