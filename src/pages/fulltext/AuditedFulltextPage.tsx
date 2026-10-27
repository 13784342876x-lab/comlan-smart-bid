import { PanelRightClose, PanelRightOpen } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { getAuditedFulltext } from "../../api/fulltext";
import { useApiQuery } from "../../api/useApiQuery";
import { useProject } from "../../app/providers/ProjectProvider";
import { DocumentParagraph } from "../../components/domain/fulltext/DocumentParagraph";
import { DocumentTree } from "../../components/domain/fulltext/DocumentTree";
import { FulltextContextPanel } from "../../components/domain/fulltext/FulltextContextPanel";
import { resolveFulltextLocation } from "../../components/domain/fulltext/fulltextLocation";
import { VersionContextBar } from "../../components/domain/fulltext/VersionContextBar";
import { EmptyState, ErrorState, LoadingState } from "../../components/ui/PageState";
import type { FulltextParagraphDTO, ReviewLocationDTO } from "../../types/api";

const BUSINESS_PARAMS = ["requirement", "finding", "reviewItem", "scoreItem"];

export function AuditedFulltextPage() {
  const project = useProject();
  const query = useApiQuery((signal) => getAuditedFulltext(project.projectId, signal), [project.projectId]);
  const [searchParams, setSearchParams] = useSearchParams();
  const [activeSection, setActiveSection] = useState<string | null>(null);
  const [selectedParagraphId, setSelectedParagraphId] = useState<string | null>(null);
  const [contextOpen, setContextOpen] = useState(true);
  const contentRef = useRef<HTMLDivElement>(null);
  const programmatic = useRef(false);
  const resolved = useMemo(() => query.data ? resolveFulltextLocation(query.data, searchParams) : null, [query.data, searchParams]);

  useEffect(() => {
    if (!query.data || !resolved) return;
    setActiveSection(resolved.sectionId);
    setSelectedParagraphId(resolved.paragraphId);
    if (!resolved.sectionId) return;
    programmatic.current = true;
    const timer = window.setTimeout(() => {
      const target = resolved.paragraphId ? document.getElementById(`paragraph-${resolved.paragraphId}`) : document.getElementById(`section-${resolved.sectionId}`);
      target?.scrollIntoView({ behavior: "smooth", block: "start" });
      window.setTimeout(() => { programmatic.current = false; }, 650);
    }, 80);
    return () => window.clearTimeout(timer);
  }, [query.data, resolved]);

  useEffect(() => {
    const root = contentRef.current;
    if (!root || !query.data) return;
    const observer = new IntersectionObserver((entries) => {
      const current = entries.filter((entry) => entry.isIntersecting).sort((left, right) => right.intersectionRatio - left.intersectionRatio)[0];
      const sectionId = (current?.target as HTMLElement | undefined)?.dataset.sectionId;
      if (!sectionId) return;
      setActiveSection(sectionId);
      if (!programmatic.current && sectionId !== resolved?.sectionId) {
        const url = new URL(window.location.href);
        url.searchParams.set("section", sectionId);
        url.searchParams.delete("paragraph");
        BUSINESS_PARAMS.forEach((key) => url.searchParams.delete(key));
        window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}`);
        setSelectedParagraphId(null);
      }
    }, { root, rootMargin: "-8% 0px -78% 0px", threshold: [0.05, 0.25, 0.6] });
    root.querySelectorAll<HTMLElement>("[data-section-id]").forEach((node) => observer.observe(node));
    return () => observer.disconnect();
  }, [query.data, resolved]);

  if (query.loading) return <LoadingState label="正在装载当前审核正文…" />;
  if (query.error) return <ErrorState error={query.error} onRetry={query.reload} />;
  if (!query.data) return <EmptyState title="暂无审核正文" description="当前项目没有可用的 audited fulltext。" />;
  const data = query.data;
  const lineageMatch = project.currentReviewedVersion.content_sha256 === data.version.audited_content_sha256;
  const currentSection = data.chapters.flatMap((chapter) => chapter.sections).find((section) => section.section_id === activeSection) ?? null;
  const currentParagraph = currentSection?.paragraphs.find((paragraph) => paragraph.paragraph_id === selectedParagraphId) ?? null;

  function navigate(sectionId: string, paragraphId?: string | null, extra?: Record<string, string>) {
    const next = new URLSearchParams(); next.set("section", sectionId); if (paragraphId) next.set("paragraph", paragraphId); Object.entries(extra ?? {}).forEach(([key, value]) => next.set(key, value)); setSearchParams(next);
  }
  function navigateLocation(location: ReviewLocationDTO) { if (location.section_id) navigate(location.section_id, location.paragraph_id); }
  function selectParagraph(sectionId: string, paragraph: FulltextParagraphDTO) { setSelectedParagraphId(paragraph.paragraph_id); navigate(sectionId, paragraph.paragraph_id); }

  return <div className="min-w-[920px] overflow-hidden rounded-xl border border-slate-200 bg-white shadow-panel"><div className="flex flex-wrap items-start justify-between gap-4 border-b border-slate-200 bg-slate-50 px-4 py-3"><div><h2 className="text-lg font-semibold text-slate-900">审核正文</h2><p className="mt-1 text-xs text-slate-500">只读 · 当前审核版本</p></div><button type="button" onClick={() => setContextOpen((value) => !value)} className="inline-flex items-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-2 text-xs font-medium text-slate-700">{contextOpen ? <PanelRightClose size={14} /> : <PanelRightOpen size={14} />}{contextOpen ? "收起追踪" : "展开追踪"}</button></div><VersionContextBar value={data} lineageMatch={lineageMatch} />{resolved?.error ? <div className="border-b border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">{resolved.error}</div> : null}<div className={`grid h-[calc(100vh-250px)] min-h-[620px] ${contextOpen ? "grid-cols-[220px_minmax(520px,1fr)_320px]" : "grid-cols-[220px_minmax(620px,1fr)]"}`}><div className="border-r border-slate-200"><DocumentTree chapters={data.chapters} activeSection={activeSection} onNavigate={(sectionId) => navigate(sectionId)} /></div><main ref={contentRef} className="overflow-y-auto bg-[#fbfbfa] px-10 py-8"><article className="mx-auto max-w-[900px] rounded-lg border border-slate-200 bg-white px-12 py-10 shadow-sm">{data.chapters.map((chapter) => <section key={chapter.chapter_id} className="mb-14 last:mb-0"><header className="mb-8 border-b-2 border-slate-800 pb-4"><h1 className="text-2xl font-semibold tracking-tight text-slate-950">{chapter.title}</h1></header>{chapter.sections.map((section) => <section key={section.section_id} id={`section-${section.section_id}`} data-section-id={section.section_id} className={`mb-10 scroll-mt-6 ${resolved?.sectionId === section.section_id && !resolved.paragraphId ? "rounded-lg bg-brand-50/50 px-3 py-3 ring-1 ring-brand-100" : ""}`}><div className="mb-4"><h2 className="text-xl font-semibold text-slate-900">{section.heading}</h2></div>{section.paragraphs.length ? <div className="ml-7 space-y-2">{section.paragraphs.map((paragraph) => <DocumentParagraph key={paragraph.paragraph_id} value={paragraph} sectionTrace={section.trace} selected={selectedParagraphId === paragraph.paragraph_id} highlighted={resolved?.paragraphId === paragraph.paragraph_id} onSelect={() => selectParagraph(section.section_id, paragraph)} />)}</div> : <button type="button" onClick={() => { setActiveSection(section.section_id); setSelectedParagraphId(null); }} className="w-full rounded-md border border-dashed border-slate-300 bg-slate-50 px-4 py-6 text-left text-sm text-slate-400">本小节在当前审核正文中无可见内容。</button>}</section>)}</section>)}</article></main>{contextOpen ? <div className="border-l border-slate-200"><FulltextContextPanel data={data} projectId={project.projectId} section={currentSection} paragraph={currentParagraph} onNavigate={navigateLocation} /></div> : null}</div></div>;
}
