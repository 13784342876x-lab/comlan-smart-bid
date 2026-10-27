import type { LineageInfoDTO } from "../../types/api";

export function LineageInfo({ value }: { value: LineageInfoDTO }) {
  return (
    <details className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-xs text-slate-500">
      <summary className="cursor-pointer font-medium text-slate-700">技术详情 / 高级信息</summary>
      <dl className="mt-3 grid gap-2 font-mono text-[11px] md:grid-cols-2">
        <Item label="审核正文" value={value.audited_content_sha256} />
        <Item label="机器审核" value={value.audit_sha256} />
        <Item label="评分评估" value={value.scoring_sha256} />
        <Item label="审核包" value={value.review_package_id} />
        {value.human_review_id ? <Item label="人工审核" value={value.human_review_id} /> : null}
        {value.finalization_id ? <Item label="审核汇总" value={value.finalization_id} /> : null}
      </dl>
    </details>
  );
}

function Item({ label, value }: { label: string; value: string }) {
  return <div><dt className="font-sans text-slate-400">{label}</dt><dd className="mt-0.5 break-all">{value}</dd></div>;
}
