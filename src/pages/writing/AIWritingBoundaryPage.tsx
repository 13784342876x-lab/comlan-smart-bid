import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, CircleDashed, FileText, LoaderCircle, PenTool, RefreshCw, ShieldCheck } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { generateWritingTask, getAIWritingReadiness, getWritingGenerations, getWritingPlan } from "../../api/writingPlan";
import { ApiError } from "../../api/client";
import { useProject } from "../../app/providers/ProjectProvider";
import { ErrorState, LoadingState } from "../../components/ui/PageState";
import type { AIWritingReadinessDTO, WritingGenerationDTO, WritingPlanDTO, WritingTaskDTO } from "../../types/api";

const activeStatuses = new Set(["queued", "running", "validating"]);

export function AIWritingBoundaryPage() {
  const { projectId } = useProject();
  const [params, setParams] = useSearchParams();
  const [plan, setPlan] = useState<WritingPlanDTO | null>(null);
  const [readiness, setReadiness] = useState<AIWritingReadinessDTO | null>(null);
  const [generations, setGenerations] = useState<WritingGenerationDTO[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [starting, setStarting] = useState(false);

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    setError(null);
    try {
      const [nextPlan, nextReadiness, nextRuns] = await Promise.all([
        getWritingPlan(projectId), getAIWritingReadiness(projectId), getWritingGenerations(projectId),
      ]);
      setPlan(nextPlan); setReadiness(nextReadiness); setGenerations(nextRuns.generations);
    } catch (reason) {
      setError(reason instanceof Error ? reason : new Error(String(reason)));
    } finally { if (!silent) setLoading(false); }
  }, [projectId]);

  useEffect(() => { void load(); }, [load]);
  const hasActive = generations.some((item) => activeStatuses.has(item.status));
  useEffect(() => {
    if (!hasActive) return;
    const timer = window.setInterval(() => void load(true), 2500);
    return () => window.clearInterval(timer);
  }, [hasActive, load]);

  const task = useMemo(() => {
    if (!plan?.tasks.length) return null;
    return plan.tasks.find((item) => item.task_id === params.get("task"))
      ?? plan.tasks.find((item) => item.section_id === params.get("section"))
      ?? plan.tasks[0];
  }, [params, plan]);
  const generation = task ? generations.find((item) => item.task_id === task.task_id) ?? null : null;
  const selectTask = (next: WritingTaskDTO) => {
    const query = new URLSearchParams(params); query.set("task", next.task_id); query.set("section", next.section_id); setParams(query, { replace: true });
  };
  const start = async () => {
    if (!task || !readiness?.input_fingerprint) return;
    setStarting(true); setError(null);
    try {
      const created = await generateWritingTask(projectId, task.task_id, readiness.input_fingerprint);
      setGenerations((current) => [created, ...current]);
    } catch (reason) { setError(reason instanceof Error ? reason : new Error(String(reason))); }
    finally { setStarting(false); }
  };

  if (loading && !plan) return <LoadingState label="正在读取标书结构与写作上下文…" />;
  if (error && !plan) return <ErrorState error={error} onRetry={() => void load()} />;
  if (!plan || !readiness) return null;
  if (!plan.available) return <div className="mx-auto max-w-3xl rounded-lg border border-slate-200 bg-white p-8"><PenTool className="text-slate-500" size={24} /><h2 className="mt-4 text-xl font-semibold text-slate-950">AI 写作尚未就绪</h2><p className="mt-2 text-sm leading-6 text-slate-600">请先生成有效的标书结构，写作将严格使用已确认的规划与上下文。</p><Link to={`/projects/${encodeURIComponent(projectId)}/writing-plan`} className="mt-5 inline-flex rounded-md bg-brand-700 px-4 py-2.5 text-sm font-medium text-white">前往标书结构</Link></div>;

  const contractReady = readiness.stage9_contract_ready;
  const taskActive = Boolean(generation && activeStatuses.has(generation.status));
  const canGenerate = contractReady && Boolean(task) && !taskActive && !starting;
  return <div className="space-y-4">
    <header className="flex flex-wrap items-start justify-between gap-4"><div><div className="flex items-center gap-2 text-xs font-medium text-brand-700"><PenTool size={15} />AI 写作草稿</div><h2 className="mt-1 text-2xl font-semibold text-slate-950">AI 写作工作区</h2><p className="mt-2 text-sm text-slate-600">按标书小节生成草稿，完成后再进入全文审核。</p></div><div className="flex items-center gap-2 text-sm">{contractReady ? <CheckCircle2 className="text-emerald-600" size={17} /> : <AlertTriangle className="text-amber-600" size={17} />}<span className={contractReady ? "text-emerald-700" : "text-amber-700"}>{contractReady ? "写作输入已就绪" : "写作输入未就绪"}</span></div></header>
    {error ? <InlineError error={error} onRetry={() => void load()} /> : null}
    {!contractReady ? <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-950">{readiness.message} 当前规划与写作上下文不一致时，生成入口保持禁用。</div> : null}
    <section className="grid min-h-[690px] overflow-hidden rounded-lg border border-slate-200 bg-white xl:grid-cols-[270px_minmax(470px,1fr)_350px]">
      <aside className="border-r border-slate-200"><div className="sticky top-0 border-b border-slate-200 bg-slate-50 px-4 py-3 text-xs font-semibold text-slate-600">写作内容</div><div className="max-h-[760px] overflow-y-auto py-2">{plan.chapters.map((chapter) => <div key={chapter.chapter_id} className="mb-2"><div className="px-4 py-2 text-xs font-semibold text-slate-700">{chapter.title}</div>{chapter.task_ids.map((taskId) => { const item = plan.tasks.find((value) => value.task_id === taskId); if (!item) return null; const run = generations.find((value) => value.task_id === taskId); return <button key={taskId} type="button" onClick={() => selectTask(item)} className={`w-full border-l-2 px-4 py-2.5 text-left ${task?.task_id === taskId ? "border-brand-600 bg-brand-50" : "border-transparent hover:bg-slate-50"}`}><div className="line-clamp-2 text-xs font-medium leading-5 text-slate-700">{item.title}</div><div className="mt-1 text-[10px] text-slate-400">{run?.status_label ?? "尚未生成"}</div></button>; })}</div>)}</div></aside>
      <main className="min-w-0 border-r border-slate-200">{task ? <div><div className="border-b border-slate-200 px-6 py-4"><h3 className="text-lg font-semibold text-slate-950">{task.title}</h3><p className="mt-2 text-sm leading-6 text-slate-600">{task.objective}</p><div className="mt-4 flex flex-wrap items-center gap-3"><button type="button" disabled={!canGenerate} onClick={() => void start()} className="inline-flex items-center gap-2 rounded-md bg-brand-700 px-4 py-2.5 text-sm font-medium text-white disabled:cursor-not-allowed disabled:bg-slate-300">{starting || taskActive ? <LoaderCircle className="animate-spin" size={15} /> : <PenTool size={15} />}{taskActive ? "正在生成" : generation?.status === "completed" ? "重新生成本节" : "生成本节"}</button><button type="button" onClick={() => void load()} className="inline-flex items-center gap-2 rounded-md border border-slate-300 px-3 py-2.5 text-sm text-slate-600"><RefreshCw size={14} />刷新</button>{generation ? <span className="text-xs text-slate-500">{generation.status_label}</span> : null}</div></div><DraftArea generation={generation} /></div> : <div className="p-8 text-sm text-slate-500">当前规划没有可生成的写作内容。</div>}</main>
      <ContextPanel task={task} readiness={readiness} generation={generation} />
    </section>
  </div>;
}

function DraftArea({ generation }: { generation: WritingGenerationDTO | null }) {
  if (!generation) return <div className="flex min-h-[520px] flex-col items-center justify-center p-8 text-center"><FileText className="text-slate-300" size={34} /><div className="mt-3 font-medium text-slate-700">尚未生成本节草稿</div><div className="mt-1 text-sm text-slate-400">生成只会使用当前 Planning 和服务端重新解析的受约束上下文。</div></div>;
  if (activeStatuses.has(generation.status)) return <div className="p-6"><div className="rounded-md border border-blue-200 bg-blue-50 p-4"><div className="flex items-center gap-2 font-medium text-blue-900"><LoaderCircle className="animate-spin" size={17} />{generation.status_label}</div><div className="mt-2 text-sm text-blue-700">当前步骤：{generation.current_step ?? "准备中"}</div></div></div>;
  if (generation.status !== "completed") return <div className="p-6"><div className="rounded-md border border-red-200 bg-red-50 p-4"><div className="font-semibold text-red-900">{generation.status_label}</div><div className="mt-2 text-sm leading-6 text-red-700">{generation.error_message ?? generation.validation_errors[0] ?? "生成结果未通过 canonical 校验。"}</div>{generation.validation_errors.length ? <ul className="mt-3 list-disc space-y-1 pl-5 text-xs text-red-700">{generation.validation_errors.slice(0, 6).map((value) => <li key={value}>{value}</li>)}</ul> : null}</div></div>;
  return <div className="p-7"><div className="mb-4 flex flex-wrap items-center gap-3 text-xs text-slate-500"><span className="font-medium text-emerald-700">草稿已生成</span><span>{generation.placeholder_ids.length} 个待确认字段</span>{!generation.generated_from_current_context ? <span className="font-medium text-amber-700">基于较早写作上下文，建议重新生成</span> : null}</div><article className="whitespace-pre-wrap break-words text-[15px] leading-8 text-slate-800">{generation.draft_markdown}</article></div>;
}

function ContextPanel({ task, readiness, generation }: { task: WritingTaskDTO | null; readiness: AIWritingReadinessDTO; generation: WritingGenerationDTO | null }) {
  if (!task) return <aside className="bg-slate-50 p-5 text-sm text-slate-500">请选择一项写作内容。</aside>;
  const facts = task.fact_dependencies.filter((item) => item.availability === "available"); const pendingFacts = task.fact_dependencies.filter((item) => item.availability !== "available");
  return <aside className="max-h-[760px] overflow-y-auto bg-slate-50/70 p-5"><div className="flex items-center gap-2 text-xs font-semibold text-slate-500"><ShieldCheck size={14} />写作上下文</div><Context title="招标要求" values={task.requirement_refs.map((item) => item.title)} /><Context title="评分规则" values={task.score_refs.map((item) => `${item.title}${item.max_score == null ? "" : ` · ${item.max_score}分`}`)} /><Context title="已确认项目事实" values={facts.map((item) => `${item.title}：${item.value ?? "已确认"}`)} /><Context title="待确认事实 / 占位" values={pendingFacts.map((item) => item.title)} warning /><Context title="业务选择约束" values={task.human_confirmation_refs.map((item) => `${item.title}：${item.value ?? item.availability_label}`)} /><Context title="材料依赖" values={task.material_dependencies.map((item) => `${item.title}：${item.status_label}`)} warning={task.material_dependencies.some((item) => item.status === "query_not_executed")} /><Context title="禁止推断" values={task.prohibited_inference} warning /><details className="mt-6 border-t border-slate-200 pt-4 text-[11px] leading-5 text-slate-400"><summary className="cursor-pointer font-medium text-slate-500">技术详情</summary><div className="mt-2">输入指纹</div><div className="break-all font-mono">{readiness.input_fingerprint ?? "—"}</div>{generation ? <><div className="mt-2">生成指纹</div><div className="break-all font-mono">{generation.input_fingerprint}</div></> : null}</details></aside>;
}

function Context({ title, values, warning = false }: { title: string; values: string[]; warning?: boolean }) { return <section className="mt-5"><div className={`text-xs font-semibold ${warning ? "text-amber-700" : "text-slate-600"}`}>{title}</div>{values.length ? <div className="mt-2 space-y-2">{values.map((value, index) => <div key={`${value}-${index}`} className="rounded border border-slate-200 bg-white px-3 py-2 text-xs leading-5 text-slate-700">{value}</div>)}</div> : <div className="mt-2 flex items-center gap-2 text-xs text-slate-400"><CircleDashed size={13} />无直接依赖</div>}</section>; }
function InlineError({ error, onRetry }: { error: Error; onRetry: () => void }) { const stale = error instanceof ApiError && error.status === 409; return <div className="flex items-start gap-3 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"><AlertTriangle className="mt-0.5 shrink-0" size={17} /><div><div className="font-semibold">{stale ? "写作上下文已变化" : "操作未完成"}</div><div className="mt-1">{error.message}</div><button type="button" onClick={onRetry} className="mt-2 font-medium underline">刷新最新状态</button></div></div>; }
