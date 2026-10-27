import { MapPin, Sparkles } from "lucide-react";
import type { ReviewItemDTO } from "../../types/api";
import { ActionabilityIndicator } from "./ActionabilityIndicator";
import { Panel } from "../ui/Panel";

export function ReviewItemCard({ item, focused = false }: { item: ReviewItemDTO; focused?: boolean }) {
  const location = item.response_location;
  const locationLabel = [location?.chapter_title, location?.section_heading].filter(Boolean).join(" / ");
  return (
    <Panel className={`overflow-hidden ${focused ? "ring-2 ring-brand-500 ring-offset-2" : ""}`}>
      <div className="p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <ActionabilityIndicator value={item.actionability} />
              {item.release_blocking ? <span className="text-xs font-medium text-red-600">影响当前版本放行</span> : null}
            </div>
            <h3 className="mt-3 text-base font-semibold leading-7 text-slate-900">{item.title}</h3>
          </div>
        </div>

        <div className="mt-4 grid gap-4 xl:grid-cols-2">
          <ContentBlock title="招标要求" text={item.requirement_text ?? "该事项不直接绑定单一招标条款。"} />
          <ContentBlock title="当前标书响应" text={item.current_response ?? "当前审核正文中未定位到可展示的响应摘录。"} />
        </div>

        <div className="mt-4 rounded-lg border border-amber-100 bg-amber-50/60 p-4">
          <div className="flex items-center gap-2 text-sm font-semibold text-amber-900"><Sparkles size={16} /> AI 发现的问题</div>
          <p className="mt-2 text-sm leading-7 text-amber-950/80">{item.machine_issue}</p>
          {item.machine_rationale ? <p className="mt-2 border-t border-amber-200/70 pt-2 text-sm leading-7 text-amber-900/75">判断理由：{item.machine_rationale}</p> : null}
        </div>

        <div className="mt-4 grid gap-4 lg:grid-cols-[1fr_0.8fr]">
          <ContentBlock title="建议处理方式" text={item.recommendation ?? "等待人工确认后确定后续处理方式。"} />
          <div className="rounded-lg bg-slate-50 p-4 text-sm">
            <div className="flex items-center gap-2 font-medium text-slate-700"><MapPin size={15} /> 当前响应位置</div>
            <p className="mt-2 text-slate-600">{locationLabel || "未提供业务位置"}</p>
            {(item.required_facts.length || item.required_materials.length) ? (
              <div className="mt-3 border-t border-slate-200 pt-3 text-xs leading-6 text-slate-500">
                {item.required_facts.length ? <div>需要确认事实：{item.required_facts.length} 项</div> : null}
                {item.required_materials.length ? <div>需要核验材料：{item.required_materials.length} 项</div> : null}
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </Panel>
  );
}

function ContentBlock({ title, text }: { title: string; text: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="text-xs font-semibold uppercase tracking-wide text-slate-400">{title}</div>
      <p className="mt-2 line-clamp-6 whitespace-pre-wrap text-sm leading-7 text-slate-700">{text}</p>
    </div>
  );
}
