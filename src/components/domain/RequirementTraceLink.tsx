import { Braces } from "lucide-react";

export function RequirementTraceLink({ traceId }: { traceId: string }) {
  return (
    <span className="inline-flex items-center gap-1 font-mono text-[11px] text-slate-400" title="内部追踪标识">
      <Braces size={12} /> {traceId}
    </span>
  );
}
