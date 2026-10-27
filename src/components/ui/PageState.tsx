import { AlertCircle, Inbox, LoaderCircle, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";
import { ApiError } from "../../api/client";

export function LoadingState({ label = "正在读取项目数据…" }: { label?: string }) {
  return (
    <div className="flex min-h-64 items-center justify-center rounded-xl border border-slate-200 bg-white text-sm text-slate-500">
      <LoaderCircle className="mr-2 animate-spin text-brand-600" size={19} /> {label}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  const apiError = error instanceof ApiError ? error : null;
  const title = apiError?.isLineageConflict ? "版本已发生变化" : "暂时无法读取数据";
  return (
    <div className="rounded-xl border border-red-200 bg-red-50 p-6">
      <div className="flex items-start gap-3">
        <AlertCircle className="mt-0.5 shrink-0 text-red-600" size={20} />
        <div>
          <h2 className="font-semibold text-red-900">{title}</h2>
          <p className="mt-1 text-sm leading-6 text-red-700">{error.message}</p>
          {onRetry ? (
            <button onClick={onRetry} className="mt-4 inline-flex items-center gap-2 rounded-md bg-white px-3 py-2 text-sm font-medium text-red-700 shadow-sm">
              <RefreshCw size={15} /> 重新读取
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export function EmptyState({ title, description, action }: { title: string; description: string; action?: ReactNode }) {
  return (
    <div className="flex min-h-64 flex-col items-center justify-center rounded-xl border border-dashed border-slate-300 bg-white p-8 text-center">
      <Inbox className="text-slate-400" size={30} />
      <h2 className="mt-3 font-semibold text-slate-800">{title}</h2>
      <p className="mt-1 max-w-lg text-sm leading-6 text-slate-500">{description}</p>
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}
