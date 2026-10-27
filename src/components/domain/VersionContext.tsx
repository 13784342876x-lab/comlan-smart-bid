import { GitCommitHorizontal } from "lucide-react";
import type { VersionRefDTO } from "../../types/api";

export function VersionContext({ current, reviewed }: { current: VersionRefDTO; reviewed: VersionRefDTO }) {
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-slate-500">
      <span className="inline-flex items-center gap-1.5"><GitCommitHorizontal size={14} />{current.label}</span>
      <span>当前查看：<strong className="font-medium text-slate-700">{reviewed.label}</strong></span>
    </div>
  );
}
