import { ArrowRight, CheckCircle2, ClipboardCheck, FileCheck2, LoaderCircle, SearchCheck } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { startFullBidGeneration } from "../../api/projects";
import { useProject, useProjectLoad } from "../../app/providers/ProjectProvider";
import { LineageInfo } from "../../components/domain/LineageInfo";
import { Panel } from "../../components/ui/Panel";
import { projectBusinessIdentity } from "../../utils/projectPresentation";
import { overviewActionTarget, reviewMetrics, scoringTarget, userStage } from "./projectOverviewViewModel";

export function ProjectOverviewPage() {
  const { projectId, overview } = useProject();
  const projectLoad = useProjectLoad();
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const { project, scoring, facts_materials: facts } = overview;
  const identity = projectBusinessIdentity(project);
  const review = reviewMetrics(overview);
  const active = overview.current_pipeline_run?.full_bid_generation_active ?? false;
  const canGenerate = overview.can_start_full_bid_generation || overview.can_resume;
  const actionTarget = overviewActionTarget(overview);

  async function generateFullBid() {
    if (starting || active) return;
    setStarting(true);
    setStartError(null);
    try {
      await startFullBidGeneration(projectId);
      projectLoad.reload();
    } catch (reason) {
      setStartError(reason instanceof Error ? reason.message : "技术标正文生成未能启动。");
    } finally {
      setStarting(false);
    }
  }

  return (
    <div className="space-y-5">
      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-panel">
        <div className="flex flex-wrap items-start justify-between gap-5">
          <div className="min-w-0"><div className="text-xs font-medium text-brand-700">项目总览</div><h2 className="mt-1 text-2xl font-semibold text-slate-950">{project.project_name}</h2></div>
          <span className="rounded-full bg-brand-50 px-3 py-1 text-sm font-medium text-brand-700">{userStage(overview)}</span>
        </div>
        <dl className="mt-6 grid gap-4 border-t border-slate-100 pt-5 sm:grid-cols-2 xl:grid-cols-4">
          <TopField label="投标主题" value={identity.bidSubject} />
          <TopField label="供应商 / 投标人" value={identity.supplierName} />
          <TopField label="当前阶段" value={userStage(overview)} />
          <TopField label="下一步操作" value={project.next_action} />
        </dl>
      </section>

      <Panel className="border-brand-200 bg-brand-50/40 p-5">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div><div className="text-xs font-medium text-brand-700">下一步</div><h3 className="mt-1 text-lg font-semibold text-slate-950">{project.next_action}</h3>{active ? <p className="mt-1 text-sm text-slate-600">正在处理：{overview.current_pipeline_run?.current_step ?? "生成技术标正文"}</p> : null}</div>
          {canGenerate ? <button type="button" disabled={starting || active} onClick={() => void generateFullBid()} className="inline-flex items-center gap-2 rounded-md bg-brand-700 px-4 py-2.5 text-sm font-medium text-white disabled:opacity-60">{starting || active ? <LoaderCircle size={15} className="animate-spin" /> : null}{overview.can_resume ? "继续处理" : "生成技术标正文"}</button> : <Link to={actionTarget} className="inline-flex items-center gap-2 rounded-md bg-brand-700 px-4 py-2.5 text-sm font-medium text-white">前往处理<ArrowRight size={15} /></Link>}
        </div>
        {startError ? <div className="mt-3 text-sm text-red-700">{startError}</div> : null}
      </Panel>

      <div className="grid gap-5 xl:grid-cols-3">
        <Panel className="p-5">
          <CardHeader icon={ClipboardCheck} title="人工审核进度" />
          {review.started ? <><Metric label="已处理" value={`${review.completed} / ${review.total}`} /><Metric label="待决定" value={`${review.pendingDecisions} 项`} /><Metric label="阻塞" value={`${review.blocking} 项`} /></> : <p className="py-5 text-sm text-slate-500">尚未开始人工审核</p>}
          {overview.human_review_ready ? <CardLink to={`/projects/${encodeURIComponent(projectId)}/review`} label="进入人工审核" /> : <p className="mt-4 text-xs text-slate-400">机器审核完成后可进入人工审核。</p>}
        </Panel>

        <Panel className="p-5">
          <CardHeader icon={FileCheck2} title="评分表" />
          {overview.scoring_available ? <><Metric label="已识别评分项" value={`${overview.scoring_item_count} 项`} /><Metric label="评分要求状态" value={overview.machine_audit_ready ? "已评估" : "已识别"} />{scoring.technical_score_max > 0 ? <Metric label="技术评分满分" value={`${scoring.technical_score_max} 分`} /> : null}</> : <p className="py-5 text-sm text-slate-500">暂未识别评分表</p>}
          {overview.scoring_available ? <CardLink to={scoringTarget(overview)} label="查看评分表" /> : <CardLink to={`/projects/${encodeURIComponent(projectId)}/analysis`} label="查看招标分析" />}
        </Panel>

        <Panel className="p-5">
          <CardHeader icon={SearchCheck} title="事实与材料" />
          <Metric label="待确认事实" value={`${facts.confirmations_required} 项`} />
          <Metric label="待补充项目信息" value={`${facts.missing_project_facts} 项`} />
          <Metric label="待补充材料" value={`${facts.queries_not_executed} 项`} />
          <CardLink to={`/projects/${encodeURIComponent(projectId)}/facts-materials`} label="查看事实与材料" />
        </Panel>
      </div>

      {hasTechnicalDetails(overview.lineage) ? <LineageInfo value={overview.lineage} /> : null}
    </div>
  );
}

function TopField({ label, value }: { label: string; value: string }) { return <div><dt className="text-xs text-slate-400">{label}</dt><dd className="mt-1 text-sm font-medium leading-6 text-slate-800">{value}</dd></div>; }
function CardHeader({ icon: Icon, title }: { icon: typeof CheckCircle2; title: string }) { return <div className="mb-3 flex items-center gap-2 font-semibold text-slate-900"><Icon size={18} className="text-brand-600" />{title}</div>; }
function Metric({ label, value }: { label: string; value: string }) { return <div className="flex items-center justify-between border-b border-slate-100 py-2 text-sm last:border-0"><span className="text-slate-500">{label}</span><strong className="font-semibold text-slate-800">{value}</strong></div>; }
function CardLink({ to, label }: { to: string; label: string }) { return <Link to={to} className="mt-4 inline-flex items-center gap-1 text-sm font-medium text-brand-700">{label}<ArrowRight size={14} /></Link>; }
function hasTechnicalDetails(value: { audited_content_sha256: string; audit_sha256: string; scoring_sha256: string; review_package_id: string }) { return Boolean(value.audited_content_sha256 || value.audit_sha256 || value.scoring_sha256 || value.review_package_id); }
