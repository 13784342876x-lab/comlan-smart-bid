import { useState } from "react";
import type { ReactNode } from "react";
import {
  AlertTriangle,
  ArrowRight,
  BookOpenCheck,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  ClipboardList,
  FileQuestion,
  LibraryBig,
  ListTree,
  PanelRightClose,
  PanelRightOpen,
  ShieldAlert,
} from "lucide-react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { generateWritingPlan, getWritingPlan } from "../../api/writingPlan";
import { useApiQuery } from "../../api/useApiQuery";
import { useProject } from "../../app/providers/ProjectProvider";
import { EmptyState, ErrorState, LoadingState } from "../../components/ui/PageState";
import type {
  WritingChapterDTO,
  WritingContextItemDTO,
  WritingPlanCoverageDTO,
  WritingPlanFactDependencyDTO,
  WritingPlanMaterialDependencyDTO,
  WritingSectionDTO,
  WritingTaskDTO,
} from "../../types/api";
import { formatLocalDateTime } from "../../utils/dateTime";
import { technicalCoverageSummary } from "./writingPlanViewModel";

type ViewKey = "structure" | "requirements" | "scores";

const materialLabels: Record<string, string> = {
  product_certificate: "产品认证证明",
  product_parameter_proof: "产品参数证明",
  product_test_report: "产品检测报告",
  manufacturer_official_document: "制造商正式文件",
  green_supply_chain_evidence: "绿色供应链证明",
  service_network: "服务机构与服务网络材料",
  service_commitment: "服务承诺材料",
  training_material: "培训材料",
};

export function WritingPlanPage() {
  const { projectId } = useProject();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [contextOpen, setContextOpen] = useState(true);
  const query = useApiQuery((signal) => getWritingPlan(projectId, signal), [projectId]);
  const view = (params.get("view") === "requirements" || params.get("view") === "scores" ? params.get("view") : "structure") as ViewKey;

  if (query.loading && !query.data) return <LoadingState label="正在读取正式写作规划与当前 Writing Context…" />;
  if (query.error) return <ErrorState error={query.error} onRetry={query.reload} />;
  if (!query.data) return null;
  const data = query.data;
  if (!data.available) return <NoPlanState projectId={projectId} message={data.message} reason={data.generation_reason} canGenerate={data.generation_available} onStarted={(runId) => navigate(`/projects/${encodeURIComponent(projectId)}/runs/${encodeURIComponent(runId)}`)} />;

  const taskById = new Map(data.tasks.map((item) => [item.task_id, item]));
  const selectedTask = taskById.get(params.get("task") ?? "") ?? null;
  const sectionId = selectedTask?.section_id ?? params.get("section");
  const sectionFromUrl = data.chapters.flatMap((item) => item.sections).find((item) => item.section_id === sectionId) ?? null;
  const chapterId = selectedTask?.chapter_id ?? sectionFromUrl?.chapter_id ?? params.get("chapter") ?? data.chapters[0]?.chapter_id ?? null;
  const selectedChapter = data.chapters.find((item) => item.chapter_id === chapterId) ?? data.chapters[0] ?? null;
  const selectedSection = sectionFromUrl;
  const visibleTasks = selectedTask ? [selectedTask] : selectedSection ? selectedSection.task_ids.map((id) => taskById.get(id)).filter(isDefined) : selectedChapter ? selectedChapter.task_ids.map((id) => taskById.get(id)).filter(isDefined) : [];
  const focusTask = selectedTask ?? visibleTasks[0] ?? null;

  const updateSelection = (next: { chapter?: string; section?: string; task?: string; view?: ViewKey }) => {
    const value = new URLSearchParams(params);
    for (const key of ["chapter", "section", "task"] as const) value.delete(key);
    if (next.chapter) value.set("chapter", next.chapter);
    if (next.section) value.set("section", next.section);
    if (next.task) value.set("task", next.task);
    value.set("view", next.view ?? "structure");
    setParams(value, { replace: true });
  };

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 text-xs font-medium text-brand-700"><BookOpenCheck size={15} />当前标书结构</div>
          <h2 className="mt-1 text-2xl font-semibold text-slate-950">标书结构</h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-600">{data.message}</p>
        </div>
        <Link to={`/projects/${encodeURIComponent(projectId)}/writing`} className="inline-flex items-center gap-2 rounded-md border border-slate-300 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-50">查看 AI 写作边界 <ArrowRight size={15} /></Link>
      </header>

      {data.status !== "ready" ? <div className="flex gap-3 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950"><AlertTriangle className="mt-0.5 shrink-0" size={18} /><div><div className="font-semibold">{data.status_label}</div><div className="mt-1 leading-6">{data.lineage.stale_reason ?? data.message}</div></div></div> : null}

      <div className="grid overflow-hidden rounded-lg border border-slate-200 bg-white sm:grid-cols-3 xl:grid-cols-6">
        <Summary label="章节 / 小节" value={`${data.summary.chapter_count} / ${data.summary.section_count}`} />
        <Summary label="写作事项" value={data.summary.task_count} />
        <Summary label="技术要求" value={data.summary.requirement_count} detail={data.summary.unassigned_requirement_count ? `${data.summary.unassigned_requirement_count} 条待覆盖` : "均已覆盖"} tone={data.summary.unassigned_requirement_count ? "warning" : "success"} />
        <Summary label="评分规则" value={data.summary.score_count} />
        <Summary label="待确认事实" value={data.summary.placeholder_fact_count} detail="写作时使用待确认字段" tone="warning" />
        <Summary label="尚未查询材料" value={data.summary.query_not_executed_count} detail="未知，不代表不存在" tone="warning" />
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200">
        <div className="flex gap-1">
          <ViewTab active={view === "structure"} onClick={() => updateSelection({ chapter: selectedChapter?.chapter_id, view: "structure" })}>标书结构</ViewTab>
          <ViewTab active={view === "requirements"} onClick={() => updateSelection({ view: "requirements" })}>技术要求覆盖</ViewTab>
          <ViewTab active={view === "scores"} onClick={() => updateSelection({ view: "scores" })}>评分覆盖</ViewTab>
        </div>
        <details className="pb-2 text-xs text-slate-500"><summary className="cursor-pointer font-medium text-slate-600">技术详情</summary><div className="absolute right-6 z-10 mt-2 w-[460px] rounded-md border border-slate-200 bg-white p-4 shadow-lg"><LineageDetails data={data.lineage} /></div></details>
      </div>

      {view === "requirements" ? <CoverageView title="技术要求覆盖" values={data.requirement_coverage} sectionTitles={new Map(data.chapters.flatMap((chapter) => chapter.sections.map((section) => [section.section_id, section.title])))} onSection={(id) => updateSelection({ section: id })} /> : null}
      {view === "scores" ? <CoverageView title="评分项覆盖情况" values={data.score_coverage} sectionTitles={new Map(data.chapters.flatMap((chapter) => chapter.sections.map((section) => [section.section_id, section.title])))} onSection={(id) => updateSelection({ section: id })} showScore /> : null}
      {view === "structure" ? (
        <section className={`grid min-h-[680px] overflow-hidden rounded-lg border border-slate-200 bg-white ${contextOpen ? "xl:grid-cols-[286px_minmax(460px,1fr)_340px]" : "xl:grid-cols-[286px_minmax(0,1fr)]"}`}>
          <WritingPlanTree chapters={data.chapters} activeChapter={selectedChapter?.chapter_id ?? null} activeSection={selectedSection?.section_id ?? null} onChapter={(id) => updateSelection({ chapter: id })} onSection={(chapter, section) => updateSelection({ chapter, section })} />
          <main className="min-w-0 border-r border-slate-200">
            <div className="flex justify-end border-b border-slate-100 px-4 py-2 xl:hidden"><button type="button" onClick={() => setContextOpen((value) => !value)} className="text-xs font-medium text-brand-700">{contextOpen ? "收起上下文" : "展开上下文"}</button></div>
            {selectedSection ? <SectionDetail projectId={projectId} section={selectedSection} tasks={visibleTasks} selectedTaskId={selectedTask?.task_id ?? null} onTask={(task) => updateSelection({ chapter: task.chapter_id, section: task.section_id, task: task.task_id })} /> : selectedChapter ? <ChapterDetail chapter={selectedChapter} tasks={visibleTasks} onSection={(id) => updateSelection({ chapter: selectedChapter.chapter_id, section: id })} /> : <EmptyState title="没有可展示的结构" description="当前规划尚未包含章节。" />}
          </main>
          {contextOpen ? <aside className="relative min-w-0 bg-slate-50/60"><button type="button" onClick={() => setContextOpen(false)} className="absolute right-3 top-3 hidden rounded p-1.5 text-slate-400 hover:bg-white hover:text-slate-700 xl:block" aria-label="收起写作上下文"><PanelRightClose size={17} /></button><WritingContextPanel projectId={projectId} task={focusTask} allContext={data.writing_context} /></aside> : <button type="button" onClick={() => setContextOpen(true)} className="absolute right-7 mt-3 hidden rounded-md border border-slate-200 bg-white p-2 text-slate-500 shadow-sm xl:block" aria-label="展开写作上下文"><PanelRightOpen size={18} /></button>}
        </section>
      ) : null}
    </div>
  );
}

function NoPlanState({ projectId, message, reason, canGenerate, onStarted }: { projectId: string; message: string; reason: string | null; canGenerate: boolean; onStarted: (runId: string) => void }) {
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const start = async () => { setStarting(true); setError(null); try { const run = await generateWritingPlan(projectId); onStarted(run.run_id); } catch (value) { setError(value instanceof Error ? value.message : String(value)); setStarting(false); } };
  return <div className="mx-auto max-w-3xl py-12"><div className="rounded-lg border border-slate-200 bg-white p-8"><div className="flex h-11 w-11 items-center justify-center rounded-md bg-slate-100 text-slate-600"><ClipboardList size={22} /></div><h2 className="mt-5 text-xl font-semibold text-slate-950">尚未生成写作规划</h2><p className="mt-3 text-sm leading-7 text-slate-600">{message}</p>{reason ? <div className="mt-4 rounded-md border border-blue-200 bg-blue-50 px-4 py-3 text-sm leading-6 text-blue-950">{reason}</div> : null}{error ? <div className="mt-4 text-sm text-red-700">{error}</div> : null}<div className="mt-6 flex flex-wrap gap-3">{canGenerate ? <button type="button" disabled={starting} onClick={() => void start()} className="rounded-md bg-brand-700 px-4 py-2.5 text-sm font-medium text-white disabled:bg-slate-300">{starting ? "正在启动…" : "生成写作规划"}</button> : null}<Link to={`/projects/${encodeURIComponent(projectId)}/analysis`} className="rounded-md border border-slate-300 px-4 py-2.5 text-sm font-medium text-slate-700">返回招标分析</Link><Link to={`/projects/${encodeURIComponent(projectId)}/writing-preparation`} className="rounded-md border border-slate-300 px-4 py-2.5 text-sm font-medium text-slate-700">查看写作准备</Link></div></div></div>;
}

function WritingPlanTree({ chapters, activeChapter, activeSection, onChapter, onSection }: { chapters: WritingChapterDTO[]; activeChapter: string | null; activeSection: string | null; onChapter: (id: string) => void; onSection: (chapter: string, section: string) => void }) {
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());
  const toggle = (id: string) => setCollapsed((current) => { const next = new Set(current); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  return <aside className="border-r border-slate-200"><div className="sticky top-0 z-10 flex items-center gap-2 border-b border-slate-200 bg-slate-50 px-4 py-3 text-xs font-semibold text-slate-600"><ListTree size={15} />标书结构</div><div className="max-h-[760px] overflow-y-auto py-2">{chapters.map((chapter) => <div key={chapter.chapter_id}><div className={`group flex items-center gap-1 px-2 ${activeChapter === chapter.chapter_id && !activeSection ? "bg-brand-50" : ""}`}><button type="button" onClick={() => toggle(chapter.chapter_id)} className="rounded p-1 text-slate-400 hover:bg-slate-100">{collapsed.has(chapter.chapter_id) ? <ChevronRight size={14} /> : <ChevronDown size={14} />}</button><button type="button" onClick={() => onChapter(chapter.chapter_id)} className="min-w-0 flex-1 py-2 pr-2 text-left"><div className="truncate text-sm font-medium text-slate-800">{chapter.title}</div><div className="mt-0.5 text-[11px] text-slate-400">{chapter.sections.length} 节 · {chapter.requirement_count} 条要求</div></button></div>{!collapsed.has(chapter.chapter_id) ? <div>{chapter.sections.filter((item) => item.section_id !== chapter.chapter_id).map((section) => <button key={section.section_id} type="button" onClick={() => onSection(chapter.chapter_id, section.section_id)} style={{ paddingLeft: `${30 + Math.max(0, section.depth - 1) * 14}px` }} className={`block w-full border-l-2 py-2 pr-3 text-left ${activeSection === section.section_id ? "border-brand-600 bg-brand-50 text-brand-800" : "border-transparent text-slate-600 hover:bg-slate-50"}`}><div className="line-clamp-2 text-xs font-medium leading-5">{section.title}</div><div className="text-[10px] text-slate-400">{section.task_ids.length ? `${section.task_ids.length} 项写作内容` : "尚未安排写作内容"}</div></button>)}</div> : null}</div>)}</div></aside>;
}

function ChapterDetail({ chapter, tasks, onSection }: { chapter: WritingChapterDTO; tasks: WritingTaskDTO[]; onSection: (id: string) => void }) {
  return <div className="p-6"><h3 className="text-xl font-semibold text-slate-950">{chapter.title}</h3><p className="mt-3 max-w-3xl text-sm leading-7 text-slate-600">{chapter.purpose}</p><div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4"><MiniStat label="小节" value={chapter.sections.length} /><MiniStat label="招标要求" value={chapter.requirement_count} /><MiniStat label="评分项" value={chapter.score_count} /><MiniStat label="待确认 / 待准备" value={chapter.pending_fact_count + chapter.pending_material_count} /></div><section className="mt-7"><h4 className="text-sm font-semibold text-slate-900">本章小节规划</h4><div className="mt-3 divide-y divide-slate-100 border-y border-slate-100">{chapter.sections.map((section) => <button type="button" key={section.section_id} onClick={() => onSection(section.section_id)} className="flex w-full items-start justify-between gap-4 py-3 text-left hover:bg-slate-50"><div><div className="text-sm font-medium text-slate-800">{section.title}</div><div className="mt-1 text-xs text-slate-400">{section.preparation_label}</div></div><div className="shrink-0 text-xs text-brand-700">查看</div></button>)}</div></section>{tasks.length ? <div className="mt-5 text-xs text-slate-500">本章共 {tasks.length} 项写作内容；选择小节查看招标要求、评分和上下文边界。</div> : null}</div>;
}

function SectionDetail({ projectId, section, tasks, selectedTaskId, onTask }: { projectId: string; section: WritingSectionDTO; tasks: WritingTaskDTO[]; selectedTaskId: string | null; onTask: (task: WritingTaskDTO) => void }) {
  const task = tasks.find((item) => item.task_id === selectedTaskId) ?? tasks[0] ?? null;
  return <div className="p-6"><div className="flex flex-wrap items-start justify-between gap-3"><div><div className="text-xs font-medium text-brand-700">{section.path.join(" / ")}</div><h3 className="mt-1 text-xl font-semibold text-slate-950">{section.title}</h3></div><span className="text-xs font-medium text-amber-700">{section.preparation_label}</span></div><DetailSection title="这一节为什么存在"><p className="text-sm leading-7 text-slate-700">{section.purpose}</p></DetailSection>{tasks.length > 1 ? <DetailSection title="写作内容"><div className="flex flex-wrap gap-2">{tasks.map((item) => <button type="button" key={item.task_id} onClick={() => onTask(item)} className={`rounded-md border px-3 py-2 text-xs font-medium ${task?.task_id === item.task_id ? "border-brand-300 bg-brand-50 text-brand-800" : "border-slate-200 text-slate-600"}`}>{item.title || item.objective}</button>)}</div></DetailSection> : null}{task ? <TaskDetail projectId={projectId} task={task} /> : <EmptyState title="尚未安排写作内容" description="当前小节还没有可展示的写作安排。" />}</div>;
}

function TaskDetail({ projectId, task }: { projectId: string; task: WritingTaskDTO }) {
  return <div><DetailSection title="写作安排"><div className="rounded-md border border-slate-200 bg-slate-50 p-4"><div className="flex items-center justify-between gap-3"><span className="text-sm font-semibold text-brand-700">{task.title}</span><span className="text-xs font-medium text-slate-500">{task.readiness_label}</span></div><p className="mt-3 text-sm leading-7 text-slate-700">{task.objective}</p>{task.instruction_points.length ? <ul className="mt-3 space-y-2 text-sm leading-6 text-slate-600">{task.instruction_points.map((value, index) => <li key={`${value}-${index}`} className="flex gap-2"><span className="text-slate-300">{index + 1}.</span><span className="line-clamp-5">{value}</span></li>)}</ul> : null}</div></DetailSection><DetailSection title={`本节负责响应 · ${task.requirement_refs.length}`}>{task.requirement_refs.length ? <TraceLinks values={task.requirement_refs} /> : <Muted>当前没有关联的招标要求。</Muted>}</DetailSection><DetailSection title={`关联招标评分规则 · ${task.score_refs.length}`}>{task.score_refs.length ? <TraceLinks values={task.score_refs} showScore /> : <Muted>没有直接评分关系。</Muted>}</DetailSection><DetailSection title="禁止推断 / 事实边界">{task.prohibited_inference.length ? <div className="space-y-2">{task.prohibited_inference.map((value) => <div key={value} className="flex gap-2 rounded-md bg-amber-50 px-3 py-2 text-sm leading-6 text-amber-950"><ShieldAlert className="mt-1 shrink-0" size={14} />{value}</div>)}</div> : <div className="flex items-center gap-2 text-sm text-emerald-700"><CheckCircle2 size={15} />当前没有额外待确认事实边界。</div>}</DetailSection><div className="mt-6 flex flex-wrap gap-2"><Link to={`/projects/${encodeURIComponent(projectId)}/writing-preparation`} className="rounded-md border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700">管理写作上下文</Link></div></div>;
}

function WritingContextPanel({ projectId, task, allContext }: { projectId: string; task: WritingTaskDTO | null; allContext: WritingContextItemDTO[] }) {
  const available = task?.fact_dependencies.filter((item) => item.availability === "available") ?? [];
  const pending = task?.fact_dependencies.filter((item) => item.availability !== "available") ?? [];
  const materials = task?.material_dependencies ?? [];
  const confirmations = allContext.filter((item) => item.availability === "pending");
  return <div className="max-h-[760px] overflow-y-auto p-5"><div className="pr-8"><div className="text-xs font-semibold uppercase tracking-wide text-slate-400">当前 AI 写作上下文</div><h3 className="mt-1 font-semibold text-slate-900">{task?.title ?? "选择一项写作内容"}</h3><p className="mt-1 text-xs leading-5 text-slate-500">这里展示当前可用于写作的最新项目事实与材料。</p></div><ContextSection title="可直接使用" icon={CheckCircle2} tone="success">{available.length ? available.map((item) => <FactLine key={item.fact_id} value={item} />) : <Muted>当前没有已确认项目事实。</Muted>}</ContextSection><ContextSection title="待确认 / 使用待确认字段" icon={CircleHelp} tone="warning">{pending.length ? pending.map((item) => <FactLine key={item.fact_id} value={item} />) : <Muted>当前没有待确认事实。</Muted>}</ContextSection><ContextSection title="证明材料" icon={LibraryBig} tone="neutral">{materials.length ? materials.map((item) => <MaterialLine key={item.dependency_id} value={item} />) : <Muted>当前没有材料依赖。</Muted>}</ContextSection>{confirmations.length ? <ContextSection title="项目级人工确认" icon={FileQuestion} tone="warning">{confirmations.map((item) => <div key={item.context_id} className="border-b border-slate-200 py-2.5 last:border-b-0"><div className="text-sm font-medium text-slate-800">{item.title}</div><div className="mt-1 text-xs leading-5 text-amber-700">待人工确认，不进入确定事实上下文</div></div>)}</ContextSection> : null}<Link to={`/projects/${encodeURIComponent(projectId)}/writing-preparation`} className="mt-6 inline-flex items-center gap-1 text-sm font-medium text-brand-700">查看全部事实与材料 <ArrowRight size={13} /></Link></div>;
}

function CoverageView({ title, values, sectionTitles, onSection, showScore }: { title: string; values: WritingPlanCoverageDTO[]; sectionTitles: Map<string, string>; onSection: (id: string) => void; showScore?: boolean }) {
  const coverage = technicalCoverageSummary(values);
  return <section className="overflow-hidden rounded-lg border border-slate-200 bg-white"><div className="border-b border-slate-200 px-5 py-4"><h3 className="font-semibold text-slate-900">{title}</h3>{showScore ? <p className="mt-1 text-xs text-slate-500">仅展示当前技术标书需要响应的评分项。</p> : <><p className="mt-1 text-xs text-slate-500">这里只展示需要在技术方案中响应的招标要求。商务、资格、报价和提交要求仍保留在招标分析中。</p><div className="mt-3 flex flex-wrap gap-5 text-xs text-slate-600"><span>技术要求总数 <strong className="text-slate-900">{coverage.total}</strong></span><span>已覆盖 <strong className="text-emerald-700">{coverage.covered}</strong></span><span>待覆盖 <strong className="text-amber-700">{coverage.pending}</strong></span></div></>}</div><div className="overflow-x-auto"><div className="min-w-[700px]"><div className="grid grid-cols-[minmax(330px,1fr)_110px_minmax(230px,0.8fr)] gap-4 border-b border-slate-200 bg-slate-50 px-5 py-3 text-xs font-semibold text-slate-500"><div>{showScore ? "评分项" : "技术要求"}</div><div>{showScore ? "满分" : "覆盖状态"}</div><div>规划位置</div></div><div className="divide-y divide-slate-100">{values.map((item) => <div key={item.trace_id} className="grid grid-cols-[minmax(330px,1fr)_110px_minmax(230px,0.8fr)] gap-4 px-5 py-3 text-sm"><Link to={item.target_url} className="line-clamp-3 font-medium leading-6 text-brand-700">{item.title}</Link><div className={item.status === "unassigned" ? "font-medium text-amber-700" : "text-slate-600"}>{showScore ? item.max_score == null ? "—" : `${formatScore(item.max_score)} 分` : item.status === "unassigned" ? "待覆盖" : item.status === "multiple_sections" ? "多处覆盖" : "已覆盖"}</div><div className="flex flex-wrap gap-2">{item.section_ids.length ? item.section_ids.map((id) => <button type="button" key={id} onClick={() => onSection(id)} className="text-xs font-medium text-brand-700 hover:underline">{sectionTitles.get(id) ?? "查看对应章节"}</button>) : <span className="text-xs text-slate-400">—</span>}</div></div>)}</div></div></div></section>;
}

function TraceLinks({ values, showScore }: { values: WritingTaskDTO["requirement_refs"]; showScore?: boolean }) {
  return <div className="space-y-2">{values.map((item) => <Link key={item.trace_id} to={item.target_url} className="flex items-start justify-between gap-3 rounded-md border border-slate-200 px-3 py-2.5 hover:border-brand-300 hover:bg-brand-50"><div><div className="line-clamp-3 text-sm leading-6 text-slate-700">{item.title}</div>{item.mapping_role ? <div className="mt-1 text-xs text-slate-400">{item.mapping_role === "primary" ? "主要响应" : "辅助响应"}</div> : null}</div>{showScore && item.max_score != null ? <span className="shrink-0 text-sm font-semibold text-slate-900">{formatScore(item.max_score)} 分</span> : <ArrowRight className="mt-1 shrink-0 text-brand-600" size={14} />}</Link>)}</div>;
}

function FactLine({ value }: { value: WritingPlanFactDependencyDTO }) { return <Link to={value.target_url ?? "#"} className="block border-b border-slate-200 py-2.5 last:border-b-0"><div className="flex items-start justify-between gap-2"><div className="text-sm font-medium text-slate-800">{value.title}</div><span className={`shrink-0 text-xs font-medium ${value.availability === "available" ? "text-emerald-700" : "text-amber-700"}`}>{value.status_label}</span></div>{value.value ? <div className="mt-1 line-clamp-4 text-xs leading-5 text-slate-600">{value.value}</div> : null}</Link>; }
function MaterialLine({ value }: { value: WritingPlanMaterialDependencyDTO }) { return <Link to={value.target_url ?? "#"} className="block border-b border-slate-200 py-2.5 last:border-b-0"><div className="flex items-start justify-between gap-2"><div className="text-sm font-medium text-slate-800">{materialLabels[value.evidence_type] ?? value.title}</div><span className={`shrink-0 text-xs font-medium ${value.status === "query_not_executed" ? "text-amber-700" : "text-slate-500"}`}>{value.status_label}</span></div></Link>; }

function ContextSection({ title, icon: Icon, tone, children }: { title: string; icon: typeof CheckCircle2; tone: "success" | "warning" | "neutral"; children: ReactNode }) { return <section className="mt-6"><div className={`flex items-center gap-2 text-xs font-semibold ${tone === "success" ? "text-emerald-700" : tone === "warning" ? "text-amber-700" : "text-slate-600"}`}><Icon size={14} />{title}</div><div className="mt-2">{children}</div></section>; }
function DetailSection({ title, children }: { title: string; children: ReactNode }) { return <section className="mt-6 border-t border-slate-100 pt-5"><h4 className="mb-3 text-sm font-semibold text-slate-900">{title}</h4>{children}</section>; }
function ViewTab({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) { return <button type="button" onClick={onClick} className={`border-b-2 px-3 pb-3 text-sm font-medium ${active ? "border-brand-700 text-brand-800" : "border-transparent text-slate-500 hover:text-slate-800"}`}>{children}</button>; }
function Summary({ label, value, detail, tone }: { label: string; value: string | number; detail?: string; tone?: "success" | "warning" }) { return <div className="border-b border-r border-slate-100 px-4 py-3 last:border-r-0 xl:border-b-0"><div className="text-xs text-slate-500">{label}</div><div className={`mt-1 text-xl font-semibold ${tone === "success" ? "text-emerald-700" : tone === "warning" ? "text-amber-700" : "text-slate-900"}`}>{value}</div>{detail ? <div className="mt-1 text-[11px] text-slate-400">{detail}</div> : null}</div>; }
function MiniStat({ label, value }: { label: string; value: number }) { return <div className="rounded-md border border-slate-200 px-3 py-3"><div className="text-xs text-slate-500">{label}</div><div className="mt-1 text-lg font-semibold text-slate-900">{value}</div></div>; }
function Muted({ children }: { children: ReactNode }) { return <div className="text-sm leading-6 text-slate-400">{children}</div>; }
function LineageDetails({ data }: { data: { source_mode: string; analysis_run_id: string | null; planning_key: string | null; generated_at: string | null; response_outline_sha256: string | null; writing_outline_sha256: string | null; writing_tasks_sha256: string | null; task_information_sha256: string | null; lineage_valid: boolean } }) { return <dl className="grid grid-cols-[130px_minmax(0,1fr)] gap-x-3 gap-y-2 text-xs"><dt>来源</dt><dd>{data.source_mode}</dd><dt>Analysis Run</dt><dd>{data.analysis_run_id ?? "历史 artifact"}</dd><dt>Planning Key</dt><dd>{data.planning_key ?? "—"}</dd><dt>生成时间</dt><dd>{formatLocalDateTime(data.generated_at)}</dd><dt>Lineage</dt><dd>{data.lineage_valid ? "校验通过" : "不一致"}</dd><dt>Response SHA</dt><dd className="truncate font-mono">{data.response_outline_sha256 ?? "—"}</dd><dt>Writing SHA</dt><dd className="truncate font-mono">{data.writing_outline_sha256 ?? "—"}</dd><dt>Tasks SHA</dt><dd className="truncate font-mono">{data.writing_tasks_sha256 ?? "—"}</dd></dl>; }
function formatScore(value: number) { return Number.isInteger(value) ? value.toFixed(0) : value.toFixed(1); }
function isDefined<T>(value: T | undefined): value is T { return value !== undefined; }
