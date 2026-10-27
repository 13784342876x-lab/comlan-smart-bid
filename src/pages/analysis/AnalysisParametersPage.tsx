import { Search, TableProperties } from "lucide-react";
import { useMemo, useState } from "react";
import { getTenderParameters } from "../../api/analysis";
import { useApiQuery } from "../../api/useApiQuery";
import { useProject } from "../../app/providers/ProjectProvider";
import { SourceReference } from "../../components/domain/SourceReference";
import { EmptyState, ErrorState, LoadingState } from "../../components/ui/PageState";
import { Panel } from "../../components/ui/Panel";
import type { TenderParameterTableDTO } from "../../types/api";

export function AnalysisParametersPage() {
  const { projectId } = useProject();
  const [search, setSearch] = useState("");
  const query = useApiQuery((signal) => getTenderParameters(projectId, signal), [projectId]);
  const tables = useMemo(() => filterTables(query.data?.tables ?? [], search), [query.data, search]);
  if (query.loading && !query.data) return <LoadingState label="正在读取技术参数…" />;
  if (query.error) return <ErrorState error={query.error} onRetry={query.reload} />;
  if (!query.data?.tables.length) return <EmptyState title="没有结构化技术参数" description="当前写作前 artifact 没有可安全展示的参数表，页面不会在 Web 中重新解释参数语义。" />;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-4"><div><h3 className="text-lg font-semibold text-slate-950">技术参数</h3><p className="mt-1 text-sm text-slate-500">大型采购清单与参数表在独立工作区阅读，不挤压普通 Requirement Matrix。</p></div><div className="text-right text-xs text-slate-500"><div className="text-xl font-semibold text-slate-900">{query.data.row_count} 行</div>{query.data.table_count} 个参数表</div></div>
      <label className="flex max-w-xl items-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-2"><Search size={16} className="text-slate-400" /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索 CPU、SSD、端口、质保、虚拟化…" className="min-w-0 flex-1 bg-transparent text-sm outline-none" /></label>
      {tables.length ? tables.map((table) => <ParameterTable key={table.table_id} value={table} search={search} />) : <EmptyState title="没有匹配的技术参数" description="换一个关键词或清除搜索条件。" />}
    </div>
  );
}

function ParameterTable({ value, search }: { value: TenderParameterTableDTO; search: string }) {
  const query = search.trim().toLocaleLowerCase();
  const visibleRows = query ? value.rows.filter((row) => row.some((cell) => cell.text.toLocaleLowerCase().includes(query))) : value.rows;
  return <Panel>
    <div className="flex flex-wrap items-start justify-between gap-4 border-b border-slate-100 px-5 py-4"><div><div className="flex items-center gap-2 font-semibold text-slate-900"><TableProperties size={17} className="text-brand-600" />{value.title}</div><div className="mt-1 text-xs text-slate-400">{value.requirement_id} · {visibleRows.length}/{value.row_count} 行 · {value.column_count} 列{value.mandatory_marker ? ` · ${value.mandatory_marker}` : ""}</div></div><div className="w-full max-w-sm"><SourceReference value={value.source_reference} compact /></div></div>
    {value.rows.length ? <div className="max-h-[620px] overflow-auto"><table className="min-w-max border-collapse text-left text-xs"><tbody>{visibleRows.map((row, rowIndex) => <tr key={`${value.table_id}-${rowIndex}`} className={rowIndex === 0 && !query ? "sticky top-0 z-10 bg-slate-100 font-semibold text-slate-800" : "bg-white text-slate-600 hover:bg-brand-50/30"}>{row.map((cell) => <td key={`${cell.row}-${cell.column}`} rowSpan={cell.rowspan} colSpan={cell.colspan} className="max-w-[520px] min-w-[120px] border border-slate-200 px-3 py-2 align-top leading-5"><HighlightedText text={cell.text} search={search} /></td>)}</tr>)}</tbody></table></div> : <pre className="max-h-[620px] overflow-auto whitespace-pre-wrap p-5 text-xs leading-5 text-slate-700">{value.raw_text}</pre>}
  </Panel>;
}

function HighlightedText({ text, search }: { text: string; search: string }) {
  const query = search.trim();
  if (!query) return <>{text}</>;
  const index = text.toLocaleLowerCase().indexOf(query.toLocaleLowerCase());
  if (index < 0) return <>{text}</>;
  return <>{text.slice(0, index)}<mark className="bg-amber-100 text-inherit">{text.slice(index, index + query.length)}</mark>{text.slice(index + query.length)}</>;
}

function filterTables(tables: TenderParameterTableDTO[], search: string) {
  const query = search.trim().toLocaleLowerCase();
  if (!query) return tables;
  return tables.filter((table) => table.title.toLocaleLowerCase().includes(query) || table.rows.some((row) => row.some((cell) => cell.text.toLocaleLowerCase().includes(query))) || table.raw_text?.toLocaleLowerCase().includes(query));
}
