import { FolderKanban, MoreHorizontal, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { deleteProject, getProjects } from "../../api/projects";
import { useApiQuery } from "../../api/useApiQuery";
import { ProjectStatus } from "../../components/domain/ProjectStatus";
import { EmptyState, ErrorState, LoadingState } from "../../components/ui/PageState";
import { Panel } from "../../components/ui/Panel";
import type { ProjectSummaryDTO } from "../../types/api";
import { formatLocalDateTime } from "../../utils/dateTime";
import { projectBusinessIdentity } from "../../utils/projectPresentation";
import { activatesProjectCard, projectOverviewTarget, withoutProject } from "./projectCenterViewModel";

export function ProjectCenterPage() {
  const query = useApiQuery((signal) => getProjects(signal), []);
  const navigate = useNavigate();
  const [deletedIds, setDeletedIds] = useState<string[]>([]);
  const [menuProjectId, setMenuProjectId] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ProjectSummaryDTO | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const projects = deletedIds.reduce(withoutProject, query.data?.projects ?? []);

  async function confirmDelete() {
    if (!deleteTarget || deleting) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteProject(deleteTarget.project_id);
      setDeletedIds((current) => [...current, deleteTarget.project_id]);
      setDeleteTarget(null);
      setMenuProjectId(null);
    } catch (reason) {
      setDeleteError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="mx-auto max-w-6xl p-8">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4"><div><div className="text-sm font-medium text-brand-700">项目中心</div><h1 className="mt-1 text-2xl font-semibold">投标项目</h1><p className="mt-2 text-sm text-slate-500">选择项目查看当前阶段、审核进度和下一步工作。</p></div><Link to="/projects/new" className="inline-flex items-center gap-2 rounded-md bg-brand-700 px-4 py-2.5 text-sm font-medium text-white"><Plus size={16} />新建投标项目</Link></div>
      {query.loading ? <LoadingState label="正在读取项目目录…" /> : null}
      {query.error ? <ErrorState error={query.error} onRetry={query.reload} /> : null}
      {query.data && projects.length === 0 ? <EmptyState title="还没有投标项目" description="新建项目后即可上传真实招标资料并启动分析。" /> : null}
      <div className="space-y-4">
        {projects.map((project) => {
          const target = projectOverviewTarget(project.project_id);
          const identity = projectBusinessIdentity(project);
          return (
            <div
              key={project.project_id}
              role="link"
              tabIndex={0}
              aria-label={`打开项目总览：${project.project_name}`}
              onClick={() => navigate(target)}
              onKeyDown={(event) => { if (event.target === event.currentTarget && activatesProjectCard(event.key)) { event.preventDefault(); navigate(target); } }}
              className="cursor-pointer rounded-xl outline-none transition-transform hover:-translate-y-0.5 focus-visible:ring-2 focus-visible:ring-brand-500"
            >
              <Panel className="p-5 hover:border-brand-200 hover:shadow-md">
                <div className="flex items-start justify-between gap-5">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 text-xs text-slate-400"><FolderKanban size={14} />投标项目</div>
                    <h2 className="mt-2 text-lg font-semibold text-slate-950">{project.project_name}</h2>
                    <div className="mt-3 grid gap-x-6 gap-y-2 text-sm text-slate-600 sm:grid-cols-2">
                      <Info label="投标主题" value={identity.bidSubject} />
                      <Info label="供应商 / 投标人" value={identity.supplierName} />
                    </div>
                  </div>
                  <div className="relative flex shrink-0 items-start gap-4">
                    <div className="text-right"><ProjectStatus workflowStatus={project.workflow_status} releaseStatus={project.release_status} />{project.updated_at ? <div className="mt-2 text-xs text-slate-400">更新于 {formatLocalDateTime(project.updated_at)}</div> : null}</div>
                    <button type="button" aria-label={`项目操作：${project.project_name}`} onClick={(event) => { event.stopPropagation(); setMenuProjectId((current) => current === project.project_id ? null : project.project_id); }} onKeyDown={(event) => event.stopPropagation()} className="rounded-md p-2 text-slate-400 hover:bg-slate-100 hover:text-slate-700"><MoreHorizontal size={18} /></button>
                    {menuProjectId === project.project_id ? <div className="absolute right-0 top-10 z-10 w-36 rounded-lg border border-slate-200 bg-white p-1 shadow-lg"><button type="button" onClick={(event) => { event.stopPropagation(); setDeleteError(null); setDeleteTarget(project); setMenuProjectId(null); }} className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm text-red-700 hover:bg-red-50"><Trash2 size={14} />删除项目</button></div> : null}
                  </div>
                </div>
              </Panel>
            </div>
          );
        })}
      </div>
      {deleteTarget ? <div role="presentation" onClick={() => { if (!deleting) setDeleteTarget(null); }} className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/35 p-5"><div role="dialog" aria-modal="true" aria-labelledby="delete-project-title" onClick={(event) => event.stopPropagation()} className="w-full max-w-md rounded-xl bg-white p-6 shadow-2xl"><h2 id="delete-project-title" className="text-lg font-semibold text-slate-950">删除项目？</h2><p className="mt-3 font-medium text-slate-800">“{deleteTarget.project_name}”</p><p className="mt-2 text-sm leading-6 text-slate-600">删除后，该项目将从项目中心移除，其项目专属处理产物也会被删除。此操作不可撤销。</p>{deleteError ? <div className="mt-4 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{deleteError}</div> : null}<div className="mt-6 flex justify-end gap-3"><button type="button" disabled={deleting} onClick={() => setDeleteTarget(null)} className="rounded-md border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 disabled:opacity-50">取消</button><button type="button" disabled={deleting} onClick={() => void confirmDelete()} className="rounded-md bg-red-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">{deleting ? "正在删除…" : "确认删除"}</button></div></div></div> : null}
    </div>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return <div><span className="text-xs text-slate-400">{label}</span><div className="mt-0.5 line-clamp-2">{value}</div></div>;
}
