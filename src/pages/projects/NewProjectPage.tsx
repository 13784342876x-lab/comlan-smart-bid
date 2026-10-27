import { ArrowLeft, ArrowRight, FolderPlus } from "lucide-react";
import { useState } from "react";
import type { FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { createProject } from "../../api/projects";
import { ApiError } from "../../api/client";
import { Panel } from "../../components/ui/Panel";

export function NewProjectPage() {
  const navigate = useNavigate();
  const [projectName, setProjectName] = useState("");
  const [procuringEntity, setProcuringEntity] = useState("");
  const [procurementNumber, setProcurementNumber] = useState("");
  const [deadline, setDeadline] = useState("");
  const [notes, setNotes] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!projectName.trim() || saving) return;
    setSaving(true);
    setError(null);
    try {
      const result = await createProject({
        project_name: projectName.trim(),
        procuring_entity: procuringEntity.trim() || null,
        procurement_number: procurementNumber.trim() || null,
        deadline: deadline || null,
        notes: notes.trim() || null,
      });
      navigate(`/projects/${encodeURIComponent(result.project.project_id)}/setup`, { replace: true });
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "项目创建失败，请稍后重试。");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mx-auto max-w-3xl p-8">
      <Link to="/projects" className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-800"><ArrowLeft size={15} />返回项目中心</Link>
      <div className="mt-6"><div className="text-sm font-medium text-brand-700">新建投标项目</div><h1 className="mt-1 text-2xl font-semibold text-slate-950">先建立项目，再导入招标资料</h1><p className="mt-2 text-sm leading-6 text-slate-500">项目名称是唯一必填项。采购人、编号和截止时间可由后续招标分析识别，也可以现在补充。</p></div>
      <div className="mt-6 grid grid-cols-4 gap-2 text-xs"><Step active label="1 项目" /><Step label="2 招标资料" /><Step label="3 文件确认" /><Step label="4 开始分析" /></div>
      <Panel className="mt-4 p-6">
        <div className="mb-5 flex items-center gap-3"><div className="rounded-lg bg-brand-50 p-2 text-brand-700"><FolderPlus size={20} /></div><div><h2 className="font-semibold text-slate-900">项目基本信息</h2><p className="mt-1 text-xs text-slate-500">创建项目不会自动启动模型或 Pipeline。</p></div></div>
        <form onSubmit={submit} className="space-y-5">
          <Field label="项目名称" required><input autoFocus value={projectName} onChange={(event) => setProjectName(event.target.value)} maxLength={200} placeholder="例如：天津市第五中心医院超融合服务器采购项目" className={inputClass} /></Field>
          <details className="rounded-lg border border-slate-200 bg-slate-50/60 p-4">
            <summary className="cursor-pointer text-sm font-medium text-slate-700">补充可选信息</summary>
            <div className="mt-4 grid gap-4 sm:grid-cols-2">
              <Field label="采购人"><input value={procuringEntity} onChange={(event) => setProcuringEntity(event.target.value)} className={inputClass} /></Field>
              <Field label="项目编号"><input value={procurementNumber} onChange={(event) => setProcurementNumber(event.target.value)} className={inputClass} /></Field>
              <Field label="投标截止时间"><input type="datetime-local" value={deadline} onChange={(event) => setDeadline(event.target.value)} className={inputClass} /></Field>
              <Field label="备注"><input value={notes} onChange={(event) => setNotes(event.target.value)} className={inputClass} /></Field>
            </div>
          </details>
          {error ? <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div> : null}
          <div className="flex justify-end"><button type="submit" disabled={!projectName.trim() || saving} className="inline-flex items-center gap-2 rounded-md bg-brand-700 px-4 py-2.5 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50">{saving ? "正在创建…" : "创建并上传资料"}<ArrowRight size={15} /></button></div>
        </form>
      </Panel>
    </div>
  );
}

const inputClass = "mt-1.5 h-10 w-full rounded-md border border-slate-300 bg-white px-3 text-sm text-slate-800 outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100";

function Field({ label, required = false, children }: { label: string; required?: boolean; children: React.ReactNode }) {
  return <label className="block text-sm font-medium text-slate-700">{label}{required ? <span className="ml-1 text-red-600">*</span> : null}{children}</label>;
}

function Step({ label, active = false }: { label: string; active?: boolean }) {
  return <div className={`rounded-md border px-3 py-2 text-center font-medium ${active ? "border-brand-300 bg-brand-50 text-brand-700" : "border-slate-200 bg-white text-slate-400"}`}>{label}</div>;
}
