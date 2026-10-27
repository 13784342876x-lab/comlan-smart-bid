import { Clock3, FileText, ListTree, Scale, SearchCheck, TableProperties, TriangleAlert } from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";
import { getTenderAnalysis } from "../api/analysis";
import { useApiQuery } from "../api/useApiQuery";
import { useProject } from "../app/providers/ProjectProvider";
import { ErrorState, LoadingState } from "../components/ui/PageState";
import { formatLocalDateTime } from "../utils/dateTime";

const tabs = [
  { suffix: "", label: "总览", icon: SearchCheck, end: true },
  { suffix: "/files", label: "招标文件", icon: FileText },
  { suffix: "/requirements", label: "招标要求", icon: ListTree },
  { suffix: "/scoring", label: "评分规则", icon: Scale },
  { suffix: "/parameters", label: "技术参数", icon: TableProperties },
  { suffix: "/risks", label: "风险与准备事项", icon: TriangleAlert },
];

export function AnalysisLayout() {
  const { projectId, projectName } = useProject();
  const query = useApiQuery((signal) => getTenderAnalysis(projectId, signal), [projectId]);
  if (query.loading && !query.data) return <LoadingState label="正在读取招标分析工作区…" />;
  if (query.error) return <ErrorState error={query.error} onRetry={query.reload} />;
  if (!query.data) return null;
  const value = query.data;
  const base = `/projects/${encodeURIComponent(projectId)}/analysis`;
  return (
    <div className="space-y-5">
      <section className="border-b border-slate-200 pb-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="text-xs font-medium text-brand-700">招标分析工作区</div>
            <h2 className="mt-1 text-xl font-semibold text-slate-950">{projectName}</h2>
            <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-500">
              <span className="font-medium text-emerald-700">{value.status}</span>
              <span>基于 {value.source_file_count} 份招标资料</span>
              {value.lineage.completed_at ? <span className="inline-flex items-center gap-1"><Clock3 size={12} />最后分析 {formatLocalDateTime(value.lineage.completed_at)}</span> : null}
              {value.lineage.run_id ? <NavLink className="font-medium text-brand-700 hover:underline" to={`/projects/${encodeURIComponent(projectId)}/runs/${encodeURIComponent(value.lineage.run_id)}`}>查看处理进度</NavLink> : null}
            </div>
          </div>
          <div className="rounded-md border border-slate-200 bg-white px-3 py-2 text-xs text-slate-500">
            {value.lineage.source_mode === "pipeline_analysis" ? "当前 Analysis Run" : "历史写作前分析"}
            {value.lineage.post_writing_available ? <span className="ml-2 text-brand-700">已形成正文</span> : null}
          </div>
        </div>
        <nav className="mt-5 flex min-w-0 gap-1 overflow-x-auto" aria-label="招标分析子导航">
          {tabs.map(({ suffix, label, icon: Icon, end }) => (
            <NavLink key={suffix || "overview"} end={end} to={`${base}${suffix}`} className={({ isActive }) => `inline-flex shrink-0 items-center gap-2 border-b-2 px-3 py-2 text-sm ${isActive ? "border-brand-600 font-medium text-brand-700" : "border-transparent text-slate-500 hover:text-slate-900"}`}>
              <Icon size={15} />{label}
            </NavLink>
          ))}
        </nav>
      </section>
      <Outlet context={{ overview: value }} />
    </div>
  );
}
