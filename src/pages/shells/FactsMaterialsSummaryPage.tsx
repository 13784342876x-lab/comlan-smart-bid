import { ArrowUpRight, CheckCircle2, FileQuestion, FolderSearch, PenLine } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { getProjectFactsMaterials } from "../../api/projects";
import { useApiQuery } from "../../api/useApiQuery";
import { useProject } from "../../app/providers/ProjectProvider";
import { FactStatus } from "../../components/domain/FactStatus";
import { MaterialStatus } from "../../components/domain/MaterialStatus";
import { EmptyState, ErrorState, LoadingState } from "../../components/ui/PageState";
import { Panel } from "../../components/ui/Panel";
import type { FactMaterialItemDTO } from "../../types/api";
import {
  factsMaterialsGroup,
  isFactsMaterialsComplete,
  safeBusinessDescription,
  safeBusinessTitle,
  type FactsMaterialsGroup,
} from "./factsMaterialsViewModel";

const groupMeta: Record<FactsMaterialsGroup, { title: string; description: string }> = {
  confirmation: { title: "待确认事实", description: "需要由项目人员作出明确确认的业务事实或选择。" },
  project_fact: { title: "待填写项目信息", description: "标书写作需要使用、但当前尚未完整填写的项目信息。" },
  material: { title: "待补充材料", description: "需要查找、核验或补充的证明材料。" },
};

export function FactsMaterialsSummaryPage() {
  const { projectId } = useProject();
  const query = useApiQuery((signal) => getProjectFactsMaterials(projectId, signal), [projectId]);
  const [searchParams] = useSearchParams();
  const focusedItem = searchParams.get("item") ?? searchParams.get("fact") ?? searchParams.get("material");

  if (query.loading) return <LoadingState label="正在读取事实与材料状态…" />;
  if (query.error) return <ErrorState error={query.error} onRetry={query.reload} />;
  if (!query.data) return <EmptyState title="暂无事实与材料数据" description="当前项目没有可用的写作准备或审核核验事项。" />;

  const { headline } = query.data;
  const items = [...query.data.items].sort((left, right) => {
    const focusOrder = Number(right.trace_id === focusedItem) - Number(left.trace_id === focusedItem);
    if (focusOrder) return focusOrder;
    return Number(isFactsMaterialsComplete(left.status)) - Number(isFactsMaterialsComplete(right.status));
  });

  return (
    <div className="space-y-5">
      <header>
        <div className="text-xs font-medium text-brand-700">写作准备与审核核验</div>
        <h2 className="mt-1 text-xl font-semibold text-slate-950">事实与材料确认</h2>
        <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-500">优先处理尚未完成的事项。每项都会说明需要补充什么、为什么需要，以及可前往哪个现有入口处理。</p>
      </header>

      <div className="grid gap-4 md:grid-cols-3">
        <Metric label="待确认事实" value={headline.confirmations_required} />
        <Metric label="待填写项目信息" value={headline.missing_project_facts} />
        <Metric label="待补充材料" value={headline.queries_not_executed} />
      </div>

      {(["confirmation", "project_fact", "material"] as FactsMaterialsGroup[]).map((group) => {
        const values = items.filter((item) => factsMaterialsGroup(item) === group);
        if (!values.length) return null;
        const meta = groupMeta[group];
        return (
          <Panel key={group}>
            <div className="border-b border-slate-100 px-5 py-4">
              <div className="font-semibold text-slate-900">{meta.title}</div>
              <p className="mt-1 text-xs text-slate-500">{meta.description}</p>
            </div>
            <div className="divide-y divide-slate-100">
              {values.map((item) => <SemanticItem key={`${item.category}-${item.trace_id}`} item={item} focused={item.trace_id === focusedItem} />)}
            </div>
          </Panel>
        );
      })}

      {!items.length ? <Panel><div className="px-5 py-10 text-center text-sm text-slate-500">当前没有需要确认的事实或补充的材料。</div></Panel> : null}
    </div>
  );
}

function SemanticItem({ item, focused }: { item: FactMaterialItemDTO; focused: boolean }) {
  const complete = isFactsMaterialsComplete(item.status);
  const group = factsMaterialsGroup(item);
  const Icon = group === "confirmation" ? FileQuestion : group === "project_fact" ? PenLine : FolderSearch;
  const description = safeBusinessDescription(item);
  return (
    <article className={`px-5 py-5 ${focused ? "bg-brand-50 ring-1 ring-inset ring-brand-200" : complete ? "bg-slate-50/60" : ""}`}>
      <div className="flex flex-wrap items-start gap-4">
        <div className={`mt-0.5 rounded-lg p-2 ${complete ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"}`}>
          {complete ? <CheckCircle2 size={18} /> : <Icon size={18} />}
        </div>
        <div className={`min-w-0 flex-1 ${complete ? "opacity-75" : ""}`}>
          <h3 className="font-medium text-slate-900">{safeBusinessTitle(item)}</h3>
          {description ? <p className="mt-1 text-sm leading-6 text-slate-600">{description}</p> : null}
          {item.current_value ? <InfoLine label="当前值" values={[item.current_value]} /> : group === "project_fact" && !complete ? <InfoLine label="当前值" values={["未填写"]} /> : null}
          {item.related_locations.length ? <InfoLine label="用于" values={item.related_locations} /> : null}
          {item.requirement_context.length ? <InfoLine label="招标要求" values={item.requirement_context.slice(0, 2)} /> : null}
          {item.source_label ? <InfoLine label="来源" values={[item.source_label]} /> : null}
        </div>
        <div className="w-32 shrink-0">
          {group === "material" ? <MaterialStatus status={item.status} label={item.status_label} /> : <FactStatus status={item.status} />}
        </div>
        <div className="w-40 shrink-0 text-right">
          <Link to={item.action_url} className="inline-flex items-center gap-1 rounded-md border border-brand-200 bg-white px-3 py-2 text-sm font-medium text-brand-700 hover:bg-brand-50">
            {complete ? "查看详情" : item.action_label}<ArrowUpRight size={13} />
          </Link>
        </div>
      </div>
    </article>
  );
}

function InfoLine({ label, values }: { label: string; values: string[] }) {
  return <div className="mt-2 text-xs leading-5 text-slate-500"><span className="font-medium text-slate-600">{label}：</span>{values.join("；")}</div>;
}

function Metric({ label, value }: { label: string; value: number }) {
  return <Panel className="p-4"><div className="text-xs text-slate-500">{label}</div><div className="mt-2 text-xl font-semibold text-slate-900">{value} 项</div></Panel>;
}
