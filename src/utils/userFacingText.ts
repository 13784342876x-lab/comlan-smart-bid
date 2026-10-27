const placeholderTokenPattern = /\{*PH-(?:FIELD-)?[A-Z0-9-]+\}*/gi;
const internalIdentityPattern = /\b(?:TR-\d+|WC-[A-Z0-9-]+|TASK-[A-Z0-9-]+|FAI-[A-Z0-9-]+|FCA-[A-Z0-9-]+|FSF-[A-Z0-9-]+)\b/gi;

export function userFacingText(value?: string | null) {
  if (!value) return "";
  return value
    .replace(placeholderTokenPattern, "待确认信息")
    .replace(internalIdentityPattern, "")
    .replace(/\s{2,}/g, " ")
    .replace(/^[：:、/\-\s]+/u, "")
    .trim();
}

export function containsPlaceholderToken(value?: string | null) {
  if (!value) return false;
  placeholderTokenPattern.lastIndex = 0;
  return placeholderTokenPattern.test(value);
}

export function placeholderDisplayTitle(title: string, category: string) {
  if (!containsPlaceholderToken(title)) return title;
  const remaining = title
    .replace(placeholderTokenPattern, "")
    .replace(/[：:·\-—\s]+$/u, "")
    .trim();
  if (remaining && !/^(待确认占位事实|占位事实|待确认字段)$/u.test(remaining)) return remaining;
  if (category === "confirmation_required") return "待确认事实";
  if (category === "missing_project_fact") return "待补充项目信息";
  return "待补充材料";
}
