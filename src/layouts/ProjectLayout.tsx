import { BookOpenText, Boxes, ChartNoAxesColumnIncreasing, ClipboardCheck, FileSearch, FileText, GitCompareArrows, LayoutDashboard, ListChecks, PackageCheck, PenTool, ScrollText, type LucideIcon } from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";
import { useProjectLoad } from "../app/providers/ProjectProvider";
import { ProjectStatus } from "../components/domain/ProjectStatus";
import { VersionContext } from "../components/domain/VersionContext";
import { ErrorState, LoadingState } from "../components/ui/PageState";

const projectNavigation: Array<{ label: string | null; items: Array<{ suffix: string; label: string; icon: LucideIcon; end?: boolean }> }> = [
  { label: null, items: [{ suffix: "", label: "项目总览", icon: LayoutDashboard, end: true }] },
  { label: "准备", items: [{ suffix: "/analysis", label: "招标分析", icon: FileSearch }, { suffix: "/writing-preparation", label: "事实与材料确认", icon: ListChecks }, { suffix: "/writing-plan", label: "标书结构", icon: ScrollText }] },
  { label: "生产", items: [{ suffix: "/fulltext", label: "标书正文", icon: BookOpenText }, { suffix: "/writing", label: "AI写作", icon: PenTool }] },
  { label: "质量", items: [{ suffix: "/review", label: "审核中心", icon: ClipboardCheck }, { suffix: "/scoring", label: "评分中心", icon: ChartNoAxesColumnIncreasing }, { suffix: "/facts-materials", label: "事实与材料", icon: Boxes }] },
  { label: "迭代", items: [{ suffix: "/revisions", label: "修改中心", icon: GitCompareArrows }, { suffix: "/versions", label: "版本记录", icon: ScrollText }] },
  { label: "交付", items: [{ suffix: "/release", label: "发布", icon: PackageCheck }] },
];

export function ProjectLayout() {
  const load = useProjectLoad();
  if (load.loading && !load.data) return <div className="p-6"><LoadingState /></div>;
  if (load.error) return <div className="p-6"><ErrorState error={load.error} onRetry={load.reload} /></div>;
  if (!load.data) return <div className="p-6"><LoadingState /></div>;
  const project = load.data;
  const base = `/projects/${encodeURIComponent(project.projectId)}`;
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b border-slate-200 bg-white/95 px-6 py-4 backdrop-blur">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-xs text-slate-400"><FileText size={13} /> 当前项目</div>
            <h1 className="mt-1 truncate text-lg font-semibold text-slate-900">{project.projectName}</h1>
            <div className="mt-2"><VersionContext current={project.currentRevision} reviewed={project.currentReviewedVersion} /></div>
          </div>
          <ProjectStatus workflowStatus={project.workflowStatus} releaseStatus={project.releaseStatus} />
        </div>
      </header>
      <div className="grid min-h-[calc(100vh-97px)] grid-cols-[220px_minmax(0,1fr)]">
        <aside className="border-r border-slate-200 bg-white p-3">
          <nav className="space-y-4" aria-label="项目导航">
            {projectNavigation.map((group, index) => (
              <div key={group.label ?? index}>
                {group.label ? <div className="mb-1 px-3 text-[11px] font-semibold uppercase tracking-wider text-slate-400">{group.label}</div> : null}
                <div className="space-y-1">
                  {group.items.map(({ suffix, label, icon: Icon, end }) => (
                    <NavLink key={suffix || "overview"} end={end} to={`${base}${suffix}`} className={({ isActive }) => `flex items-center gap-2.5 rounded-md px-3 py-2 text-sm ${isActive ? "bg-brand-50 font-medium text-brand-700" : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"}`}>
                      <Icon size={16} /> {label}
                    </NavLink>
                  ))}
                </div>
              </div>
            ))}
          </nav>
        </aside>
        <div className="min-w-0 p-6"><Outlet /></div>
      </div>
    </div>
  );
}
