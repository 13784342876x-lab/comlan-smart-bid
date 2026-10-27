import type { AuditedFulltextDTO, ReviewLocationDTO } from "../../../types/api";

export interface ResolvedFulltextLocation {
  sectionId: string | null;
  paragraphId: string | null;
  source: "explicit" | "requirement" | "finding" | "review" | "score" | "default";
  error: string | null;
}

export function resolveFulltextLocation(data: AuditedFulltextDTO, params: URLSearchParams): ResolvedFulltextLocation {
  const sections = new Set(data.chapters.flatMap((chapter) => chapter.sections.map((section) => section.section_id)));
  const paragraphs = new Map(data.chapters.flatMap((chapter) => chapter.sections.flatMap((section) => section.paragraphs.map((paragraph) => [paragraph.paragraph_id, section.section_id] as const))));
  const paragraph = params.get("paragraph");
  if (paragraph) {
    const owner = paragraphs.get(paragraph);
    return owner ? { sectionId: owner, paragraphId: paragraph, source: "explicit", error: null } : missing("原审核段落在当前审核版本中无法定位。");
  }
  const section = params.get("section");
  if (section) return sections.has(section) ? { sectionId: section, paragraphId: null, source: "explicit", error: null } : missing("原审核小节在当前审核版本中无法定位。");

  const requirementId = params.get("requirement");
  if (requirementId) {
    const item = data.trace_index.requirements.find((value) => value.trace_id === requirementId);
    return item ? fromLocations(item.response_locations, "requirement", sections, paragraphs) : missing("该招标要求不属于当前审核版本。");
  }
  const findingId = params.get("finding");
  if (findingId) {
    const item = data.trace_index.findings.find((value) => value.trace_id === findingId);
    return item ? fromLocations(item.locations, "finding", sections, paragraphs) : missing("该机器发现不属于当前审核版本。");
  }
  const reviewId = params.get("reviewItem");
  if (reviewId) {
    const item = data.trace_index.reviews.find((value) => value.trace_id === reviewId);
    return item ? fromLocations(item.response_locations, "review", sections, paragraphs) : missing("该人工审核事项不属于当前审核版本。");
  }
  const scoreId = params.get("scoreItem");
  if (scoreId) {
    const item = data.trace_index.scores.find((value) => value.trace_id === scoreId);
    return item ? fromLocations(item.response_locations, "score", sections, paragraphs) : missing("该评分项不属于当前审核版本。");
  }
  const first = data.chapters[0]?.sections[0];
  return { sectionId: first?.section_id ?? null, paragraphId: null, source: "default", error: null };
}

function fromLocations(locations: ReviewLocationDTO[], source: ResolvedFulltextLocation["source"], sections: Set<string>, paragraphs: Map<string, string>): ResolvedFulltextLocation {
  for (const location of locations) if (location.paragraph_id && paragraphs.has(location.paragraph_id)) return { sectionId: paragraphs.get(location.paragraph_id) ?? null, paragraphId: location.paragraph_id, source, error: null };
  for (const location of locations) if (location.section_id && sections.has(location.section_id)) return { sectionId: location.section_id, paragraphId: null, source, error: null };
  return missing("该业务对象在当前审核正文中没有可用定位。");
}

function missing(error: string): ResolvedFulltextLocation { return { sectionId: null, paragraphId: null, source: "default", error }; }
