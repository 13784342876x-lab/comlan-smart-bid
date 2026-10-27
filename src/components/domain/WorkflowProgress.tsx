import { Check, Circle, LockKeyhole } from "lucide-react";
import type { WorkflowStepDTO } from "../../types/api";

export function WorkflowProgress({ steps }: { steps: WorkflowStepDTO[] }) {
  return (
    <ol className="grid gap-2 md:grid-cols-5">
      {steps.map((step) => {
        const active = step.status === "active";
        const complete = step.status === "completed";
        const blocked = step.status === "blocked";
        const Icon = complete ? Check : blocked ? LockKeyhole : Circle;
        return (
          <li key={step.key} className={`rounded-lg border p-3 ${active ? "border-brand-300 bg-brand-50" : "border-slate-200 bg-white"}`}>
            <div className="flex items-center gap-2 text-sm font-medium text-slate-800">
              <Icon size={15} className={complete ? "text-emerald-600" : active ? "text-brand-600" : "text-slate-400"} />
              {step.label}
            </div>
            <p className="mt-2 text-xs leading-5 text-slate-500">{step.detail}</p>
          </li>
        );
      })}
    </ol>
  );
}
