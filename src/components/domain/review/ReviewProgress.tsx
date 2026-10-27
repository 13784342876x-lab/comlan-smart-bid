import type { ReviewProgressDTO } from "../../../types/api";

export function ReviewProgress({ value }: { value: ReviewProgressDTO }) {
  const rows = [
    ["问题决策", value.issue_decisions_completed, value.issue_decisions_total],
    ["事实确认", value.fact_confirmations_completed, value.fact_confirmations_total],
    ["材料核验", value.material_verifications_completed, value.material_verifications_total],
  ] as const;
  return (
    <div className="grid gap-3 rounded-xl border border-slate-200 bg-white p-4 md:grid-cols-3">
      {rows.map(([label, done, total]) => (
        <div key={label}>
          <div className="flex justify-between text-xs text-slate-500"><span>{label}</span><span>{done} / {total}</span></div>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-100"><div className="h-full bg-brand-500" style={{ width: `${total ? Math.round((done / total) * 100) : 0}%` }} /></div>
        </div>
      ))}
    </div>
  );
}
