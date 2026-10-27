import { StatusIndicator } from "../ui/StatusIndicator";

export function FactStatus({ status }: { status: string }) {
  if (status === "verified" || status === "confirmed") return <StatusIndicator status="verified">已确认</StatusIndicator>;
  if (status === "missing") return <StatusIndicator status="warning">缺少事实</StatusIndicator>;
  return <StatusIndicator status="pending">待确认</StatusIndicator>;
}
