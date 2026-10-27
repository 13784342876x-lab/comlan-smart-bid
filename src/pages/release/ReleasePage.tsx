import { Download, FileText, PackageCheck } from "lucide-react";
import { useEffect, useState } from "react";

import { getRelease, publishRelease, releaseDownloadUrl } from "../../api/release";
import { useApiQuery } from "../../api/useApiQuery";
import { useProject, useProjectLoad } from "../../app/providers/ProjectProvider";
import { ErrorState, LoadingState } from "../../components/ui/PageState";
import { Panel } from "../../components/ui/Panel";
import type { ReleaseStatusDTO } from "../../types/api";
import { releaseView } from "./releaseViewModel";

export function ReleasePage() {
  const projectLoad = useProjectLoad();
  const project = useProject();
  const query = useApiQuery(
    (signal) => getRelease(project.projectId, signal),
    [project.projectId, project.currentRevision.version_id],
  );
  const [status, setStatus] = useState<ReleaseStatusDTO | null>(null);
  const [publishing, setPublishing] = useState(false);
  const [publishError, setPublishError] = useState<string | null>(null);

  useEffect(() => setStatus(query.data), [query.data]);
  if (query.loading && !status) return <LoadingState label="正在检查技术标正文是否可生成…" />;
  if (query.error && !status) return <ErrorState error={query.error} onRetry={query.reload} />;
  if (!status) return null;
  const view = releaseView(status, publishing);

  async function publish() {
    setPublishing(true);
    setPublishError(null);
    try {
      const response = await publishRelease(project.projectId);
      setStatus(response.data);
      projectLoad.reload();
    } catch (error) {
      setPublishError(error instanceof Error ? error.message : "技术标正文生成失败");
    } finally {
      setPublishing(false);
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-xl font-semibold text-slate-900">技术标正文</h2>
        <p className="mt-1 text-sm text-slate-500">当前可导出技术标正文。招标文件要求的专项表格或独立上传材料请根据下方清单在提交前完成。</p>
      </div>
      <Panel className="p-6">
        <div className="flex items-start gap-4">
          <div className="rounded-lg bg-brand-50 p-3 text-brand-700"><PackageCheck size={24} /></div>
          <div className="min-w-0 flex-1">
            <h3 className="font-semibold text-slate-900">技术标正文</h3>
            <p className="mt-1 text-sm text-slate-600">状态：{view.statusLabel}</p>
            {view.blockers.length ? <ul className="mt-3 space-y-2 text-sm text-amber-800">{view.blockers.map((message) => <li key={message}>• {message}</li>)}</ul> : <p className="mt-2 text-sm text-slate-500">系统将基于当前审核版本生成技术标正文 Word 和 PDF。</p>}
            {publishError ? <div className="mt-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{publishError}</div> : null}
            <div className="mt-5 flex flex-wrap gap-3">
              {!view.showDownloads ? <button type="button" disabled={!view.canPublish} onClick={() => void publish()} className="rounded-md bg-brand-700 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-40">{view.actionLabel}</button> : <><a href={releaseDownloadUrl(project.projectId, "docx")} className="inline-flex items-center gap-2 rounded-md bg-brand-700 px-4 py-2 text-sm font-medium text-white"><FileText size={15} />下载技术标正文 Word</a><a href={releaseDownloadUrl(project.projectId, "pdf")} className="inline-flex items-center gap-2 rounded-md border border-brand-300 px-4 py-2 text-sm font-medium text-brand-700"><Download size={15} />下载技术标正文 PDF</a></>}
            </div>
          </div>
        </div>
      </Panel>
      {status.required_submission_items.length ? (
        <Panel className="p-6">
          <h3 className="font-semibold text-slate-900">提交前待处理事项</h3>
          <div className="mt-4 divide-y divide-slate-100">
            {status.required_submission_items.map((item) => (
              <div key={item.item_key} className="py-4 first:pt-0 last:pb-0">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h4 className="font-medium text-slate-900">{item.display_title}</h4>
                  <span className="rounded-full bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-800">
                    {item.status === "prepared" ? "已准备" : item.submission_kind === "external_upload" ? "需在投标平台另行处理" : "待处理"}
                  </span>
                </div>
                <p className="mt-2 text-sm text-slate-600">{item.description}</p>
                {item.current_record ? <p className="mt-2 text-sm text-slate-500">当前记录：{item.current_record}</p> : null}
              </div>
            ))}
          </div>
        </Panel>
      ) : null}
    </div>
  );
}
