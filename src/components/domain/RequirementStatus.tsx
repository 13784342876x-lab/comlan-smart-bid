const labels: Record<string, string> = {
  present: "已响应",
  partial: "部分响应",
  missing: "缺失",
  uncertain: "不确定",
  pending: "待人工判断",
  not_started: "尚未形成响应",
};

const colors: Record<string, string> = {
  present: "bg-emerald-500 text-emerald-700",
  partial: "bg-amber-500 text-amber-700",
  missing: "bg-red-500 text-red-700",
  uncertain: "bg-slate-400 text-slate-600",
  pending: "bg-blue-500 text-blue-700",
  not_started: "bg-slate-300 text-slate-600",
};

export function requirementStatusLabel(value: string) {
  return labels[value] ?? value;
}

export function RequirementStatus({ value }: { value: string }) {
  const color = colors[value] ?? colors.uncertain;
  return <span className={`inline-flex items-center gap-1.5 text-sm font-medium ${color.split(" ")[1]}`}><span className={`h-1.5 w-1.5 rounded-full ${color.split(" ")[0]}`} />{requirementStatusLabel(value)}</span>;
}
