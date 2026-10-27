import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ApiError } from "../../api/client";
import { acceptRisk, confirmFact, decideIssue, getVersionedReview, verifyMaterial } from "../../api/review";
import { useProjectLoad } from "../../app/providers/ProjectProvider";
import { LineageInfo } from "../../components/domain/LineageInfo";
import { ReviewDetail, type ReviewSelection } from "../../components/domain/review/ReviewDetail";
import { ReviewProgress } from "../../components/domain/review/ReviewProgress";
import { ReviewQueue, type ReviewQueueEntry } from "../../components/domain/review/ReviewQueue";
import { StaleReviewConflict } from "../../components/domain/review/StaleReviewConflict";
import { EmptyState, ErrorState, LoadingState } from "../../components/ui/PageState";
import type { ActionabilityKey, FactConfirmationCommandDTO, IssueDecisionCommandDTO, MaterialVerificationCommandDTO, ReviewWorkspaceDTO, RiskAcceptanceCommandDTO } from "../../types/api";
import { placeholderDisplayTitle } from "../../utils/userFacingText";

type TabKey = "must_decide" | "facts" | "materials" | "no_action" | "informational";
const tabs: Array<{ key: TabKey; label: string }> = [
  { key: "must_decide", label: "需要我决定" }, { key: "facts", label: "事实确认" },
  { key: "materials", label: "材料核验" }, { key: "no_action", label: "无需修改" },
  { key: "informational", label: "仅供参考" },
];

export function ReviewWorkspacePage() {
  const projectLoad = useProjectLoad(); const projectId = projectLoad.projectId;
  const [searchParams, setSearchParams] = useSearchParams();
  const [data, setData] = useState<ReviewWorkspaceDTO | null>(null); const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true); const [error, setError] = useState<Error | null>(null);
  const [tab, setTab] = useState<TabKey>("must_decide"); const [selectedId, setSelectedId] = useState<string | null>(searchParams.get("item"));
  const [selectedKind, setSelectedKind] = useState<ReviewQueueEntry["kind"]>("issue"); const [search, setSearch] = useState("");
  const [dirty, setDirty] = useState(false); const [conflict, setConflict] = useState(false); const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => { const response = await getVersionedReview(projectId); setData(response.data); setToken(response.etag); setError(null); return response.data; }, [projectId]);
  useEffect(() => { let active = true; setLoading(true); getVersionedReview(projectId).then((response) => { if (active) { setData(response.data); setToken(response.etag); setError(null); } }).catch((cause) => { if (active) setError(cause instanceof Error ? cause : new Error("读取审核数据失败")); }).finally(() => { if (active) setLoading(false); }); return () => { active = false; }; }, [projectId]);
  useEffect(() => { const handler = () => { void load(); }; window.addEventListener("stage13-revision-completed", handler); return () => window.removeEventListener("stage13-revision-completed", handler); }, [load]);
  useEffect(() => { const handler = (event: BeforeUnloadEvent) => { if (dirty) event.preventDefault(); }; window.addEventListener("beforeunload", handler); return () => window.removeEventListener("beforeunload", handler); }, [dirty]);
  useEffect(() => { if (!dirty) return; const guard = (event: MouseEvent) => { const anchor = (event.target as Element | null)?.closest("a"); if (!anchor || anchor.target === "_blank") return; if (!window.confirm("当前审核结论尚未保存。确定离开吗？")) { event.preventDefault(); event.stopPropagation(); } }; document.addEventListener("click", guard, true); return () => document.removeEventListener("click", guard, true); }, [dirty]);

  const queue = useMemo(() => buildQueue(data, tab).filter((item) => `${item.title} ${item.description} ${item.traceId}`.toLowerCase().includes(search.trim().toLowerCase())), [data, search, tab]);
  useEffect(() => { if (!data) return; const requested = searchParams.get("item"); if (requested) { const owner = locate(data, requested); if (owner) { setTab(owner.tab); setSelectedId(requested); setSelectedKind(owner.kind); return; } } const first = buildQueue(data, tab)[0]; if (!selectedId && first) { setSelectedId(first.traceId); setSelectedKind(first.kind); } }, [data, searchParams, selectedId, tab]);
  const selection = useMemo<ReviewSelection | null>(() => { if (!data || !selectedId) return null; if (selectedKind === "fact") { const item = data.fact_confirmations.find((value) => value.trace_id === selectedId); return item ? { kind: "fact", item } : null; } if (selectedKind === "material") { const item = data.material_verifications.find((value) => value.trace_id === selectedId); return item ? { kind: "material", item } : null; } const item = data.groups.flatMap((group) => group.items).find((value) => value.trace_id === selectedId); return item ? { kind: "issue", item } : null; }, [data, selectedId, selectedKind]);

  function choose(entry: ReviewQueueEntry) { if (dirty && !window.confirm("当前审核结论尚未保存。确定切换审核事项吗？")) return; setDirty(false); setSelectedId(entry.traceId); setSelectedKind(entry.kind); setSearchParams({ item: entry.traceId }); }
  function chooseTab(next: TabKey) { if (dirty && !window.confirm("当前审核结论尚未保存。确定切换分类吗？")) return; setDirty(false); setTab(next); const first = buildQueue(data, next)[0]; setSelectedId(first?.traceId ?? null); setSelectedKind(first?.kind ?? "issue"); setSearchParams(first ? { item: first.traceId } : {}); }
  async function runMutation(action: (version: string) => ReturnType<typeof decideIssue>) { if (!token) throw new Error("缺少审核版本令牌，请刷新后重试。"); try { const response = await action(token); setData(response.data.review); setToken(response.etag ?? response.data.version_token); setConflict(false); setDirty(false); projectLoad.reload(); } catch (cause) { if (cause instanceof ApiError && cause.isLineageConflict) setConflict(true); throw cause; } }
  const onIssue = (id: string, value: IssueDecisionCommandDTO) => runMutation((version) => decideIssue(projectId, id, version, value));
  const onFact = (id: string, value: FactConfirmationCommandDTO) => runMutation((version) => confirmFact(projectId, id, version, value));
  const onMaterial = (id: string, value: MaterialVerificationCommandDTO) => runMutation((version) => verifyMaterial(projectId, id, version, value));
  const onRisk = (id: string, value: RiskAcceptanceCommandDTO) => runMutation((version) => acceptRisk(projectId, id, version, value));

  if (loading) return <LoadingState label="正在读取当前审核版本…" />;
  if (error) return <ErrorState error={error} onRetry={() => { setLoading(true); load().finally(() => setLoading(false)); }} />;
  if (!data) return <EmptyState title="暂无审核数据" description="没有找到当前版本对应的审核内容。" />;
  const count = (key: ActionabilityKey) => data.actionability_counts[key] ?? 0;
  return <div className="space-y-5"><div className="flex flex-wrap items-start justify-between gap-4"><div><h2 className="text-xl font-semibold text-slate-900">人工审核</h2><p className="mt-1 text-sm text-slate-500">优先处理真正阻塞下一阶段的业务决定；事实、材料和非阻塞事项保持各自语义。</p></div><div className="text-right text-xs text-slate-400">当前审核版本<br /><span className="font-medium text-slate-600">{data.reviewed_version.label}</span></div></div><div className="flex flex-wrap gap-x-5 gap-y-2 rounded-lg border border-slate-200 bg-white px-4 py-3 text-sm"><span className="font-medium text-red-700">需要决定 {count("must_decide")}</span><span className="text-slate-600">核验型审核事项 {count("verification")}</span><span className="text-slate-600">无需修改 {count("no_action")}</span><span className="text-slate-600">信息 {count("informational")}</span></div><ReviewProgress value={data.progress} />{conflict ? <StaleReviewConflict refreshing={refreshing} onRefresh={() => { setRefreshing(true); load().then(() => setConflict(false)).finally(() => setRefreshing(false)); }} /> : null}<div className="overflow-hidden rounded-xl border border-slate-200 bg-white"><div className="flex gap-1 overflow-x-auto border-b border-slate-200 bg-white p-2">{tabs.map((item) => { const tabCount = item.key === "facts" ? data.fact_confirmations.length : item.key === "materials" ? data.material_verifications.length : count(item.key); return <button key={item.key} onClick={() => chooseTab(item.key)} className={`whitespace-nowrap rounded-md px-3 py-2 text-sm ${tab === item.key ? "bg-brand-50 font-medium text-brand-700" : "text-slate-600 hover:bg-slate-50"}`}>{item.label} <span className="ml-1 tabular-nums text-xs">{tabCount}</span></button>; })}</div><div className="grid min-h-[650px] grid-cols-[330px_minmax(0,1fr)]"><ReviewQueue entries={queue} selectedId={selectedId} search={search} onSearch={setSearch} onSelect={choose} /><ReviewDetail projectId={projectId} selection={selection} editable={tab === "must_decide" || tab === "facts" || tab === "materials"} onIssue={onIssue} onFact={onFact} onMaterial={onMaterial} onRisk={onRisk} onDirtyChange={setDirty} /></div></div><LineageInfo value={data.lineage} /></div>;
}

function buildQueue(data: ReviewWorkspaceDTO | null, tab: TabKey): ReviewQueueEntry[] { if (!data) return []; if (tab === "facts") return data.fact_confirmations.map((item) => ({ kind: "fact", traceId: item.trace_id, title: placeholderDisplayTitle(item.title, "confirmation_required"), description: item.description ?? (item.source_type === "FCA" ? "正文事实性声明待确认" : "项目事实待确认"), status: item.confirmation_status === "pending" ? "待确认" : item.confirmation_status === "confirmed" ? "已确认" : "已驳回" })); if (tab === "materials") return data.material_verifications.map((item) => ({ kind: "material", traceId: item.trace_id, title: item.title, description: item.description ?? "材料依赖", status: item.upstream_status === "query_not_executed" ? "尚未查询" : item.verification_status === "pending" ? "待核验" : item.verification_status })); const group = data.groups.find((item) => item.key === tab); return (group?.items ?? []).map((item) => ({ kind: "issue", traceId: item.trace_id, title: item.title, description: item.machine_issue, status: item.current_decision === "pending" ? (tab === "must_decide" ? "待人工决定" : tab === "no_action" ? "无需修改" : "仅供参考") : item.current_decision ?? "待处理", blocking: item.release_blocking })); }
function locate(data: ReviewWorkspaceDTO, id: string): { tab: TabKey; kind: ReviewQueueEntry["kind"] } | null { if (data.fact_confirmations.some((item) => item.trace_id === id)) return { tab: "facts", kind: "fact" }; if (data.material_verifications.some((item) => item.trace_id === id)) return { tab: "materials", kind: "material" }; for (const group of data.groups) if (group.items.some((item) => item.trace_id === id)) return { tab: group.key === "must_decide" ? "must_decide" : group.key === "no_action" ? "no_action" : group.key === "informational" ? "informational" : "facts", kind: "issue" }; return null; }
