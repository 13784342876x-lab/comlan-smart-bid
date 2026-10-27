import { CheckCircle2, FileArchive, FileText, Play, Trash2, UploadCloud } from "lucide-react";
import { useRef, useState } from "react";
import type { DragEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../../api/client";
import { deleteProjectFile, getProjectFiles, startProjectRun, updateProjectFileRole, uploadProjectFile } from "../../api/projects";
import { useApiQuery } from "../../api/useApiQuery";
import { useProject } from "../../app/providers/ProjectProvider";
import { EmptyState, ErrorState, LoadingState } from "../../components/ui/PageState";
import { Panel } from "../../components/ui/Panel";
import type { SourceFileDTO, SourceFileRole } from "../../types/api";

const roleLabels: Record<SourceFileRole, string> = {
  main_tender: "主招标文件",
  technical_spec: "技术参数 / 规范",
  scoring_document: "评分文件",
  attachment: "附件",
  supplement: "补充 / 澄清文件",
  unknown: "待确认",
};

export function TenderImportPage() {
  const project = useProject();
  const navigate = useNavigate();
  const query = useApiQuery((signal) => getProjectFiles(project.projectId, signal), [project.projectId]);
  const inputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState<Record<string, "uploading" | "success" | "error">>({});
  const [busyFile, setBusyFile] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);

  async function upload(files: File[]) {
    if (!files.length) return;
    setError(null);
    for (const file of files) {
      setUploading((current) => ({ ...current, [file.name]: "uploading" }));
      try {
        await uploadProjectFile(project.projectId, file);
        setUploading((current) => ({ ...current, [file.name]: "success" }));
      } catch (reason) {
        setUploading((current) => ({ ...current, [file.name]: "error" }));
        setError(reason instanceof ApiError ? `${file.name}：${reason.message}` : `${file.name} 上传失败。`);
      }
    }
    query.reload();
  }

  async function updateRole(file: SourceFileDTO, role: SourceFileRole) {
    setBusyFile(file.file_id);
    setError(null);
    try {
      await updateProjectFileRole(project.projectId, file.file_id, role);
      query.reload();
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "文件角色更新失败。");
    } finally {
      setBusyFile(null);
    }
  }

  async function remove(file: SourceFileDTO) {
    if (!window.confirm(`移除“${file.display_name}”？`)) return;
    setBusyFile(file.file_id);
    try {
      await deleteProjectFile(project.projectId, file.file_id);
      query.reload();
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "文件移除失败。");
    } finally {
      setBusyFile(null);
    }
  }

  async function start() {
    if (!query.data?.can_start || starting) return;
    setStarting(true);
    setError(null);
    try {
      const run = await startProjectRun(project.projectId);
      navigate(`/projects/${encodeURIComponent(project.projectId)}/runs/${encodeURIComponent(run.run_id)}`);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "招标分析任务启动失败。");
      setStarting(false);
    }
  }

  function drop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    void upload(Array.from(event.dataTransfer.files));
  }

  if (query.loading && !query.data) return <LoadingState label="正在读取项目资料…" />;
  if (query.error) return <ErrorState error={query.error} onRetry={query.reload} />;
  if (!query.data) return <EmptyState title="无法读取招标资料" description="请刷新页面后重试。" />;
  const data = query.data;

  return (
    <div className="space-y-5">
      <div><div className="text-xs font-medium text-brand-700">项目准备</div><h2 className="mt-1 text-xl font-semibold text-slate-950">导入并确认招标资料</h2><p className="mt-1 text-sm text-slate-500">文件上传不会自动启动分析。确认主招标文件后，由你明确开始 Pipeline。</p></div>
      <div className="grid grid-cols-4 gap-2 text-xs"><Step done label="1 项目" /><Step active label="2 招标资料" /><Step active={data.files.length > 0} label="3 文件确认" /><Step active={data.can_start} label="4 开始分析" /></div>
      <Panel className="p-5">
        <div onDragEnter={(event) => { event.preventDefault(); setDragging(true); }} onDragOver={(event) => event.preventDefault()} onDragLeave={() => setDragging(false)} onDrop={drop} className={`flex min-h-40 flex-col items-center justify-center rounded-lg border-2 border-dashed px-6 text-center ${dragging ? "border-brand-500 bg-brand-50" : "border-slate-300 bg-slate-50/60"}`}>
          <UploadCloud size={28} className="text-brand-600" /><div className="mt-3 font-medium text-slate-800">拖入招标文件，或选择多个文件</div><p className="mt-1 text-xs text-slate-500">实际支持：{data.allowed_extensions.map((value) => value.toUpperCase()).join("、")} · 单文件上限 {formatSize(data.max_file_bytes)}</p>
          <button type="button" onClick={() => inputRef.current?.click()} className="mt-4 rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50">选择文件</button>
          <input ref={inputRef} type="file" multiple className="hidden" accept={data.allowed_extensions.map((value) => `.${value}`).join(",")} onChange={(event) => { void upload(Array.from(event.target.files ?? [])); event.currentTarget.value = ""; }} />
        </div>
        {Object.keys(uploading).length ? <div className="mt-3 flex flex-wrap gap-2">{Object.entries(uploading).map(([name, status]) => <span key={name} className={`text-xs ${status === "error" ? "text-red-600" : status === "success" ? "text-emerald-700" : "text-brand-700"}`}>{name} · {status === "uploading" ? "上传中" : status === "success" ? "已上传" : "失败"}</span>)}</div> : null}
      </Panel>

      <Panel title={`已上传资料 · ${data.files.length}`}>
        {data.files.length ? <div className="divide-y divide-slate-100">{data.files.map((file) => <FileRow key={file.file_id} file={file} busy={busyFile === file.file_id} onRole={(role) => void updateRole(file, role)} onRemove={() => void remove(file)} />)}</div> : <div className="p-5"><EmptyState title="尚未上传招标资料" description="可以一次选择主招标文件、技术参数、评分办法和相关附件。" /></div>}
      </Panel>

      <Panel className="p-5">
        <div className="flex flex-wrap items-start justify-between gap-5"><div><h3 className="font-semibold text-slate-900">开始招标分析</h3><p className="mt-1 text-sm text-slate-500">将创建独立 Pipeline Run，并执行到“招标分析完成”检查点。不会自动进入全文写作。</p>{data.start_blockers.length ? <ul className="mt-3 space-y-1 text-sm text-amber-700">{data.start_blockers.map((item) => <li key={item}>· {item}</li>)}</ul> : <div className="mt-3 inline-flex items-center gap-2 text-sm text-emerald-700"><CheckCircle2 size={16} />资料与主文件已确认</div>}</div><button type="button" disabled={!data.can_start || starting} onClick={() => void start()} className="inline-flex items-center gap-2 rounded-md bg-brand-700 px-4 py-2.5 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"><Play size={15} />{starting ? "正在创建任务…" : "开始招标分析"}</button></div>
        {error ? <div className="mt-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div> : null}
      </Panel>
    </div>
  );
}

function FileRow({ file, busy, onRole, onRemove }: { file: SourceFileDTO; busy: boolean; onRole: (role: SourceFileRole) => void; onRemove: () => void }) {
  const Icon = file.extension === "zip" ? FileArchive : FileText;
  return <div className="grid grid-cols-[minmax(260px,1fr)_160px_220px_40px] items-center gap-4 px-5 py-4"><div className="flex min-w-0 items-start gap-3"><div className="rounded-md bg-slate-100 p-2 text-slate-500"><Icon size={17} /></div><div className="min-w-0"><div className="truncate text-sm font-medium text-slate-800">{file.display_name}</div><div className="mt-1 text-xs text-slate-400">{file.extension.toUpperCase()} · {formatSize(file.size)} · 上传完成</div></div></div><div className="text-xs text-slate-500">{file.role_confirmed ? <span className="text-emerald-700">已确认角色</span> : <button type="button" disabled={busy} onClick={() => onRole(file.role)} className="font-medium text-amber-700 underline decoration-amber-300 underline-offset-2 disabled:opacity-40">确认系统建议</button>}</div><select disabled={busy} value={file.role} onChange={(event) => onRole(event.target.value as SourceFileRole)} className="h-9 rounded-md border border-slate-300 bg-white px-3 text-sm text-slate-700 disabled:opacity-50">{Object.entries(roleLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><button type="button" disabled={busy} onClick={onRemove} title="移除文件" className="rounded-md p-2 text-slate-400 hover:bg-red-50 hover:text-red-600 disabled:opacity-40"><Trash2 size={16} /></button></div>;
}

function Step({ label, active = false, done = false }: { label: string; active?: boolean; done?: boolean }) {
  return <div className={`rounded-md border px-3 py-2 text-center font-medium ${done ? "border-emerald-200 bg-emerald-50 text-emerald-700" : active ? "border-brand-300 bg-brand-50 text-brand-700" : "border-slate-200 bg-white text-slate-400"}`}>{label}</div>;
}

function formatSize(value: number) {
  if (value >= 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(value / 1024))} KB`;
}
