import { SectionShellPage } from "./SectionShellPage";

export function MyWorkPage() {
  return <div className="p-8"><SectionShellPage title="我的工作" description="跨项目待办与审核工作聚合入口。" /></div>;
}

export function EnterpriseKnowledgePage() {
  return <div className="p-8"><SectionShellPage title="企业知识库" description="企业事实、资质、案例与材料的全局管理入口。" /></div>;
}
