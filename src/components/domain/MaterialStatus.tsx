import { StatusIndicator } from "../ui/StatusIndicator";

export function MaterialStatus({ status, label }: { status: string; label: string }) {
  if (status === "query_not_executed") return <StatusIndicator status="query_not_executed">尚未查询</StatusIndicator>;
  if (status === "verified") return <StatusIndicator status="verified">已核验</StatusIndicator>;
  if (status === "invalid" || status === "expired") return <StatusIndicator status="warning">{label}</StatusIndicator>;
  return <StatusIndicator status="pending">{label}</StatusIndicator>;
}
