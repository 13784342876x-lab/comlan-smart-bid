import type { ProjectOverviewDTO } from "../../types/api";

export function userStage(data: ProjectOverviewDTO) {
  const run = data.current_pipeline_run;
  if (data.project.release_status.includes("已生成") || data.project.release_status.includes("已发布")) return "技术正文已生成";
  if (run?.status === "FAILED") return "处理失败";
  if (data.revision_ready) return "待发布";
  if (data.human_review_ready || data.project.workflow_status.includes("人工审核")) return "人工审核";
  if (data.machine_audit_ready || data.project.workflow_status.includes("机器审核")) return "机器审核";
  if (run?.full_bid_generation_active || data.project.workflow_status.includes("生成")) return "标书生成中";
  if (run?.analysis_ready || data.project.workflow_status.includes("写作")) return "事实与材料确认";
  if (run || data.project.workflow_status.includes("分析")) return "招标文件分析中";
  return data.project.workflow_status || "未开始";
}

export function reviewMetrics(data: ProjectOverviewDTO) {
  const progress = data.review_progress;
  const total = progress.issue_decisions_total + progress.fact_confirmations_total + progress.material_verifications_total;
  const completed = progress.issue_decisions_completed + progress.fact_confirmations_completed + progress.material_verifications_completed;
  return {
    started: data.human_review_ready || total > 0,
    total,
    completed,
    pendingDecisions: Math.max(0, progress.issue_decisions_total - progress.issue_decisions_completed),
    blocking: data.actionability_counts.must_decide ?? data.project.blocking_count ?? 0,
  };
}

export function overviewActionTarget(data: ProjectOverviewDTO) {
  const projectId = encodeURIComponent(data.project.project_id);
  if (data.revision_ready) return `/projects/${projectId}/release`;
  if (data.human_review_ready) return `/projects/${projectId}/review`;
  if (data.machine_audit_ready || data.fulltext_ready) return `/projects/${projectId}/fulltext`;
  if (data.current_pipeline_run?.full_bid_generation_active) return `/projects/${projectId}/runs/${encodeURIComponent(data.current_pipeline_run.run_id)}`;
  return data.project.primary_action_url ?? `/projects/${projectId}/setup`;
}

export function scoringTarget(data: ProjectOverviewDTO) {
  const projectId = encodeURIComponent(data.project.project_id);
  return data.machine_audit_ready ? `/projects/${projectId}/scoring` : `/projects/${projectId}/analysis/scoring`;
}
