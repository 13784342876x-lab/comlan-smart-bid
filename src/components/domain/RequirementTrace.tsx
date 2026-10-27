import { Check, Copy, ShieldAlert, Sparkles } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import type { RequirementDetailDTO } from "../../types/api";
import { Panel } from "../ui/Panel";
import { RequirementStatus } from "./RequirementStatus";
import { RequirementDependencies } from "./RequirementDependencies";
import { RequirementReviewState } from "./RequirementReviewState";
import { ResponseLocation } from "./ResponseLocation";
import { ScoreImpact } from "./ScoreImpact";
import { SourceReference } from "./SourceReference";

export function RequirementTrace({ value }: { value: RequirementDetailDTO }) {
  const [copied, setCopied] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const requirement = value.requirement;
  const preparationItemId = value.preparation_dependencies[0]?.trace_id ?? value.fact_dependencies[0]?.trace_id ?? value.material_dependencies[0]?.trace_id;
  const issueFindings = value.findings
    .filter((item) => item.verdict === "issue" || item.release_blocking || item.requires_human_review)
    .filter((item, index, items) => items.findIndex((candidate) => candidate.summary === item.summary) === index);
  const copy = async () => {
    await navigator.clipboard.writeText(requirement.requirement_text);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };
  return (
    <div className="space-y-5">
      <Panel className="p-5">
        <div className="flex flex-wrap items-start justify-between gap-4"><div className="flex flex-wrap items-center gap-3"><RequirementStatus value={requirement.response_status} />{requirement.category ? <span className="text-xs text-slate-400">{requirement.category}</span> : null}</div><button onClick={copy} className="inline-flex items-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-600 hover:bg-slate-50">{copied ? <Check size={14} /> : <Copy size={14} />}{copied ? "已复制" : "复制要求"}</button></div>
        <p className={`mt-4 whitespace-pre-wrap text-sm leading-7 text-slate-800 ${expanded ? "" : "line-clamp-8"}`}>{requirement.requirement_text}</p>
        {requirement.requirement_text.length > 560 ? <button onClick={() => setExpanded((current) => !current)} className="mt-2 text-xs font-medium text-brand-600">{expanded ? "收起长文本" : "展开完整要求"}</button> : null}
      </Panel>

      <TraceSection title="A. 招标来源"><SourceReference value={requirement.source_reference} /></TraceSection>

      <TraceSection title="B. 当前标书响应">
        {requirement.response_locations.length ? <div className="space-y-3">{requirement.response_locations.map((location, index) => <ResponseLocation key={`${location.technical_ref ?? "location"}-${index}`} value={location} projectId={value.project_id} trace={{ requirement: requirement.trace_id }} label={location.response_role === "primary" ? "主要响应位置" : index === 0 ? "响应位置" : "其他相关响应"} />)}</div> : <p className="text-sm text-slate-400">{requirement.response_status === "not_started" ? "当前仍处于写作前招标分析阶段，尚未形成正文响应。" : "当前审核正文没有已定位响应位置。"}</p>}
      </TraceSection>

      <TraceSection title="C. Machine Assessment">
        {issueFindings.length ? <div className="space-y-3">{issueFindings.map((item) => <div key={item.trace_id} className={`rounded-lg border p-4 ${item.release_blocking ? "border-red-200 bg-red-50/60" : "border-amber-200 bg-amber-50/60"}`}><div className="flex flex-wrap items-center gap-2 text-sm font-semibold text-slate-900"><Sparkles size={16} className="text-amber-600" />{item.summary}{item.release_blocking ? <span className="text-xs font-medium text-red-600">阻塞放行</span> : null}</div>{item.rationale ? <p className="mt-2 text-sm leading-7 text-slate-600">判断理由：{item.rationale}</p> : null}{item.recommendation ? <p className="mt-2 border-t border-amber-200/70 pt-2 text-sm leading-7 text-slate-700">建议：{item.recommendation}</p> : null}<div className="mt-2 text-xs text-slate-400">严重性：{item.severity ?? "未定"}</div></div>)}</div> : <div className="flex items-center gap-2 text-sm text-slate-500"><ShieldAlert size={16} />没有关联需处置的机器问题。</div>}
        {value.findings.length ? <details className="mt-3 rounded-lg border border-slate-200 bg-white px-4 py-3"><summary className="cursor-pointer text-xs font-medium text-slate-500">技术详情 · {value.findings.length} 条审计引用</summary><div className="mt-3 space-y-2">{value.findings.map((item) => <div key={item.trace_id} className="text-xs text-slate-600">{item.summary}</div>)}</div></details> : null}
      </TraceSection>

      <TraceSection title="D. Scoring Impact"><ScoreImpact items={value.scoring_impacts} projectId={value.project_id} /></TraceSection>
      <TraceSection title="E. Facts & Materials"><RequirementDependencies facts={value.fact_dependencies} materials={value.material_dependencies} projectId={value.project_id} /></TraceSection>
      {value.preparation_dependencies.length ? <TraceSection title="写作准备"><RequirementDependencies facts={value.preparation_dependencies} materials={[]} projectId={value.project_id} /></TraceSection> : null}
      {preparationItemId ? <div className="flex justify-end"><Link to={`/projects/${encodeURIComponent(value.project_id)}/writing-preparation?item=${encodeURIComponent(preparationItemId)}`} className="text-sm font-medium text-brand-700">查看写作准备中的事实与材料依赖</Link></div> : null}
      <TraceSection title="F. Human Review"><RequirementReviewState items={value.review_states} projectId={value.project_id} /></TraceSection>
    </div>
  );
}

function TraceSection({ title, children }: { title: string; children: React.ReactNode }) {
  return <section><h3 className="mb-3 text-sm font-semibold text-slate-900">{title}</h3>{children}</section>;
}
