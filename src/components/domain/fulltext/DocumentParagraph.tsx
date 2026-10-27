import { AlertTriangle, CheckCircle2, ListChecks, Sigma } from "lucide-react";
import type { FulltextParagraphDTO, FulltextTraceRefsDTO } from "../../../types/api";

export function DocumentParagraph({ value, sectionTrace, selected, highlighted, onSelect }: { value: FulltextParagraphDTO; sectionTrace: FulltextTraceRefsDTO; selected: boolean; highlighted: boolean; onSelect: () => void }) {
  const trace = mergeTrace(sectionTrace, value.trace);
  return <div id={`paragraph-${value.paragraph_id}`} data-paragraph-id={value.paragraph_id} onClick={onSelect} className={`group relative scroll-mt-28 rounded-md border-l-2 px-5 py-3 transition-colors ${highlighted ? "border-brand-500 bg-brand-50 ring-1 ring-inset ring-brand-100" : selected ? "border-slate-400 bg-slate-50" : "border-transparent hover:bg-slate-50"}`}><div className="absolute -left-8 top-4 flex w-7 flex-col items-end gap-1 text-slate-400">{trace.requirement_ids.length ? <span title="关联招标要求"><ListChecks size={13} /></span> : null}{trace.finding_ids.length ? <span title="关联机器发现" className="text-amber-600"><AlertTriangle size={13} /></span> : null}{trace.review_item_ids.length ? <span title="关联人工审核" className="text-emerald-600"><CheckCircle2 size={13} /></span> : null}{trace.score_item_ids.length ? <span title="关联评分项"><Sigma size={13} /></span> : null}</div><ParagraphBody value={value} /></div>;
}

function ParagraphBody({ value }: { value: FulltextParagraphDTO }) {
  if (value.content_type === "table") return <SafeTable text={value.text} />;
  if (value.content_type === "list") { const lines = value.text.split(/\r?\n/); return <ul className="list-disc space-y-2 pl-5 text-[15px] leading-7 text-slate-700">{lines.filter(Boolean).map((line, index) => <li key={index}>{renderTokens(line.replace(/^(?:[-*+]\s+|\d+[.、)]\s*)/, ""))}</li>)}</ul>; }
  return <p className="whitespace-pre-wrap text-[15px] leading-8 text-slate-700">{renderTokens(value.text)}</p>;
}

function SafeTable({ text }: { text: string }) {
  const rows = text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean).map((line) => line.replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim()));
  const content = rows.filter((row) => !row.every((cell) => /^:?-{3,}:?$/.test(cell)));
  if (!content.length) return null;
  return <div className="overflow-x-auto rounded-md border border-slate-200"><table className="min-w-full border-collapse text-left text-sm"><thead className="bg-slate-100 text-slate-700"><tr>{content[0].map((cell, index) => <th key={index} className="whitespace-nowrap border-b border-r border-slate-200 px-3 py-2 font-semibold last:border-r-0">{renderTokens(cell)}</th>)}</tr></thead><tbody>{content.slice(1).map((row, rowIndex) => <tr key={rowIndex} className="even:bg-slate-50/60">{row.map((cell, cellIndex) => <td key={cellIndex} className="min-w-32 border-b border-r border-slate-100 px-3 py-2 align-top leading-6 last:border-r-0">{renderTokens(cell)}</td>)}</tr>)}</tbody></table></div>;
}

function renderTokens(text: string) {
  const parts = text.split(/(\{\{[^{}]+\}\})/g);
  return parts.map((part, index) => { const match = /^\{\{([^{}]+)\}\}$/.exec(part); return match ? <span key={index} title="待确认信息" className="mx-0.5 inline-flex rounded border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-xs font-medium text-amber-800">待确认字段</span> : part; });
}

export function mergeTrace(...values: FulltextTraceRefsDTO[]): FulltextTraceRefsDTO { const keys: Array<keyof FulltextTraceRefsDTO> = ["requirement_ids", "finding_ids", "score_item_ids", "review_item_ids", "fact_refs", "material_refs"]; return Object.fromEntries(keys.map((key) => [key, [...new Set(values.flatMap((value) => value[key]))]])) as unknown as FulltextTraceRefsDTO; }
