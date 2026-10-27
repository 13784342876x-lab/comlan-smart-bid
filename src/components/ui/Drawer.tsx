import { X } from "lucide-react";
import { useEffect, type ReactNode } from "react";

export function Drawer({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", closeOnEscape);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", closeOnEscape);
      document.body.style.overflow = previous;
    };
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50" role="dialog" aria-modal="true" aria-label={title}>
      <button className="absolute inset-0 cursor-default bg-slate-950/25" aria-label="关闭详情" onClick={onClose} />
      <section className="absolute inset-y-0 right-0 flex w-[min(760px,calc(100vw-260px))] min-w-[640px] flex-col border-l border-slate-200 bg-slate-50 shadow-2xl">
        <header className="flex items-center justify-between border-b border-slate-200 bg-white px-6 py-4">
          <div className="min-w-0"><div className="text-xs text-slate-400">Requirement Detail</div><h2 className="truncate text-base font-semibold text-slate-900">{title}</h2></div>
          <button className="rounded-md p-2 text-slate-500 hover:bg-slate-100 hover:text-slate-900" onClick={onClose} aria-label="关闭"><X size={19} /></button>
        </header>
        <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto p-6">{children}</div>
      </section>
    </div>
  );
}
