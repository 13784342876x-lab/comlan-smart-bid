import { FileCheck2, FileText } from "lucide-react";
import { getTenderAnalysisFiles } from "../../api/analysis";
import { useApiQuery } from "../../api/useApiQuery";
import { useProject } from "../../app/providers/ProjectProvider";
import { EmptyState, ErrorState, LoadingState } from "../../components/ui/PageState";
import { Panel } from "../../components/ui/Panel";

export function AnalysisFilesPage() {
  const { projectId } = useProject();
  const query = useApiQuery((signal) => getTenderAnalysisFiles(projectId, signal), [projectId]);
  if (query.loading && !query.data) return <LoadingState label="正在读取招标文件结构…" />;
  if (query.error) return <ErrorState error={query.error} onRetry={query.reload} />;
  if (!query.data?.length) return <EmptyState title="没有可展示的招标资料" description="当前 Analysis Run 未登记 Source File。" />;
  return (
    <Panel>
      <div className="border-b border-slate-100 px-5 py-4"><h3 className="font-semibold text-slate-900">招标文件</h3><p className="mt-1 text-xs text-slate-500">展示文件业务角色与解析状态，不暴露服务器存储路径。</p></div>
      <div className="divide-y divide-slate-100">
        {query.data.map((file) => (
          <div key={file.file_id || file.display_name} className="grid gap-3 px-5 py-4 md:grid-cols-[180px_minmax(0,1fr)_130px] md:items-center">
            <div className="text-sm font-medium text-brand-700">{file.role_label}</div>
            <div className="min-w-0"><div className="flex items-center gap-2 font-medium text-slate-900"><FileText size={16} className="shrink-0 text-slate-400" /><span className="truncate">{file.display_name}</span></div>{file.size != null ? <div className="mt-1 text-xs text-slate-400">{formatBytes(file.size)}</div> : null}</div>
            <div className="inline-flex items-center gap-2 text-xs text-emerald-700"><FileCheck2 size={14} />{statusLabel(file.parse_status)}</div>
          </div>
        ))}
      </div>
    </Panel>
  );
}

function statusLabel(value: string) {
  return ({ parsed: "解析完成", uploaded: "已上传", ready: "已就绪" } as Record<string, string>)[value] ?? value;
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 ** 2).toFixed(1)} MB`;
}
