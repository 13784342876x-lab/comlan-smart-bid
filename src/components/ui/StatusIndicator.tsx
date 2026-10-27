import type { ReactNode } from "react";

export type SemanticStatus =
  | "blocking"
  | "warning"
  | "uncertain"
  | "pending"
  | "acknowledged"
  | "verified"
  | "completed"
  | "not_started"
  | "query_not_executed";

const styles: Record<SemanticStatus, string> = {
  blocking: "border-red-200 bg-red-50 text-red-700",
  warning: "border-amber-200 bg-amber-50 text-amber-700",
  uncertain: "border-sky-200 bg-sky-50 text-sky-700",
  pending: "border-violet-200 bg-violet-50 text-violet-700",
  acknowledged: "border-slate-200 bg-slate-100 text-slate-600",
  verified: "border-emerald-200 bg-emerald-50 text-emerald-700",
  completed: "border-emerald-200 bg-emerald-50 text-emerald-700",
  not_started: "border-slate-200 bg-white text-slate-500",
  query_not_executed: "border-slate-200 bg-slate-100 text-slate-700",
};

export function StatusIndicator({ status, children }: { status: SemanticStatus; children: ReactNode }) {
  return <span className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-medium ${styles[status]}`}>{children}</span>;
}
