import { GitCommitHorizontal } from "lucide-react";
import { getProjectVersions } from "../../api/projects";
import { useApiQuery } from "../../api/useApiQuery";
import { useProject } from "../../app/providers/ProjectProvider";
import { EmptyState, ErrorState, LoadingState } from "../../components/ui/PageState";
import { Panel } from "../../components/ui/Panel";
import { formatLocalDateTime } from "../../utils/dateTime";

export function VersionsSummaryPage() {
  const { projectId } = useProject();
  const query = useApiQuery((signal) => getProjectVersions(projectId, signal), [projectId]);
  if (query.loading) return <LoadingState label="正在读取版本记录…" />;
  if (query.error) return <ErrorState error={query.error} onRetry={query.reload} />;
  if (!query.data) return <EmptyState title="暂无版本记录" description="当前项目没有可展示的冻结版本。" />;
  const data = query.data;
  return (
    <div className="space-y-5">
      <div><h2 className="text-xl font-semibold">版本记录</h2><p className="mt-1 text-sm text-slate-500">展示已有全文版本、机器审核和人工审核记录。</p></div>
      <Panel className="p-5">
        <ol className="space-y-1">
          {data.entries.map((entry, index) => (
            <li key={`${entry.kind}-${entry.internal_id ?? index}`} className="grid grid-cols-[32px_minmax(0,1fr)] gap-3">
              <div className="flex flex-col items-center"><div className="flex h-7 w-7 items-center justify-center rounded-full bg-brand-50 text-brand-700"><GitCommitHorizontal size={14} /></div>{index < data.entries.length - 1 ? <div className="min-h-12 w-px flex-1 bg-slate-200" /> : null}</div>
              <div className="pb-5"><div className="flex flex-wrap items-center gap-2"><span className="font-medium">{entry.label}</span><span className="text-xs text-slate-400">{entry.status}</span>{entry.current ? <span className="rounded-full bg-brand-50 px-2 py-0.5 text-xs text-brand-700">当前</span> : null}</div>{entry.generated_at ? <div className="mt-1 text-xs text-slate-400">{formatLocalDateTime(entry.generated_at)}</div> : null}</div>
            </li>
          ))}
        </ol>
      </Panel>
      <div className="rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-800">Revision Round / Task / Result / Diff 尚未接入生产字段。</div>
    </div>
  );
}
