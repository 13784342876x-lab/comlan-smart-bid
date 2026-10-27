import { AlertTriangle, CheckCircle2, Circle, LoaderCircle, RefreshCw, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError } from "../../api/client";
import { getProjectRun, resumeProjectRun } from "../../api/projects";
import { useProject } from "../../app/providers/ProjectProvider";
import { ErrorState, LoadingState } from "../../components/ui/PageState";
import { Panel } from "../../components/ui/Panel";
import type { PipelineRunDTO, PipelineStageDTO } from "../../types/api";
import { formatLocalDateTime } from "../../utils/dateTime";

export function PipelineProgressPage() {
  const project = useProject();
  const { runId = "" } = useParams();
  const [run, setRun] = useState<PipelineRunDTO | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [resuming, setResuming] = useState(false);

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const value = await getProjectRun(project.projectId, runId, signal);
      setRun(value);
      setError(null);
      return value;
    } catch (reason) {
      if (!(reason instanceof DOMException && reason.name === "AbortError")) setError(reason instanceof Error ? reason : new Error(String(reason)));
      return null;
    } finally {
      setLoading(false);
    }
  }, [project.projectId, runId]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  useEffect(() => {
    if (!run || (!run.full_bid_generation_active && !["QUEUED", "CREATED", "RUNNING"].includes(run.status))) return;
    const timer = window.setInterval(() => void load(), 3000);
    return () => window.clearInterval(timer);
  }, [load, run]);

  async function resume() {
    if (!run?.resume_available || resuming) return;
    setResuming(true);
    try {
      const value = await resumeProjectRun(project.projectId, run.run_id);
      setRun(value);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason : new Error("继续处理失败。"));
    } finally {
      setResuming(false);
    }
  }

  if (loading && !run) return <LoadingState label="正在恢复真实 Pipeline 状态…" />;
  if (error && !run) return <ErrorState error={error} onRetry={() => { setLoading(true); void load(); }} />;
  if (!run) return null;
  const active = run.full_bid_generation_active || ["QUEUED", "CREATED", "RUNNING"].includes(run.status);

  return (
    <div className="mx-auto max-w-4xl space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-4"><div><div className="text-xs font-medium text-brand-700">项目处理进度</div><h2 className="mt-1 text-xl font-semibold text-slate-950">{run.status === "FAILED" ? "处理未完成" : active ? run.current_phase : run.human_review_ready ? "技术标正文与审核包已就绪" : run.writing_plan_ready ? "写作规划已完成" : run.analysis_ready ? "招标分析已完成" : run.current_phase}</h2><p className="mt-1 text-sm text-slate-500">刷新或关闭浏览器不会重复已经完成的处理步骤。</p></div><button type="button" onClick={() => void load()} className="inline-flex items-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-2 text-xs font-medium text-slate-600"><RefreshCw size={13} />刷新状态</button></div>
      <Panel className="p-5">
        <div className="flex flex-wrap items-center justify-between gap-4"><div><div className="text-sm font-semibold text-slate-900">{run.status_label}</div><div className="mt-1 text-xs text-slate-500">{run.current_step ? `当前：${run.current_step}` : run.analysis_ready ? "全部招标分析步骤已完成" : "正在准备任务"}</div></div><div className="text-right"><div className="text-sm font-semibold text-slate-800">{run.completed_steps} / {run.total_steps} 步</div><div className="mt-1 text-xs text-slate-400">最近更新 {formatLocalDateTime(run.updated_at)}</div></div></div>
        {active ? <div className="mt-4 inline-flex items-center gap-2 text-xs font-medium text-brand-700"><LoaderCircle size={13} className="animate-spin" />等待下一次真实 checkpoint 更新</div> : null}
      </Panel>

      {run.error ? <Panel className="border-red-200 p-5"><div className="flex gap-3"><AlertTriangle className="mt-0.5 shrink-0 text-red-600" size={20} /><div><h3 className="font-semibold text-red-900">{run.error.user_message}</h3><p className="mt-1 text-sm text-red-700">失败阶段保留在真实 checkpoint 中，继续处理不会重复已经完成的阶段。</p>{run.error.technical_detail ? <details className="mt-3 rounded-md bg-red-50 px-3 py-2 text-xs text-red-700"><summary className="cursor-pointer font-medium">技术详情</summary><p className="mt-2 break-words font-mono">{run.error.technical_detail}</p></details> : null}</div></div></Panel> : null}

      <Panel title="处理步骤"><div className="divide-y divide-slate-100">{run.stages.map((stage) => <StageRow key={stage.stage_id} stage={stage} />)}</div></Panel>

      <div className="flex flex-wrap justify-end gap-3">
        {run.resume_available ? <button type="button" disabled={resuming} onClick={() => void resume()} className="inline-flex items-center gap-2 rounded-md bg-brand-700 px-4 py-2.5 text-sm font-medium text-white disabled:opacity-50"><RotateCcw size={15} />{resuming ? "正在继续…" : "继续处理"}</button> : null}
        {run.human_review_ready ? <Link to={`/projects/${encodeURIComponent(project.projectId)}/review`} className="inline-flex items-center gap-2 rounded-md bg-brand-700 px-4 py-2.5 text-sm font-medium text-white">进入人工审核</Link> : !active && run.fulltext_ready ? <Link to={`/projects/${encodeURIComponent(project.projectId)}/fulltext`} className="inline-flex items-center gap-2 rounded-md bg-brand-700 px-4 py-2.5 text-sm font-medium text-white">查看技术标正文</Link> : run.writing_plan_ready && !active ? <Link to={`/projects/${encodeURIComponent(project.projectId)}/writing-plan`} className="inline-flex items-center gap-2 rounded-md bg-brand-700 px-4 py-2.5 text-sm font-medium text-white">查看写作规划</Link> : run.analysis_ready && !active ? <Link to={`/projects/${encodeURIComponent(project.projectId)}/analysis`} className="inline-flex items-center gap-2 rounded-md bg-brand-700 px-4 py-2.5 text-sm font-medium text-white">查看招标分析</Link> : null}
      </div>
      {error && run ? <div className="text-sm text-red-600">状态刷新失败：{error.message}</div> : null}
    </div>
  );
}

function StageRow({ stage }: { stage: PipelineStageDTO }) {
  const Icon = stage.status === "completed" ? CheckCircle2 : stage.status === "running" ? LoaderCircle : stage.status === "failed" ? AlertTriangle : Circle;
  const color = stage.status === "completed" ? "text-emerald-600" : stage.status === "running" ? "animate-spin text-brand-600" : stage.status === "failed" ? "text-red-600" : "text-slate-300";
  return <div className="flex items-start gap-3 px-5 py-3.5"><Icon size={17} className={`mt-0.5 shrink-0 ${color}`} /><div className="min-w-0 flex-1"><div className={`text-sm font-medium ${stage.status === "pending" ? "text-slate-400" : "text-slate-800"}`}>{stage.label}</div>{stage.error ? <div className="mt-1 text-xs text-red-600">{stage.error.user_message}</div> : null}</div><div className="text-xs text-slate-400">{stage.status === "completed" ? "已完成" : stage.status === "running" ? "进行中" : stage.status === "failed" ? "失败" : "等待"}</div></div>;
}
