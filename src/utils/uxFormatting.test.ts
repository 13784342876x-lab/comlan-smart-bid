import assert from "node:assert/strict";
import test from "node:test";

import { formatLocalDateTime } from "./dateTime.ts";
import { containsPlaceholderToken, placeholderDisplayTitle } from "./userFacingText.ts";

test("ISO timestamps use browser-local fields without ISO separators or timezone suffixes", () => {
  const input = "2026-09-17T08:57:54.885033+00:00";
  const parsed = new Date(input);
  const pad = (part: number) => String(part).padStart(2, "0");
  const expected = `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())} ${pad(parsed.getHours())}:${pad(parsed.getMinutes())}:${pad(parsed.getSeconds())}`;
  const displayed = formatLocalDateTime(input);
  assert.equal(displayed, expected);
  assert.doesNotMatch(displayed, /T|\.\d{3}|(?:Z|[+-]\d\d:\d\d)$/u);
});

test("placeholder identity is hidden while human titles remain unchanged", () => {
  assert.equal(containsPlaceholderToken("待确认占位事实：{{PH-FIELD-7D8D757412FD}}"), true);
  assert.equal(placeholderDisplayTitle("待确认占位事实：{{PH-FIELD-7D8D757412FD}}", "confirmation_required"), "待确认事实");
  assert.equal(placeholderDisplayTitle("ISO 27001 信息安全管理体系认证证书", "evidence_query_not_executed"), "ISO 27001 信息安全管理体系认证证书");
});
