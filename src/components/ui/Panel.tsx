import type { ReactNode } from "react";

export function Panel({ children, className = "", title }: { children: ReactNode; className?: string; title?: string }) {
  return (
    <section className={`rounded-xl border border-slate-200 bg-white shadow-panel ${className}`}>
      {title ? <div className="border-b border-slate-100 px-5 py-4 font-semibold text-slate-900">{title}</div> : null}
      {children}
    </section>
  );
}
