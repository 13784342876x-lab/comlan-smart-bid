import type { ActionabilityKey } from "../../types/api";
import { StatusIndicator, type SemanticStatus } from "../ui/StatusIndicator";

const meta: Record<ActionabilityKey, { label: string; status: SemanticStatus }> = {
  must_decide: { label: "必须决定", status: "blocking" },
  verification: { label: "事实与材料核验", status: "pending" },
  no_action: { label: "无需修改，待知悉", status: "acknowledged" },
  informational: { label: "背景信息", status: "uncertain" },
};

export function ActionabilityIndicator({ value }: { value: ActionabilityKey }) {
  const item = meta[value];
  return <StatusIndicator status={item.status}>{item.label}</StatusIndicator>;
}
