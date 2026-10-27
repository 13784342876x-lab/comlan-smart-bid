import { Construction } from "lucide-react";
import { useLocation, useSearchParams } from "react-router-dom";
import { Panel } from "../../components/ui/Panel";

export function SectionShellPage({ title, description, boundary, focusParameter }: { title: string; description: string; boundary?: string; focusParameter?: string }) {
  const location = useLocation();
  const [searchParams] = useSearchParams();
  if (location.pathname.endsWith("/writing-plan")) {
    title = "写作规划";
    description = "目录、章节与分节写作安排将在后续接入。";
    boundary = "当前只保留页面入口与关联跳转，不会在本轮生成标书结构或写作内容。";
  }
  const focus = focusParameter ? searchParams.get(focusParameter) : null;
  return (
    <div className="space-y-5">
      <div><h2 className="text-xl font-semibold">{title}</h2><p className="mt-1 text-sm text-slate-500">{description}</p></div>
      <Panel className="p-8">
        <Construction className="text-brand-500" size={26} />
        <h3 className="mt-4 font-semibold">Not implemented yet</h3>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500">正式路由与项目/版本上下文已经建立。本轮不使用旧 mock 页面承担该业务。</p>
        {focus ? <div className="mt-4 rounded-lg border border-brand-100 bg-brand-50 px-4 py-3 text-sm text-brand-700">已接收正文定位信息，正式页面将打开对应内容。</div> : null}
        {boundary ? <div className="mt-4 rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-800">{boundary}</div> : null}
      </Panel>
    </div>
  );
}
