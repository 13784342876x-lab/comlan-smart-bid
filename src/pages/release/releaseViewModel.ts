import type { ReleaseStatusDTO } from "../../types/api";

const blockerLabels: Record<string, string> = {
  review_not_finalized: "人工审核尚未完成",
  pending_issue_decisions: "仍有审核项待处理",
  pending_fact_confirmations: "仍有事实待确认",
  pending_material_verifications: "仍有材料待核验",
  revision_required: "仍有 AI 修改待完成",
  risk_acceptance_required: "仍有风险接受待确认",
  lineage_mismatch: "当前正文与审核结果不一致，请刷新后重试",
};

export function releaseView(status: ReleaseStatusDTO, publishing: boolean) {
  return {
    canPublish: status.releaseable && !publishing,
    showDownloads: status.status === "published" && Boolean(status.docx && status.pdf),
    statusLabel: status.status === "published" ? "已生成" : status.releaseable ? "待生成" : "暂不可生成",
    blockers: status.blocking_reasons.map((code) => blockerLabels[code] ?? "当前版本尚未满足发布条件"),
    actionLabel: publishing ? "正在生成技术标正文…" : "生成技术标正文",
  };
}
