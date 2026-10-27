import { StatusIndicator } from "../ui/StatusIndicator";

export function ProjectStatus({ workflowStatus, releaseStatus }: { workflowStatus: string; releaseStatus: string }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <StatusIndicator status="pending">{workflowStatus}</StatusIndicator>
      <span className="text-xs text-slate-400">交付状态</span>
      <StatusIndicator status={releaseStatus.includes("可生成") || releaseStatus.includes("已生成") ? "completed" : "not_started"}>{releaseStatus}</StatusIndicator>
    </div>
  );
}
