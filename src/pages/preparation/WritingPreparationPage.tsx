import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  ArrowRight,
  BookOpenCheck,
  Box,
  CheckCircle2,
  CircleHelp,
  FileQuestion,
  LibraryBig,
  Search,
  ShieldCheck,
} from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError } from "../../api/client";
import { confirmProjectFact, decideHumanConfirmation, getWritingPreparation } from "../../api/writingPreparation";
import { useApiQuery } from "../../api/useApiQuery";
import { useProject, useProjectLoad } from "../../app/providers/ProjectProvider";
import { SourceReference } from "../../components/domain/SourceReference";
import { EmptyState, ErrorState, LoadingState } from "../../components/ui/PageState";
import type {
  PreparationDependencyDTO,
  HumanConfirmationCommandDTO,
  ProjectFactCommandDTO,
  WritingContextItemDTO,
  WritingPreparationItemDTO,
} from "../../types/api";

type ViewKey = "all" | "project_fact" | "human_confirmation" | "material" | "context";

const tabs: Array<{ key: ViewKey; label: string }> = [
  { key: "all", label: "全部" },
  { key: "project_fact", label: "项目事实" },
  { key: "human_confirmation", label: "人工确认" },
  { key: "material", label: "证明材料" },
  { key: "context", label: "AI 可用上下文" },
];

const kindLabels: Record<WritingPreparationItemDTO["kind"], string> = {
  project_fact: "项目事实",
  human_confirmation: "人工确认",
  material: "证明材料",
  evidence_query: "材料查询",
};

export function WritingPreparationPage() {
  const { projectId } = useProject();
  const projectLoad = useProjectLoad();
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const query = useApiQuery((signal) => getWritingPreparation(projectId, signal), [projectId]);
  const view = (tabs.some((item) => item.key === params.get("view")) ? params.get("view") : "all") as ViewKey;

  const filtered = useMemo(() => {
    if (!query.data) return [];
    const needle = search.trim().toLowerCase();
    return query.data.items.filter((item) => {
      const kindMatch = view === "all" || view === "context" || item.kind === view || (view === "material" && item.kind === "evidence_query");
      const statusMatch = status === "all" || item.status === status;
      const haystack = [item.title, item.description, item.item_id, ...item.dependencies.map((value) => value.title)].join(" ").toLowerCase();
      return kindMatch && statusMatch && (!needle || haystack.includes(needle));
    });
  }, [query.data, search, status, view]);

  if (query.loading && !query.data) return <LoadingState label="正在读取写作准备与 AI 上下文…" />;
  if (query.error) return <ErrorState error={query.error} onRetry={query.reload} />;
  if (!query.data) return null;

  const data = query.data;
  const selectedId = params.get("item");
  const selected = filtered.find((item) => item.item_id === selectedId) ?? filtered[0] ?? null;
  const statuses = Array.from(new Map(data.items.map((item) => [item.status, item.status_label])).entries());
  const selectView = (key: ViewKey) => {
    const next = new URLSearchParams(params);
    next.set("view", key);
    next.delete("item");
    setParams(next, { replace: true });
  };
  const selectItem = (itemId: string) => {
    const next = new URLSearchParams(params);
    next.set("item", itemId);
    setParams(next, { replace: true });
  };

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 text-xs font-medium text-brand-700"><BookOpenCheck size={15} />基于当前招标分析版本</div>
          <h2 className="mt-1 text-2xl font-semibold text-slate-950">事实与材料确认</h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-600">{data.summary.guidance}</p>
        </div>
        <Link to={`/projects/${encodeURIComponent(projectId)}/writing-plan`} className="inline-flex items-center gap-2 rounded-md bg-brand-700 px-4 py-2.5 text-sm font-medium text-white hover:bg-brand-800">
          查看标书结构 <ArrowRight size={16} />
        </Link>
      </header>

      <div className="grid overflow-hidden rounded-lg border border-slate-200 bg-white sm:grid-cols-2 xl:grid-cols-4">
        <SummaryCell label="已确认事实" value={data.summary.confirmed_fact_count} detail="可作为确定事实使用" tone="success" />
        <SummaryCell label="待确认事实" value={data.summary.pending_fact_count + data.summary.confirmation_count} detail="不会进入确定事实上下文" tone="warning" />
        <SummaryCell label="尚未查询材料" value={data.summary.query_not_executed_count} detail="未知，不代表不存在" tone="warning" />
        <SummaryCell label="待确认字段" value={data.summary.placeholder_count} detail="现在写作时需保留占位" />
      </div>

      {data.read_only || data.mutation_unavailable_reason ? (
        <div className="flex items-start gap-3 rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-sm leading-6 text-blue-950">
          <ShieldCheck className="mt-0.5 shrink-0 text-blue-700" size={18} />
          <div><span className="font-medium">当前版本已更新，暂时无法写入项目事实。</span> {data.mutation_unavailable_reason}</div>
        </div>
      ) : null}

      <section className="overflow-hidden rounded-lg border border-slate-200 bg-white">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 px-4 pt-3">
          <div className="flex gap-1 overflow-x-auto">
            {tabs.map((tab) => (
              <button key={tab.key} type="button" onClick={() => selectView(tab.key)} className={`whitespace-nowrap border-b-2 px-3 pb-3 pt-1 text-sm font-medium ${view === tab.key ? "border-brand-700 text-brand-800" : "border-transparent text-slate-500 hover:text-slate-800"}`}>
                {tab.label}
              </button>
            ))}
          </div>
          {view !== "context" ? <div className="flex items-center gap-2 pb-3">
            <label className="relative block"><Search className="pointer-events-none absolute left-3 top-2.5 text-slate-400" size={15} /><input value={search} onChange={(event) => setSearch(event.target.value)} className="w-64 rounded-md border border-slate-200 py-2 pl-9 pr-3 text-sm outline-none focus:border-brand-500" placeholder="搜索事实、材料、要求或章节" /></label>
            <select value={status} onChange={(event) => setStatus(event.target.value)} className="rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700"><option value="all">全部状态</option>{statuses.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select>
          </div> : null}
        </div>

        {view === "context" ? <WritingContextView values={data.writing_context} /> : (
          <div className="grid min-h-[580px] xl:grid-cols-[390px_minmax(0,1fr)]">
            <div className="border-r border-slate-200">
              <div className="border-b border-slate-100 px-4 py-2 text-xs text-slate-500">{filtered.length} 项准备事项</div>
              {filtered.length ? <div className="max-h-[720px] overflow-y-auto">{filtered.map((item) => <QueueRow key={item.item_id} value={item} active={selected?.item_id === item.item_id} onClick={() => selectItem(item.item_id)} />)}</div> : <EmptyState title="没有匹配的准备事项" description="调整类型、状态或搜索条件后重试。" />}
            </div>
            <div className="min-w-0">{selected ? <PreparationDetail projectId={projectId} value={selected} token={data.version_token} onSaved={() => { query.reload(); projectLoad.reload(); }} /> : <EmptyState title="选择一项查看详情" description="详情会解释为什么需要，以及它会影响哪些要求、评分和写作位置。" />}</div>
          </div>
        )}
      </section>
    </div>
  );
}

function SummaryCell({ label, value, detail, tone }: { label: string; value: number; detail: string; tone?: "success" | "warning" }) {
  return <div className="border-b border-r border-slate-100 px-5 py-4 last:border-r-0 sm:border-b-0"><div className="text-xs text-slate-500">{label}</div><div className={`mt-1 text-2xl font-semibold ${tone === "success" ? "text-emerald-700" : tone === "warning" ? "text-amber-700" : "text-slate-900"}`}>{value}</div><div className="mt-1 text-xs text-slate-500">{detail}</div></div>;
}

function QueueRow({ value, active, onClick }: { value: WritingPreparationItemDTO; active: boolean; onClick: () => void }) {
  const Icon = value.kind === "project_fact" ? Box : value.kind === "human_confirmation" ? FileQuestion : LibraryBig;
  return <button type="button" onClick={onClick} className={`block w-full border-b border-slate-100 px-4 py-3 text-left ${active ? "bg-brand-50" : "hover:bg-slate-50"}`}><div className="flex items-start gap-3"><Icon className={`mt-0.5 shrink-0 ${active ? "text-brand-700" : "text-slate-400"}`} size={17} /><div className="min-w-0 flex-1"><div className="flex items-start justify-between gap-2"><div className="line-clamp-2 text-sm font-medium text-slate-900">{value.title}</div><span className={`shrink-0 text-xs font-medium ${value.status === "confirmed" || value.status === "verified" ? "text-emerald-700" : value.status === "query_not_executed" ? "text-amber-700" : "text-slate-500"}`}>{value.status_label}</span></div><div className="mt-1 flex items-center gap-2 text-xs text-slate-400"><span>{kindLabels[value.kind]}</span>{value.dependencies.length ? <><span>·</span><span>关联 {value.dependencies.length}</span></> : null}</div></div></div></button>;
}

function PreparationDetail({ projectId, value, token, onSaved }: { projectId: string; value: WritingPreparationItemDTO; token: string; onSaved: () => void }) {
  const requirements = value.dependencies.filter((item) => item.kind === "requirement");
  const scores = value.dependencies.filter((item) => item.kind === "score");
  const writing = value.dependencies.filter((item) => item.kind === "writing");
  return <div className="p-6">
    <div className="flex flex-wrap items-start justify-between gap-3"><div><div className="text-xs font-medium text-brand-700">{kindLabels[value.kind]}</div><h3 className="mt-1 text-xl font-semibold text-slate-950">{value.title}</h3></div><span className={`text-sm font-medium ${value.status === "query_not_executed" ? "text-amber-700" : value.status === "confirmed" ? "text-emerald-700" : "text-slate-600"}`}>{value.status_label}</span></div>
    <p className="mt-5 whitespace-pre-wrap text-sm leading-7 text-slate-700">{value.description || "当前没有额外说明。"}</p>
    {value.current_value ? <DetailSection title="当前确认值"><div className="rounded-md bg-emerald-50 px-4 py-3 text-sm leading-6 text-emerald-950">{value.current_value}</div></DetailSection> : null}
    {value.kind === "project_fact" && value.can_confirm ? <ProjectFactEditor projectId={projectId} value={value} token={token} onSaved={onSaved} /> : null}
    {value.kind === "human_confirmation" && value.can_confirm ? <HumanConfirmationEditor projectId={projectId} value={value} token={token} onSaved={onSaved} /> : null}
    {value.writing_context ? <DetailSection title="AI 写作使用边界"><ContextLine value={value.writing_context} /></DetailSection> : null}
    <DependencySection title="相关招标要求" values={requirements} empty="当前没有关联的招标要求。" />
    <DependencySection title="相关评分规则" values={scores} empty="没有直接评分关系。" />
    <DependencySection title="预计写作用途" values={writing.slice(0, 16)} empty="当前尚未建立章节依赖。" />
    {writing.length > 16 ? <div className="mt-2 text-xs text-slate-400">另有 {writing.length - 16} 个写作位置，后续 Writing Plan 将提供完整任务视图。</div> : null}
    {value.source_references.length ? <DetailSection title="来源"><div className="space-y-2">{value.source_references.slice(0, 4).map((source, index) => <SourceReference key={`${source.file_name}-${index}`} value={source} />)}</div></DetailSection> : null}
    <div className="mt-6 flex flex-wrap gap-2"><Link to={`/projects/${encodeURIComponent(projectId)}/facts-materials?item=${encodeURIComponent(value.item_id)}`} className="rounded-md border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50">进入事实与材料工作区</Link></div>
  </div>;
}

function HumanConfirmationEditor({ projectId, value, token, onSaved }: { projectId: string; value: WritingPreparationItemDTO; token: string; onSaved: () => void }) {
  const initialAnswer = typeof value.current_raw_value === "boolean" ? String(value.current_raw_value) : String(value.current_raw_value ?? "");
  const [answer, setAnswer] = useState(initialAnswer);
  const [sourceDescription, setSourceDescription] = useState("");
  const [basis, setBasis] = useState("");
  const [note, setNote] = useState("");
  const [changeReason, setChangeReason] = useState("");
  const [saveState, setSaveState] = useState<"idle" | "saving" | "success" | "error" | "conflict">("idle");
  const [message, setMessage] = useState<string | null>(null);
  const dirty = answer !== initialAnswer || sourceDescription !== "" || basis !== "" || note !== "" || changeReason !== "";
  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => { if (dirty) event.preventDefault(); };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [dirty]);

  async function save() {
    setSaveState("saving"); setMessage(null);
    const body: HumanConfirmationCommandDTO = {
      answer: value.confirmation_response_kind === "boolean" ? answer === "true" : answer.trim(),
      source_description: sourceDescription.trim(), confirmation_basis: basis.trim(),
      note: note.trim() || null, change_reason: changeReason.trim() || null,
    };
    try {
      await decideHumanConfirmation(projectId, value.item_id, token, body);
      setSaveState("success");
      setMessage("业务选择已保存，Writing Context 已更新");
      onSaved();
    } catch (cause) {
      if (cause instanceof ApiError && cause.isLineageConflict) {
        setSaveState("conflict");
        setMessage("写作准备内容已发生变化。当前输入仍保留，请刷新最新版本后重新确认。");
      } else {
        setSaveState("error");
        setMessage(cause instanceof Error ? cause.message : "保存人工确认失败");
      }
    }
  }

  const answerReady = value.confirmation_response_kind === "boolean" ? answer === "true" || answer === "false" : Boolean(answer.trim());
  return <DetailSection title="确认业务选择"><div className="space-y-4 rounded-lg border border-slate-200 bg-slate-50 p-4">
    <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-950">这是业务选择，不会作为已确认项目事实使用；保存后仍需等待写作输入准备完成。</div>
    {value.confirmation_response_kind === "boolean" ? <fieldset><legend className="mb-2 text-sm font-medium text-slate-700">确认结果 *</legend><div className="flex gap-4">{[["true", "是，当前项目采用"], ["false", "否，当前项目不采用"]].map(([key, label]) => <label key={key} className="flex items-center gap-2 text-sm text-slate-700"><input type="radio" name={`confirmation-${value.item_id}`} value={key} checked={answer === key} onChange={(event) => setAnswer(event.target.value)} />{label}</label>)}</div></fieldset> : value.confirmation_response_kind === "selection" ? <label className="block text-sm text-slate-700"><span className="mb-1.5 block font-medium">选择方案 *</span><select value={answer} onChange={(event) => setAnswer(event.target.value)} className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 outline-none focus:border-brand-500"><option value="">请选择</option>{value.candidate_values.map((candidate) => <option key={candidate} value={candidate}>{candidate}</option>)}</select></label> : <label className="block text-sm text-slate-700"><span className="mb-1.5 block font-medium">确认结果 *</span><textarea value={answer} onChange={(event) => setAnswer(event.target.value)} rows={3} className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 outline-none focus:border-brand-500" /></label>}
    <label className="block text-sm text-slate-700"><span className="mb-1.5 block font-medium">来源说明 *</span><input value={sourceDescription} onChange={(event) => setSourceDescription(event.target.value)} placeholder="例如：项目负责人确认、服务方案会签" className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 outline-none focus:border-brand-500" /></label>
    <label className="block text-sm text-slate-700"><span className="mb-1.5 block font-medium">确认依据 *</span><input value={basis} onChange={(event) => setBasis(event.target.value)} placeholder="说明作出该业务选择的依据" className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 outline-none focus:border-brand-500" /></label>
    <label className="block text-sm text-slate-700"><span className="mb-1.5 block font-medium">备注</span><textarea value={note} onChange={(event) => setNote(event.target.value)} rows={2} className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 outline-none focus:border-brand-500" /></label>
    {value.status === "confirmed" ? <label className="block text-sm text-slate-700"><span className="mb-1.5 block font-medium">变更说明 *</span><textarea value={changeReason} onChange={(event) => setChangeReason(event.target.value)} rows={2} className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 outline-none focus:border-brand-500" /></label> : null}
    {message ? <div className={`flex items-center justify-between gap-3 text-sm ${saveState === "success" ? "text-emerald-700" : saveState === "conflict" ? "text-amber-700" : "text-red-700"}`}><span>{message}</span>{saveState === "conflict" ? <button type="button" onClick={onSaved} className="shrink-0 rounded-md border border-amber-300 bg-white px-3 py-1.5 font-medium text-amber-800">刷新最新版本</button> : null}</div> : null}
    <div className="flex justify-end"><button type="button" disabled={saveState === "saving" || !answerReady || !sourceDescription.trim() || !basis.trim() || (value.status === "confirmed" && !changeReason.trim())} onClick={save} className="rounded-md bg-brand-700 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50">{saveState === "saving" ? "保存中…" : "确认并保存"}</button></div>
  </div></DetailSection>;
}

function ProjectFactEditor({ projectId, value, token, onSaved }: { projectId: string; value: WritingPreparationItemDTO; token: string; onSaved: () => void }) {
  const initial = useMemo(() => {
    const raw = value.current_raw_value;
    if (value.value_type === "structured" && raw && typeof raw === "object") {
      return Object.fromEntries(value.value_fields.map((field) => [field.key, String((raw as Record<string, unknown>)[field.key] ?? "")]));
    }
    return { value: raw == null ? "" : String(raw) };
  }, [value]);
  const [fields, setFields] = useState<Record<string, string>>(initial);
  const [sourceDescription, setSourceDescription] = useState("");
  const [basis, setBasis] = useState("");
  const [note, setNote] = useState("");
  const [changeReason, setChangeReason] = useState("");
  const [saveState, setSaveState] = useState<"idle" | "saving" | "success" | "error" | "conflict">("idle");
  const [message, setMessage] = useState<string | null>(null);
  const dirty = JSON.stringify(fields) !== JSON.stringify(initial) || sourceDescription !== "" || basis !== "" || note !== "" || changeReason !== "";
  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => { if (dirty) event.preventDefault(); };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [dirty]);

  async function save() {
    setSaveState("saving"); setMessage(null);
    const factValue = value.value_type === "structured"
      ? Object.fromEntries(value.value_fields.map((field) => [field.key, field.field_type === "number" ? Number(fields[field.key]) : (fields[field.key] ?? "").trim()]))
      : value.value_type === "number" ? Number(fields.value) : (fields.value ?? "").trim();
    const body: ProjectFactCommandDTO = {
      status: "confirmed", value: factValue, source_description: sourceDescription.trim(),
      confirmation_basis: basis.trim(), note: note.trim() || null, change_reason: changeReason.trim() || null,
    };
    try {
      await confirmProjectFact(projectId, value.item_id, token, body);
      setSaveState("success"); setMessage("已确认并写入 AI 可用上下文"); onSaved();
    } catch (cause) {
      if (cause instanceof ApiError && cause.isLineageConflict) {
        setSaveState("conflict"); setMessage("写作准备内容已发生变化。当前输入仍保留，请刷新最新版本后重新确认。");
      } else {
        setSaveState("error"); setMessage(cause instanceof Error ? cause.message : "保存项目事实失败");
      }
    }
  }

  return <DetailSection title="确认项目事实"><div className="space-y-4 rounded-lg border border-slate-200 bg-slate-50 p-4">
    <div className="grid gap-4 md:grid-cols-2">{value.value_fields.map((field) => <label key={field.key} className="text-sm text-slate-700"><span className="mb-1.5 block font-medium">{field.label}{field.required ? " *" : ""}</span><input type={field.field_type === "number" ? "number" : "text"} min={field.minimum ?? undefined} value={fields[field.key] ?? ""} onChange={(event) => setFields((current) => ({ ...current, [field.key]: event.target.value }))} className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 outline-none focus:border-brand-500" /></label>)}</div>
    <label className="block text-sm text-slate-700"><span className="mb-1.5 block font-medium">来源说明 *</span><input value={sourceDescription} onChange={(event) => setSourceDescription(event.target.value)} placeholder="例如：制造商书面确认、项目负责人提供" className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 outline-none focus:border-brand-500" /></label>
    <label className="block text-sm text-slate-700"><span className="mb-1.5 block font-medium">确认依据 *</span><input value={basis} onChange={(event) => setBasis(event.target.value)} placeholder="说明核验事实所依据的资料或业务确认" className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 outline-none focus:border-brand-500" /></label>
    <label className="block text-sm text-slate-700"><span className="mb-1.5 block font-medium">备注</span><textarea value={note} onChange={(event) => setNote(event.target.value)} rows={2} className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 outline-none focus:border-brand-500" /></label>
    {value.status === "confirmed" ? <label className="block text-sm text-slate-700"><span className="mb-1.5 block font-medium">变更说明 *</span><textarea value={changeReason} onChange={(event) => setChangeReason(event.target.value)} rows={2} className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 outline-none focus:border-brand-500" /></label> : null}
    {message ? <div className={`flex items-center justify-between gap-3 text-sm ${saveState === "success" ? "text-emerald-700" : saveState === "conflict" ? "text-amber-700" : "text-red-700"}`}><span>{message}</span>{saveState === "conflict" ? <button type="button" onClick={onSaved} className="shrink-0 rounded-md border border-amber-300 bg-white px-3 py-1.5 font-medium text-amber-800">刷新最新版本</button> : null}</div> : null}
    <div className="flex items-center justify-between gap-3"><p className="text-xs leading-5 text-slate-500">保存后会更新写作可用信息，但不会自动重写正文。</p><button type="button" disabled={saveState === "saving" || !sourceDescription.trim() || !basis.trim() || (value.status === "confirmed" && !changeReason.trim())} onClick={save} className="shrink-0 rounded-md bg-brand-700 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50">{saveState === "saving" ? "保存中…" : "确认并保存"}</button></div>
  </div></DetailSection>;
}

function DetailSection({ title, children }: { title: string; children: ReactNode }) {
  return <section className="mt-6 border-t border-slate-100 pt-5"><h4 className="mb-3 text-sm font-semibold text-slate-900">{title}</h4>{children}</section>;
}

function DependencySection({ title, values, empty }: { title: string; values: PreparationDependencyDTO[]; empty: string }) {
  return <DetailSection title={title}>{values.length ? <div className="space-y-2">{values.map((item) => <Link key={`${item.kind}-${item.trace_id}`} to={item.target_url ?? "#"} className="flex items-start justify-between gap-3 rounded-md border border-slate-200 px-3 py-2.5 text-sm hover:border-brand-300 hover:bg-brand-50"><span className="line-clamp-3 text-slate-700">{item.title}</span><span className="shrink-0 text-xs text-brand-700">查看</span></Link>)}</div> : <div className="text-sm text-slate-400">{empty}</div>}</DetailSection>;
}

function WritingContextView({ values }: { values: WritingContextItemDTO[] }) {
  const available = values.filter((item) => item.availability === "available");
  const pending = values.filter((item) => item.availability !== "available");
  return <div className="grid min-h-[560px] gap-0 xl:grid-cols-2"><ContextGroup title="当前可用于写作" description="只有已确认事实或具备正式来源的项目元数据才能进入确定上下文。" icon={CheckCircle2} values={available} tone="success" /><ContextGroup title="不会作为确定事实使用" description="待确认、占位和不可用信息不会被当成已知事实。" icon={CircleHelp} values={pending} tone="warning" /></div>;
}

function ContextGroup({ title, description, icon: Icon, values, tone }: { title: string; description: string; icon: typeof CheckCircle2; values: WritingContextItemDTO[]; tone: "success" | "warning" }) {
  return <section className="border-r border-slate-200 p-5 last:border-r-0"><div className="flex gap-3"><Icon className={tone === "success" ? "text-emerald-600" : "text-amber-600"} size={20} /><div><h3 className="font-semibold text-slate-900">{title} · {values.length}</h3><p className="mt-1 text-xs leading-5 text-slate-500">{description}</p></div></div>{values.length ? <div className="mt-5 divide-y divide-slate-100 border-y border-slate-100">{values.map((item) => <ContextLine key={item.context_id} value={item} />)}</div> : <EmptyState title="当前没有此类上下文" description={tone === "success" ? "尚未登记可用于写作的已确认项目事实。" : "当前没有待确认上下文。"} />}</section>;
}

function ContextLine({ value }: { value: WritingContextItemDTO }) {
  return <div className="py-3"><div className="flex items-start justify-between gap-3"><div className="text-sm font-medium text-slate-800">{value.title}</div><span className={`shrink-0 text-xs font-medium ${value.availability === "available" ? "text-emerald-700" : "text-amber-700"}`}>{value.availability_label}</span></div>{value.value ? <div className="mt-1 text-sm text-slate-700">{value.value}</div> : null}{value.source_description ? <div className="mt-1 text-xs leading-5 text-slate-500">{value.source_description}</div> : null}</div>;
}
