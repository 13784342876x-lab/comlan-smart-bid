import { Search } from "lucide-react";

export interface ReviewQueueEntry {
  kind: "issue" | "fact" | "material";
  traceId: string;
  title: string;
  description: string;
  status: string;
  blocking?: boolean;
}

export function ReviewQueue({ entries, selectedId, search, onSearch, onSelect }: { entries: ReviewQueueEntry[]; selectedId: string | null; search: string; onSearch: (value: string) => void; onSelect: (entry: ReviewQueueEntry) => void }) {
  return <aside className="flex min-h-[650px] flex-col border-r border-slate-200 bg-white"><div className="border-b border-slate-200 p-3"><div className="relative"><Search size={15} className="absolute left-3 top-2.5 text-slate-400" /><input value={search} onChange={(e) => onSearch(e.target.value)} placeholder="搜索问题、事实或材料" className="w-full rounded-md border border-slate-300 py-2 pl-9 pr-3 text-sm outline-none focus:border-brand-500" /></div></div><div className="flex-1 overflow-y-auto">{entries.map((entry) => <button type="button" key={`${entry.kind}-${entry.traceId}`} onClick={() => onSelect(entry)} className={`w-full border-b border-slate-100 px-4 py-3 text-left ${selectedId === entry.traceId ? "border-l-2 border-l-brand-600 bg-brand-50" : "hover:bg-slate-50"}`}><div className="flex items-start justify-between gap-3"><div className="line-clamp-2 text-sm font-medium leading-5 text-slate-800">{entry.title}</div>{entry.blocking ? <span className="shrink-0 text-[11px] font-medium text-red-600">阻塞</span> : null}</div><p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-500">{entry.description}</p><div className="mt-2 text-[11px] text-slate-400">{entry.status}</div></button>)}{!entries.length ? <div className="p-6 text-center text-sm text-slate-400">当前筛选下没有事项</div> : null}</div></aside>;
}
