import { ArrowUpRight, CircleAlert, MapPin } from "lucide-react";
import { Link } from "react-router-dom";
import type {
  AuditedFulltextDTO,
  FulltextParagraphDTO,
  FulltextSectionDTO,
  ReviewLocationDTO,
} from "../../../types/api";
import { mergeTrace } from "./DocumentParagraph";
import { placeholderDisplayTitle } from "../../../utils/userFacingText";

interface FulltextContextPanelProps {
  data: AuditedFulltextDTO;
  projectId: string;
  section: FulltextSectionDTO | null;
  paragraph: FulltextParagraphDTO | null;
  onNavigate: (location: ReviewLocationDTO) => void;
}

export function FulltextContextPanel({
  data,
  projectId,
  section,
  paragraph,
  onNavigate,
}: FulltextContextPanelProps) {
  if (!section) {
    return <EmptyPanel text="选择正文小节或段落后，可查看其关联的招标要求、审核发现和评分项。" />;
  }

  const trace = paragraph ? mergeTrace(section.trace, paragraph.trace) : section.trace;
  const requirements = data.trace_index.requirements.filter((item) => trace.requirement_ids.includes(item.trace_id));
  const findings = data.trace_index.findings.filter((item) => trace.finding_ids.includes(item.trace_id));
  const reviews = data.trace_index.reviews.filter((item) => trace.review_item_ids.includes(item.trace_id));
  const scores = data.trace_index.scores.filter((item) => trace.score_item_ids.includes(item.trace_id));
  const dependencies = data.trace_index.dependencies.filter((item) =>
    trace.fact_refs.includes(item.trace_id) || trace.material_refs.includes(item.trace_id),
  );

  return (
    <aside className="h-full overflow-y-auto bg-white">
      <div className="sticky top-0 z-10 border-b border-slate-200 bg-white px-4 py-4">
        <div className="text-xs font-medium uppercase tracking-wide text-slate-400">当前正文位置</div>
        <div className="mt-1 text-sm font-semibold text-slate-900">{section.heading}</div>
      </div>

      <div className="space-y-6 px-4 py-5">
        {requirements.length ? (
          <TraceGroup title="关联招标要求">
            {requirements.map((item) => (
              <TraceCard key={item.trace_id} title={item.title}>
                <p className="line-clamp-5 text-sm leading-6 text-slate-600">{item.requirement_text}</p>
                <LocationLinks label="响应正文位置" locations={item.response_locations} onNavigate={onNavigate} />
                <TraceLink to={`/projects/${encodeURIComponent(projectId)}/analysis/requirements/${encodeURIComponent(item.trace_id)}`}>
                  查看招标要求
                </TraceLink>
              </TraceCard>
            ))}
          </TraceGroup>
        ) : null}

        {findings.length ? (
          <TraceGroup title="机器发现">
            {findings.map((item) => {
              const review = reviews.find((candidate) => candidate.trace_id === item.trace_id);
              return (
                <TraceCard
                  key={item.trace_id}
                  title={item.title}
                  meta={[item.severity, item.release_blocking ? "影响放行" : null].filter(Boolean).join(" · ")}
                  warning={item.release_blocking}
                >
                  <p className="text-sm leading-6 text-slate-700">{item.summary}</p>
                  {item.rationale ? (
                    <details className="mt-2 text-xs leading-5 text-slate-500">
                      <summary className="cursor-pointer select-none">查看机器判断理由</summary>
                      <p className="mt-2 whitespace-pre-wrap">{item.rationale}</p>
                    </details>
                  ) : null}
                  {item.recommendation ? (
                    <div className="mt-3 rounded-md bg-slate-50 px-3 py-2 text-xs leading-5 text-slate-600">
                      <span className="font-medium text-slate-700">建议：</span>{item.recommendation}
                    </div>
                  ) : null}
                  {review ? (
                    <TraceLink to={`/projects/${encodeURIComponent(projectId)}/review?item=${encodeURIComponent(review.trace_id)}`}>
                      查看审核决定
                    </TraceLink>
                  ) : null}
                </TraceCard>
              );
            })}
          </TraceGroup>
        ) : null}

        {reviews.length ? (
          <TraceGroup title="人工审核">
            {reviews.map((item) => (
              <TraceCard
                key={item.trace_id}
                title={decisionLabel(item.current_decision, item.risk_accepted)}
                meta={actionabilityLabel(item.actionability)}
              >
                <LocationLinks label="当前响应位置" locations={item.response_locations} onNavigate={onNavigate} />
                {item.recommended_locations.length ? (
                  <LocationLinks label="建议修改位置" locations={item.recommended_locations} onNavigate={onNavigate} />
                ) : null}
                {item.revision_candidate ? (
                  <div className="mt-3 text-xs font-medium text-amber-700">已列为下一轮修改候选</div>
                ) : null}
                <TraceLink to={`/projects/${encodeURIComponent(projectId)}/review?item=${encodeURIComponent(item.trace_id)}`}>
                  打开人工审核
                </TraceLink>
              </TraceCard>
            ))}
          </TraceGroup>
        ) : null}

        {scores.length ? (
          <TraceGroup title="关联评分项">
            {scores.map((item) => (
              <TraceCard key={item.trace_id} title={item.title} meta={`满分 ${item.max_score} 分`}>
                <div className="text-sm text-slate-600">
                  当前评估：{item.score_range.minimum}–{item.score_range.maximum} 分
                </div>
                {item.pending_evidence.map((evidence, index) => (
                  <div key={`${evidence.evidence_type}-${index}`} className="mt-2 text-xs leading-5 text-amber-700">
                    {evidence.status === "query_not_executed" ? "尚未查询" : evidence.status_label}
                    {evidence.reason ? `：${evidence.reason}` : ""}
                  </div>
                ))}
                <LocationLinks label="评分对应正文" locations={item.response_locations} onNavigate={onNavigate} />
                <TraceLink to={`/projects/${encodeURIComponent(projectId)}/scoring?scoreItem=${encodeURIComponent(item.trace_id)}`}>
                  查看评分项
                </TraceLink>
              </TraceCard>
            ))}
          </TraceGroup>
        ) : null}

        {dependencies.length ? (
          <TraceGroup title="事实与材料">
            {dependencies.map((item) => {
              const isMaterial = item.kind.toLowerCase().includes("material") || trace.material_refs.includes(item.trace_id);
              const parameter = isMaterial ? "material" : "fact";
              return (
                <TraceCard key={item.trace_id} title={placeholderDisplayTitle(item.title, isMaterial ? "material" : "confirmation_required")} meta={item.status_label}>
                  <TraceLink to={`/projects/${encodeURIComponent(projectId)}/facts-materials?${parameter}=${encodeURIComponent(item.trace_id)}`}>
                    查看{isMaterial ? "材料" : "事实"}
                  </TraceLink>
                </TraceCard>
              );
            })}
          </TraceGroup>
        ) : null}

        {!requirements.length && !findings.length && !reviews.length && !scores.length && !dependencies.length ? (
          <div className="rounded-md border border-dashed border-slate-300 px-4 py-6 text-sm leading-6 text-slate-500">
            当前位置没有关联的审核追踪对象。
          </div>
        ) : null}
      </div>
    </aside>
  );
}

function EmptyPanel({ text }: { text: string }) {
  return <aside className="h-full bg-white px-5 py-8 text-sm leading-6 text-slate-500">{text}</aside>;
}

function TraceGroup({ title, children }: { title: string; children: React.ReactNode }) {
  return <section><h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</h3><div className="space-y-3">{children}</div></section>;
}

function TraceCard({ title, meta, warning = false, children }: { title: string; meta?: string; warning?: boolean; children: React.ReactNode }) {
  return (
    <div className={`rounded-lg border p-3 ${warning ? "border-amber-200 bg-amber-50/40" : "border-slate-200 bg-white"}`}>
      <div className="flex items-start gap-2">
        {warning ? <CircleAlert className="mt-0.5 shrink-0 text-amber-600" size={15} /> : null}
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium leading-5 text-slate-800">{title}</div>
          {meta ? <div className="mt-1 text-[11px] text-slate-400">{meta}</div> : null}
        </div>
      </div>
      <div className="mt-3">{children}</div>
    </div>
  );
}

function TraceLink({ to, children }: { to: string; children: React.ReactNode }) {
  return <Link to={to} className="mt-3 inline-flex items-center gap-1 text-xs font-medium text-brand-700 hover:text-brand-800">{children}<ArrowUpRight size={12} /></Link>;
}

function LocationLinks({ label, locations, onNavigate }: { label: string; locations: ReviewLocationDTO[]; onNavigate: (location: ReviewLocationDTO) => void }) {
  if (!locations.length) return null;
  return (
    <details className="mt-3" open={locations.length <= 2}>
      <summary className="cursor-pointer select-none text-xs font-medium text-slate-500">{label} · {locations.length}</summary>
      <div className="mt-2 max-h-44 space-y-1.5 overflow-y-auto">
        {locations.map((location, index) => (
          <button
            key={`${location.section_id ?? "location"}-${location.paragraph_id ?? index}`}
            type="button"
            onClick={() => onNavigate(location)}
            className="flex w-full items-start gap-2 rounded-md border border-slate-200 px-3 py-2 text-left text-xs text-brand-700 hover:bg-brand-50"
          >
            <MapPin className="mt-0.5 shrink-0" size={13} />
            <span>{locationLabel(location)}</span>
          </button>
        ))}
      </div>
    </details>
  );
}

function decisionLabel(decision: string, riskAccepted: boolean) {
  if (riskAccepted) return "已接受风险";
  if (decision === "accepted") return "按建议修改";
  if (decision === "modified") return "已制定自定义修改方案";
  if (decision === "rejected") return "人工未采纳该问题";
  return "等待人工决定";
}

function actionabilityLabel(value: string) {
  if (value === "must_decide") return "必须决定";
  if (value === "verification") return "事实与材料核验";
  if (value === "no_action") return "无需修改";
  return "仅供参考";
}

function locationLabel(location: ReviewLocationDTO) {
  return [location.chapter_title, location.section_heading].filter(Boolean).join(" > ") || "正文位置";
}
