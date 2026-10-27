import { useEffect, useMemo, useState } from "react";
import { ShieldAlert } from "lucide-react";
import type { IssueDecisionCommandDTO, ReviewItemDTO, RevisionOperationDTO, RiskAcceptanceCommandDTO } from "../../../types/api";
import { getAuditedFulltext } from "../../../api/fulltext";
import { getCurrentAIModify, getVersionedReview, startAIModify } from "../../../api/review";
import { useProjectLoad } from "../../../app/providers/ProjectProvider";
import { canRunAIRevision, canSelectMachineRevision } from "./issueDecisionViewModel";

type SaveState = "idle" | "saving" | "success" | "error";

function initialDecision(item: ReviewItemDTO): "accepted" | "modified" | "rejected" {
  if (item.current_decision === "accepted" || item.current_decision === "modified" || item.current_decision === "rejected") {
    return item.current_decision;
  }
  return canSelectMachineRevision(item) ? "accepted" : "modified";
}

export function IssueDecisionEditor({
  item,
  onSave,
  onRisk,
  onDirtyChange,
}: {
  item: ReviewItemDTO;
  onSave: (value: IssueDecisionCommandDTO) => Promise<void>;
  onRisk: (value: RiskAcceptanceCommandDTO) => Promise<void>;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const projectLoad = useProjectLoad();
  const [decision, setDecision] = useState<"accepted" | "modified" | "rejected">(initialDecision(item));
  const [instruction, setInstruction] = useState(item.revision_instruction ?? "");
  const [rejection, setRejection] = useState(item.rejection_reason ?? "");
  const [comment, setComment] = useState(item.optional_comment ?? "");
  const [machineLocation, setMachineLocation] = useState(item.use_machine_location);
  const [sectionId, setSectionId] = useState(String(item.location_override?.section_id ?? ""));
  const [paragraphId, setParagraphId] = useState(String(item.location_override?.paragraph_id ?? ""));
  const [locationNote, setLocationNote] = useState(String(item.location_override?.location_note ?? ""));
  const [locationReason, setLocationReason] = useState(item.location_override_reason ?? "");
  const [changeReason, setChangeReason] = useState("");
  const [state, setState] = useState<SaveState>("idle");
  const [error, setError] = useState<string | null>(null);
  const [riskOpen, setRiskOpen] = useState(false);
  const [revisionOperation, setRevisionOperation] = useState<RevisionOperationDTO | null>(null);
  const [aiStarting, setAIStarting] = useState(false);

  const baselineDecision = initialDecision(item);
  const dirty = useMemo(() => decision !== baselineDecision
    || instruction !== (item.revision_instruction ?? "")
    || rejection !== (item.rejection_reason ?? "")
    || comment !== (item.optional_comment ?? "")
    || machineLocation !== item.use_machine_location
    || sectionId !== String(item.location_override?.section_id ?? "")
    || paragraphId !== String(item.location_override?.paragraph_id ?? "")
    || locationNote !== String(item.location_override?.location_note ?? "")
    || locationReason !== (item.location_override_reason ?? "")
    || Boolean(changeReason), [baselineDecision, changeReason, comment, decision, instruction, item, locationNote, locationReason, machineLocation, paragraphId, rejection, sectionId]);
  useEffect(() => onDirtyChange(dirty), [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);
  useEffect(() => {
    let active = true;
    getCurrentAIModify(projectLoad.projectId).then((value) => {
      if (active) setRevisionOperation(value);
    }).catch((cause) => {
      if (active) setError(cause instanceof Error ? cause.message : "无法读取 AI 修改状态");
    });
    return () => { active = false; };
  }, [projectLoad.projectId]);

  const existing = item.current_decision !== "pending";
  const operationActive = revisionOperation?.status === "queued" || revisionOperation?.status === "running";
  const savedForAI = canRunAIRevision(item);
  useEffect(() => {
    if (!operationActive) return;
    const timer = window.setInterval(async () => {
      try {
        const next = await getCurrentAIModify(projectLoad.projectId);
        setRevisionOperation(next);
        if (next?.status === "completed") {
          await getAuditedFulltext(projectLoad.projectId);
          projectLoad.reload();
          window.dispatchEvent(new Event("stage13-revision-completed"));
        }
        if (next?.status === "failed") {
          setError(next.error_message ?? "AI修改失败");
        }
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "无法刷新 AI 修改状态");
      }
    }, 2500);
    return () => window.clearInterval(timer);
  }, [operationActive, projectLoad.projectId, projectLoad.reload]);

  async function startAI() {
    setAIStarting(true); setError(null);
    try {
      const latest = await getVersionedReview(projectLoad.projectId);
      const saved = latest.data.groups.flatMap((group) => group.items).find(
        (value) => value.trace_id === item.trace_id && value.source_type === item.source_type,
      );
      if (!saved || !canRunAIRevision(saved)) {
        throw new Error("请先保存修改结论并完成当前轮必要审核。");
      }
      if (!latest.etag) throw new Error("缺少最新审核版本令牌，请刷新后重试。");
      setRevisionOperation(await startAIModify(
        projectLoad.projectId,
        saved.trace_id,
        saved.source_type,
        latest.etag,
      ));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "AI修改启动失败");
    } finally {
      setAIStarting(false);
    }
  }
  async function submit() {
    setState("saving"); setError(null);
    try {
      await onSave({
        decision,
        revision_instruction: decision === "modified" ? instruction : null,
        rejection_reason: decision === "rejected" ? rejection : null,
        optional_comment: comment || null,
        use_machine_location: decision === "rejected" ? true : machineLocation,
        location_override: decision === "rejected" || machineLocation ? null : {
          ...(sectionId ? { section_id: sectionId } : {}),
          ...(paragraphId ? { paragraph_id: paragraphId } : {}),
          ...(locationNote ? { location_note: locationNote } : {}),
        },
        location_override_reason: decision === "rejected" || machineLocation ? null : locationReason,
        change_reason: existing ? changeReason : null,
      });
      setState("success"); setChangeReason(""); onDirtyChange(false);
    } catch (cause) {
      setState("error"); setError(cause instanceof Error ? cause.message : "保存失败");
    }
  }
  const invalid = (decision === "modified" && !instruction.trim()) || (decision === "rejected" && !rejection.trim()) || (!machineLocation && !(sectionId || paragraphId || locationNote)) || (!machineLocation && !locationReason.trim()) || (existing && !changeReason.trim());
  const decisionOptions: ReadonlyArray<readonly ["accepted" | "modified" | "rejected", string, string]> = [
    ...(canSelectMachineRevision(item) ? [["accepted", "按建议修改", "采纳审核问题和现有修改建议"] as const] : []),
    ["modified", "自定义修改方案", "采纳问题，自行给出修改要求"],
    ["rejected", "不采纳该问题", "认为机器判断不适用"],
  ];
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5">
      <h3 className="font-semibold text-slate-900">人工结论</h3>
      <div className="mt-4 grid gap-2 md:grid-cols-3">
        {decisionOptions.map(([value, label, help]) => (
          <label key={value} className={`cursor-pointer rounded-lg border p-3 ${decision === value ? "border-brand-400 bg-brand-50" : "border-slate-200"}`}>
            <div className="flex gap-2"><input type="radio" checked={decision === value} onChange={() => setDecision(value)} /><span className="text-sm font-medium text-slate-800">{label}</span></div>
            <p className="mt-1 pl-5 text-xs text-slate-500">{help}</p>
          </label>
        ))}
      </div>
      {decision === "modified" ? <Field label="修改要求 *"><textarea value={instruction} onChange={(e) => setInstruction(e.target.value)} rows={4} className={inputClass} /></Field> : null}
      {decision === "rejected" ? <Field label="不采纳理由 *"><textarea value={rejection} onChange={(e) => setRejection(e.target.value)} rows={3} className={inputClass} /></Field> : null}
      {decision !== "rejected" ? <div className="mt-4"><div className="text-sm font-medium text-slate-700">修改位置</div><label className="mt-2 flex gap-2 text-sm"><input type="radio" checked={machineLocation} onChange={() => setMachineLocation(true)} />使用机器建议位置</label><label className="mt-2 flex gap-2 text-sm"><input type="radio" checked={!machineLocation} onChange={() => setMachineLocation(false)} />指定其他位置</label>{!machineLocation ? <details className="mt-3 rounded-lg bg-slate-50 p-3"><summary className="cursor-pointer text-sm font-medium text-slate-600">高级定位</summary><div className="mt-3 grid gap-3 md:grid-cols-2"><input className={inputClass} placeholder="小节定位标识" value={sectionId} onChange={(e) => setSectionId(e.target.value)} /><input className={inputClass} placeholder="段落定位标识（可选）" value={paragraphId} onChange={(e) => setParagraphId(e.target.value)} /><input className={`${inputClass} md:col-span-2`} placeholder="位置说明（可选）" value={locationNote} onChange={(e) => setLocationNote(e.target.value)} /><input className={`${inputClass} md:col-span-2`} placeholder="更改位置的原因 *" value={locationReason} onChange={(e) => setLocationReason(e.target.value)} /></div></details> : null}</div> : null}
      <Field label="审核备注"><textarea value={comment} onChange={(e) => setComment(e.target.value)} rows={2} className={inputClass} /></Field>
      {existing ? <Field label="变更已有结论的原因 *"><input value={changeReason} onChange={(e) => setChangeReason(e.target.value)} className={inputClass} /></Field> : null}
      {error ? <p className="mt-3 text-sm text-red-600">{error}</p> : null}
      <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-4">
        <button type="button" onClick={() => setRiskOpen(true)} className="inline-flex items-center gap-2 text-sm font-medium text-amber-800"><ShieldAlert size={16} />接受风险，不要求修改</button>
        <div className="flex flex-wrap items-center gap-3"><span className="text-xs text-slate-500">{state === "success" ? "已保存" : null}</span>{decision === "modified" || savedForAI ? <button type="button" disabled={!savedForAI || dirty || operationActive || aiStarting} onClick={() => void startAI()} className="rounded-md border border-brand-600 px-4 py-2 text-sm font-medium text-brand-700 disabled:cursor-not-allowed disabled:opacity-40">{operationActive || aiStarting ? "AI修改中…" : "AI修改"}</button> : null}<button type="button" disabled={invalid || state === "saving"} onClick={submit} className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-40">{state === "saving" ? "保存中…" : "保存结论"}</button></div>
      </div>
      {(decision === "modified" || (decision === "accepted" && canSelectMachineRevision(item))) && !savedForAI ? <p className="mt-3 text-xs text-slate-500">请先保存修改结论并完成当前轮必要审核，再启动 AI 修改。</p> : null}
      {revisionOperation ? <p className={`mt-3 text-sm ${revisionOperation.status === "failed" ? "text-red-600" : "text-brand-700"}`}>{revisionOperation.status_label}{revisionOperation.status === "failed" && revisionOperation.error_message ? `：${revisionOperation.error_message}` : ""}</p> : null}
      {riskOpen ? <RiskDialog item={item} existing={existing} onClose={() => setRiskOpen(false)} onSubmit={async (value) => { await onRisk(value); setRiskOpen(false); onDirtyChange(false); }} /> : null}
    </div>
  );
}

function RiskDialog({ item, existing, onClose, onSubmit }: { item: ReviewItemDTO; existing: boolean; onClose: () => void; onSubmit: (value: RiskAcceptanceCommandDTO) => Promise<void> }) {
  const [reason, setReason] = useState(""); const [scope, setScope] = useState("当前审核事项及当前审核版本"); const [confirmed, setConfirmed] = useState(false); const [changeReason, setChangeReason] = useState(""); const [saving, setSaving] = useState(false); const [error, setError] = useState<string | null>(null);
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-6"><div className="w-full max-w-2xl rounded-xl bg-white p-6 shadow-2xl"><div className="flex items-start gap-3"><ShieldAlert className="text-amber-700" /><div><h3 className="text-lg font-semibold">接受风险，不进入修改</h3><p className="mt-1 text-sm text-slate-500">该操作会把机器问题保留为已接受风险，并影响最终审核结果。</p></div></div><div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm"><div className="font-medium text-amber-900">{item.title}</div><div className="mt-2 text-amber-800">严重程度：{item.severity} · {item.release_blocking ? "当前影响放行" : "不直接影响放行"}</div></div><Field label="风险接受理由 *"><textarea className={inputClass} rows={4} value={reason} onChange={(e) => setReason(e.target.value)} /></Field><Field label="适用范围 *"><input className={inputClass} value={scope} onChange={(e) => setScope(e.target.value)} /></Field>{existing ? <Field label="变更原因 *"><input className={inputClass} value={changeReason} onChange={(e) => setChangeReason(e.target.value)} /></Field> : null}<label className="mt-4 flex items-start gap-2 text-sm text-slate-700"><input className="mt-1" type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />我已理解该问题、评分暴露和放行影响，并确认由人工承担此风险。</label>{error ? <p className="mt-3 text-sm text-red-600">{error}</p> : null}<div className="mt-6 flex justify-end gap-3"><button onClick={onClose} className="rounded-md border border-slate-300 px-4 py-2 text-sm">取消</button><button disabled={!reason.trim() || !scope.trim() || !confirmed || (existing && !changeReason.trim()) || saving} onClick={async () => { setSaving(true); setError(null); try { await onSubmit({ reason, scope, confirmed: true, change_reason: existing ? changeReason : null }); } catch (cause) { setError(cause instanceof Error ? cause.message : "风险接受失败"); setSaving(false); } }} className="rounded-md bg-amber-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-40">{saving ? "确认中…" : "确认接受风险"}</button></div></div></div>;
}

const inputClass = "w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm outline-none focus:border-brand-500";
function Field({ label, children }: { label: string; children: React.ReactNode }) { return <label className="mt-4 block"><span className="mb-2 block text-sm font-medium text-slate-700">{label}</span>{children}</label>; }
