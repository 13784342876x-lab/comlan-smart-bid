import { RefreshCw, TriangleAlert } from "lucide-react";

export function StaleReviewConflict({ onRefresh, refreshing }: { onRefresh: () => void; refreshing: boolean }) {
  return (
    <div className="rounded-lg border border-amber-300 bg-amber-50 p-4" role="alert">
      <div className="flex gap-3">
        <TriangleAlert className="mt-0.5 shrink-0 text-amber-700" size={18} />
        <div>
          <h4 className="font-semibold text-amber-900">审核内容已发生变化</h4>
          <p className="mt-1 text-sm leading-6 text-amber-800">你当前查看的审核版本已经不是最新版本。未保存输入仍保留在编辑器中，不会被静默覆盖。</p>
          <button type="button" disabled={refreshing} onClick={onRefresh} className="mt-3 inline-flex items-center gap-2 rounded-md border border-amber-400 bg-white px-3 py-2 text-sm font-medium text-amber-900 disabled:opacity-50">
            <RefreshCw size={14} className={refreshing ? "animate-spin" : ""} /> 刷新最新审核并保留输入
          </button>
        </div>
      </div>
    </div>
  );
}
