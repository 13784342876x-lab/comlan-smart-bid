import {
  AlertTriangle,
  ArrowRight,
  Bot,
  CheckCircle2,
  CircleCheck,
  ClipboardCopy,
  Database,
  Download,
  Edit3,
  ExternalLink,
  Eye,
  FileSearch,
  FileText,
  Filter,
  LayoutDashboard,
  MapPin,
  MessageSquareText,
  Minimize2,
  Paperclip,
  PenLine,
  Play,
  RefreshCw,
  Save,
  Search,
  Send,
  ShieldCheck,
  Sparkles,
  Table2,
  Upload,
  Wand2,
  X
} from "lucide-react";
import { useMemo, useRef, useState } from "react";

type PageKey = "overview" | "parse" | "matrix" | "generate" | "review" | "knowledge" | "export";
type RiskLevel = "高风险" | "中风险" | "低风险" | "格式提示";
type MatrixStatus = "已响应" | "待补充" | "高风险" | "人工确认";
type ChapterStatus = "未生成" | "已生成" | "待优化" | "已确认";
type AssistantSource = "general" | "generate" | "review" | "matrix";

type MatrixRow = {
  id: string;
  source: string;
  type: "资格项" | "废标项" | "商务项" | "技术项" | "评分项" | "报价项";
  score: string;
  required: boolean;
  chapter: string;
  knowledge: string;
  position: string;
  status: MatrixStatus;
  risk: RiskLevel;
  analysis: string;
  strategy: string;
};

type Chapter = {
  name: string;
  status: ChapterStatus;
  risks: number;
  scores: number;
  requirements: number;
  references: number;
};

type RiskItem = {
  id: string;
  level: RiskLevel;
  type: string;
  title: string;
  clause: string;
  impact: string;
  suggestion: string;
  position: string;
  chapter: string;
  handled: boolean;
};

type FileInfo = {
  name: string;
  size: number;
  type: string;
};

type KnowledgeStatus = "有效" | "即将过期" | "已过期" | "未设置有效期";
type KnowledgeCategory = "企业资质库" | "业绩案例库" | "产品资料库" | "历史标书库" | "常用素材库";
type AssetType = "证书整页" | "合同关键页" | "产品图片" | "检测报告页" | "盖章页" | "流程图" | "其他";
type InsertMode = "page_width" | "half_width" | "original_ratio" | "table_cell";

type KnowledgeAsset = {
  id: string;
  fileId: string;
  name: string;
  assetType: AssetType;
  sourcePage: string;
  previewUrl?: string;
  ocrText: string;
  recommendedUsage: string;
  insertable: boolean;
  insertMode: InsertMode;
};

type KnowledgeFile = {
  id: string;
  name: string;
  type: string;
  size: number;
  category: KnowledgeCategory;
  tags: string[];
  validFrom?: string;
  validTo?: string;
  status: KnowledgeStatus;
  uploadedAt: string;
  sourceFile: {
    name: string;
    type: string;
    size: number;
    objectUrl?: string;
  };
  parsedText: {
    summary: string;
    keywords: string[];
    ocrText: string;
    chapters: string[];
    scoreItems: string[];
  };
  assets: KnowledgeAsset[];
  applicableIndustry: string;
  applicableProduct: string;
  recentProject: string;
  remark: string;
};

type AssistantContext = {
  source: AssistantSource;
  chapter?: string;
  risk?: string;
  requirement?: string;
};

type AssistantMessage = {
  role: "ai" | "user";
  text: string;
};

const navItems: Array<{ key: PageKey; label: string; icon: typeof LayoutDashboard }> = [
  { key: "overview", label: "项目概览", icon: LayoutDashboard },
  { key: "parse", label: "招标解析", icon: FileSearch },
  { key: "matrix", label: "响应矩阵", icon: Table2 },
  { key: "generate", label: "标书生成", icon: PenLine },
  { key: "review", label: "审查中心", icon: ShieldCheck },
  { key: "knowledge", label: "知识库", icon: Database },
  { key: "export", label: "导出定稿", icon: Download }
];

const project = {
  name: "XX市卫健委医疗设备采购项目",
  owner: "XX市卫生健康委员会",
  code: "XXWJW-2026-CG-042",
  deadline: "2026-07-08 17:30",
  stage: "标书编制与审查",
  parse: 96,
  matrix: 80,
  business: 72,
  technical: 68,
  review: 74
};

const stats = {
  high: 3,
  medium: 7,
  low: 11,
  passed: 126,
  scoreTotal: 35,
  responded: 28,
  pending: 7
};

const chaptersSeed: Chapter[] = [
  { name: "投标函", status: "已生成", risks: 1, scores: 1, requirements: 4, references: 2 },
  { name: "资格证明文件", status: "待优化", risks: 3, scores: 3, requirements: 9, references: 5 },
  { name: "商务响应表", status: "已生成", risks: 2, scores: 4, requirements: 12, references: 4 },
  { name: "技术偏离表", status: "待优化", risks: 4, scores: 7, requirements: 18, references: 6 },
  { name: "技术方案", status: "已生成", risks: 2, scores: 9, requirements: 21, references: 7 },
  { name: "实施方案", status: "未生成", risks: 1, scores: 4, requirements: 8, references: 3 },
  { name: "售后服务方案", status: "已生成", risks: 1, scores: 5, requirements: 10, references: 5 },
  { name: "培训方案", status: "未生成", risks: 0, scores: 2, requirements: 5, references: 2 },
  { name: "报价文件", status: "待优化", risks: 2, scores: 0, requirements: 7, references: 1 }
];

const matrixRows: MatrixRow[] = [
  {
    id: "R-001",
    source: "投标人须具备有效营业执照，经营范围应覆盖医疗设备销售及售后服务。",
    type: "资格项",
    score: "-",
    required: true,
    chapter: "资格证明文件",
    knowledge: "营业执照、售后服务制度",
    position: "资格证明文件 / 1.1",
    status: "高风险",
    risk: "高风险",
    analysis: "营业执照已引用，但经营范围截图未在正文中显式说明。",
    strategy: "在资格证明章节加入经营范围摘录，并绑定营业执照扫描件。"
  },
  {
    id: "R-002",
    source: "技术方案需说明设备安装、调试、验收、培训和质保期服务安排。",
    type: "评分项",
    score: "8",
    required: true,
    chapter: "技术方案",
    knowledge: "产品技术手册V3.2、售后服务制度",
    position: "技术方案 / 3.2",
    status: "已响应",
    risk: "低风险",
    analysis: "主体内容完整，培训响应可进一步量化。",
    strategy: "补充培训课时、人员角色、考核方式和回访承诺。"
  },
  {
    id: "R-003",
    source: "投标文件不得出现报价前后不一致，否则按无效投标处理。",
    type: "废标项",
    score: "-",
    required: true,
    chapter: "报价文件",
    knowledge: "报价核对清单",
    position: "报价文件 / 汇总表",
    status: "人工确认",
    risk: "中风险",
    analysis: "报价清单和开标一览表需要人工最终核对。",
    strategy: "导出前执行报价一致性审查，并锁定报价版本。"
  },
  {
    id: "R-004",
    source: "同类医疗设备供货业绩不少于三个，每个案例须提供合同或中标通知书。",
    type: "商务项",
    score: "6",
    required: false,
    chapter: "商务响应表",
    knowledge: "医疗设备项目合同案例、XX医院中标通知书",
    position: "商务响应表 / 2.4",
    status: "待补充",
    risk: "中风险",
    analysis: "已引用两个案例，第三个案例资料缺失。",
    strategy: "从业绩案例库补充最近三年同类项目合同扫描件。"
  },
  {
    id: "R-005",
    source: "产品参数响应表须逐项响应，不允许仅写满足或无偏离。",
    type: "技术项",
    score: "10",
    required: true,
    chapter: "技术偏离表",
    knowledge: "产品技术手册V3.2",
    position: "技术偏离表 / 参数表",
    status: "待补充",
    risk: "高风险",
    analysis: "部分关键参数缺少具体数值响应。",
    strategy: "从产品手册提取型号、指标、检测条件和证明页码。"
  }
];

const knowledgeDocs = [
  "营业执照",
  "ISO9001质量管理体系认证",
  "医疗设备项目合同案例",
  "XX医院中标通知书",
  "产品技术手册V3.2",
  "售后服务制度",
  "培训方案历史模板"
];

const knowledgeLibraries = ["企业资质库", "业绩案例库", "产品资料库", "历史标书库", "常用素材库"];

const parseSteps = ["正在读取文件", "正在提取标书目录", "正在识别评分项", "正在匹配知识库", "正在生成章节任务"];

const knowledgeSeed: KnowledgeFile[] = [
  {
    id: "kf-001",
    name: "营业执照.pdf",
    type: "PDF",
    size: 1840 * 1024,
    category: "企业资质库",
    tags: ["营业执照", "基础资质"],
    validFrom: "2025-01-01",
    validTo: "2028-03-12",
    status: "有效",
    uploadedAt: "2026-06-10 09:20",
    sourceFile: { name: "营业执照.pdf", type: "PDF", size: 1840 * 1024 },
    parsedText: {
      summary: "企业基础资质文件，包含统一社会信用代码、经营范围与登记机关信息。",
      keywords: ["营业执照", "经营范围", "医疗设备销售"],
      ocrText: "经营范围包含医疗设备销售、安装调试、售后服务等相关内容。",
      chapters: ["资格证明文件", "商务响应表"],
      scoreItems: ["企业资质完整性", "资格条件响应"]
    },
    assets: [
      {
        id: "ka-001",
        fileId: "kf-001",
        name: "第1页整页截图",
        assetType: "证书整页",
        sourcePage: "第1页",
        ocrText: "营业执照整页扫描件",
        recommendedUsage: "资格证明文件附件页",
        insertable: true,
        insertMode: "page_width"
      },
      {
        id: "ka-002",
        fileId: "kf-001",
        name: "经营范围关键区域",
        assetType: "证书整页",
        sourcePage: "第1页",
        ocrText: "经营范围覆盖医疗设备销售及售后服务",
        recommendedUsage: "资格响应说明旁证",
        insertable: true,
        insertMode: "half_width"
      }
    ],
    applicableIndustry: "医疗,政府采购",
    applicableProduct: "全线产品",
    recentProject: project.name,
    remark: "资格证明章节优先引用。"
  },
  {
    id: "kf-002",
    name: "ISO9001质量管理体系认证.pdf",
    type: "PDF",
    size: 1260 * 1024,
    category: "企业资质库",
    tags: ["质量体系", "认证"],
    validFrom: "2023-08-22",
    validTo: "2026-08-21",
    status: "即将过期",
    uploadedAt: "2026-06-11 14:05",
    sourceFile: { name: "ISO9001质量管理体系认证.pdf", type: "PDF", size: 1260 * 1024 },
    parsedText: {
      summary: "质量管理体系认证证书，可用于企业质量保障能力说明。",
      keywords: ["ISO9001", "质量管理", "认证有效期"],
      ocrText: "证书有效期至 2026-08-21，覆盖质量管理体系认证范围。",
      chapters: ["资格证明文件", "技术方案"],
      scoreItems: ["质量保障体系", "企业管理能力"]
    },
    assets: [
      {
        id: "ka-003",
        fileId: "kf-002",
        name: "证书关键页",
        assetType: "证书整页",
        sourcePage: "第1页",
        ocrText: "ISO9001 认证证书关键页",
        recommendedUsage: "资格证明文件",
        insertable: true,
        insertMode: "page_width"
      },
      {
        id: "ka-004",
        fileId: "kf-002",
        name: "盖章页",
        assetType: "盖章页",
        sourcePage: "第2页",
        ocrText: "认证机构盖章页",
        recommendedUsage: "附件真实性证明",
        insertable: false,
        insertMode: "original_ratio"
      }
    ],
    applicableIndustry: "医疗",
    applicableProduct: "医疗设备",
    recentProject: project.name,
    remark: "有效期接近截止，导出前需复核。"
  },
  {
    id: "kf-003",
    name: "产品技术手册V3.2.docx",
    type: "DOCX",
    size: 3560 * 1024,
    category: "产品资料库",
    tags: ["技术参数", "产品手册"],
    status: "未设置有效期",
    uploadedAt: "2026-06-12 16:30",
    sourceFile: { name: "产品技术手册V3.2.docx", type: "DOCX", size: 3560 * 1024 },
    parsedText: {
      summary: "产品技术参数、安装条件、维护说明和主要功能介绍。",
      keywords: ["技术参数", "安装调试", "维护"],
      ocrText: "设备支持多模式运行，关键参数满足招标文件技术指标要求。",
      chapters: ["技术偏离表", "技术方案", "实施方案"],
      scoreItems: ["技术参数响应", "实施可行性"]
    },
    assets: [
      {
        id: "ka-005",
        fileId: "kf-003",
        name: "产品结构图",
        assetType: "产品图片",
        sourcePage: "第6页",
        ocrText: "产品结构示意图",
        recommendedUsage: "技术方案产品介绍",
        insertable: true,
        insertMode: "half_width"
      },
      {
        id: "ka-006",
        fileId: "kf-003",
        name: "参数表截图",
        assetType: "检测报告页",
        sourcePage: "第12页",
        ocrText: "关键技术参数表",
        recommendedUsage: "技术偏离表参数证明",
        insertable: true,
        insertMode: "table_cell"
      }
    ],
    applicableIndustry: "医疗",
    applicableProduct: "核心产品",
    recentProject: project.name,
    remark: "技术标章节常用资料。"
  }
];

const risksSeed: RiskItem[] = [
  {
    id: "K-001",
    level: "高风险",
    type: "废标条款风险",
    title: "技术参数表存在仅写“满足”的响应项",
    clause: "招标文件 第三章 3.1.2",
    impact: "可能被认定为未实质性响应，存在废标风险。",
    suggestion: "补充对应型号参数、检测条件和证明材料页码。",
    position: "技术偏离表 / 第12行",
    chapter: "技术偏离表",
    handled: false
  },
  {
    id: "K-002",
    level: "高风险",
    type: "资格文件问题",
    title: "营业执照经营范围未在正文中显式说明",
    clause: "招标文件 第二章 2.2.1",
    impact: "资格审查人员需要快速确认适配性。",
    suggestion: "在资格证明文件中增加经营范围摘录和附件索引。",
    position: "资格证明文件 / 1.1",
    chapter: "资格证明文件",
    handled: false
  },
  {
    id: "K-003",
    level: "中风险",
    type: "评分项未覆盖",
    title: "培训方案缺少考核方式",
    clause: "评分办法 4.3 培训服务",
    impact: "可能影响培训服务评分项得分。",
    suggestion: "补充培训签到、现场考核、满意度回访机制。",
    position: "培训方案 / 2.3",
    chapter: "培训方案",
    handled: false
  },
  {
    id: "K-004",
    level: "低风险",
    type: "前后内容不一致",
    title: "质保期表述存在 24 个月和 36 个月两种写法",
    clause: "合同条款 8.2",
    impact: "评审时可能产生理解偏差。",
    suggestion: "统一为 36 个月，并在售后服务方案中同步。",
    position: "售后服务方案 / 1.2",
    chapter: "售后服务方案",
    handled: false
  }
];

const parseTabs = ["项目基本信息", "资格要求", "废标条款", "评分办法", "标书目录/模板"];
const riskCategories = [
  "资格文件问题",
  "废标条款风险",
  "评分项未覆盖",
  "资料缺失",
  "资质过期",
  "商务响应不完整",
  "技术响应不完整",
  "前后内容不一致",
  "报价一致性问题",
  "签字盖章问题"
];

function App() {
  const [page, setPage] = useState<PageKey>("overview");
  const [parseTab, setParseTab] = useState(parseTabs[0]);
  const [generationReady, setGenerationReady] = useState(false);
  const [selectedChapter, setSelectedChapter] = useState(chaptersSeed[4].name);
  const [chapters, setChapters] = useState(chaptersSeed);
  const [chapterDrafts, setChapterDrafts] = useState<Record<string, string>>({});
  const [adoptedDocs, setAdoptedDocs] = useState<Record<string, boolean>>({
    营业执照: true,
    ISO9001质量管理体系认证: true,
    "产品技术手册V3.2": true
  });
  const [knowledgeFiles, setKnowledgeFiles] = useState<KnowledgeFile[]>(knowledgeSeed);
  const [matrixFilter, setMatrixFilter] = useState({ type: "全部", risk: "全部", status: "全部" });
  const [drawerRow, setDrawerRow] = useState<MatrixRow | null>(null);
  const [risks, setRisks] = useState(risksSeed);
  const [exportModal, setExportModal] = useState(false);
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [assistantMinimized, setAssistantMinimized] = useState(true);
  const [assistantContext, setAssistantContext] = useState<AssistantContext>({ source: "general" });
  const [assistantMessages, setAssistantMessages] = useState<AssistantMessage[]>([
    {
      role: "ai",
      text: "我可以帮你生成章节、优化表达、补充案例、检查漏项和处理审查风险。"
    }
  ]);

  const activeChapter = chapters.find((item) => item.name === selectedChapter) ?? chapters[0];
  const unhandledHigh = risks.filter((risk) => risk.level === "高风险" && !risk.handled).length;

  const filteredRows = useMemo(() => {
    return matrixRows.filter((row) => {
      const typeOk = matrixFilter.type === "全部" || row.type === matrixFilter.type;
      const riskOk = matrixFilter.risk === "全部" || row.risk === matrixFilter.risk;
      const statusOk = matrixFilter.status === "全部" || row.status === matrixFilter.status;
      return typeOk && riskOk && statusOk;
    });
  }, [matrixFilter]);

  const setAppPage = (nextPage: PageKey) => {
    setPage(nextPage);
    if (nextPage !== "generate" && nextPage !== "review" && nextPage !== "matrix") {
      setAssistantContext({ source: "general" });
    }
  };

  const updateChapterStatus = (name: string, status: ChapterStatus) => {
    setChapters((items) => items.map((item) => (item.name === name ? { ...item, status } : item)));
  };

  const setDraft = (text: string) => {
    setChapterDrafts((items) => ({ ...items, [selectedChapter]: text }));
  };

  const appendToCurrentChapter = (text: string) => {
    setChapterDrafts((items) => ({
      ...items,
      [selectedChapter]: `${items[selectedChapter] || ""}\n\n${text}`.trim()
    }));
    updateChapterStatus(selectedChapter, "待优化");
  };

  const generateOutline = () => {
    setDraft(
      `一、${selectedChapter}编制目标\n1. 对招标条款进行逐条响应，确保无遗漏。\n2. 结合本地知识库资料形成可审查、可追溯内容。\n\n二、关键响应点\n1. 绑定评分项：${activeChapter.scores} 项。\n2. 绑定招标要求：${activeChapter.requirements} 项。\n3. 引用资料：${activeChapter.references} 份。\n\n三、待补充与风险控制\n1. 对高风险条款进行显式响应。\n2. 补充附件页码、证明材料和历史案例。`
    );
    updateChapterStatus(selectedChapter, "已生成");
  };

  const generateBody = () => {
    setDraft(
      `${selectedChapter}\n\n我方充分理解本项目医疗设备采购对合规性、交付能力、技术响应和持续服务能力的要求。本章围绕招标文件对应条款逐项响应，并调用企业资质、历史业绩、产品手册及服务制度等本地知识库资料，形成可验证、可追溯的投标内容。\n\n针对本章绑定的 ${activeChapter.requirements} 项招标要求，我方逐条给出响应说明；针对 ${activeChapter.scores} 个评分项，重点补充实施路径、人员安排、时间节点、质量保障和证明材料索引。所有引用资料均标注来源，便于后续审查中心定位核验。\n\n风险控制方面，本章将对资格有效期、技术参数、响应偏离、签字盖章和附件一致性进行复核。对于仍需人工确认的内容，系统已在右侧任务卡中列出，后续可通过章节审查继续闭环处理。`
    );
    updateChapterStatus(selectedChapter, "已生成");
  };

  const openAssistant = (context: AssistantContext, seed?: string) => {
    setAssistantContext(context);
    setAssistantOpen(true);
    setAssistantMinimized(false);
    if (seed) {
      setAssistantMessages((items) => [...items, { role: "ai", text: seed }]);
    }
  };

  const jumpToChapter = (chapter: string, riskTitle?: string) => {
    setSelectedChapter(chapter);
    setGenerationReady(true);
    setPage("generate");
    if (riskTitle) {
      openAssistant(
        { source: "review", chapter, risk: riskTitle },
        `已带入审查风险：“${riskTitle}”。我可以解释风险原因、定位相关章节，并生成可插入正文的修复文本。`
      );
    }
  };

  const markRiskHandled = (id: string) => {
    setRisks((items) => items.map((item) => (item.id === id ? { ...item, handled: true } : item)));
  };

  const handleAssistantQuickAction = (label: string) => {
    if (page === "generate" && generationReady && label === "生成本章大纲") {
      generateOutline();
    }
    if (page === "generate" && generationReady && label === "生成本章正文") {
      generateBody();
    }
    const contextText = assistantContext.risk
      ? `围绕风险“${assistantContext.risk}”，建议补充条款解释、响应承诺、证明材料页码和一致性复核说明。`
      : assistantContext.requirement
        ? `围绕要求“${assistantContext.requirement}”，建议在对应章节中补充明确响应、资料来源和评审可核验表述。`
        : `针对“${selectedChapter}”，建议强化评分项响应，补充知识库案例，并将语言调整为正式投标表达。`;
    setAssistantMessages((items) => [
      ...items,
      { role: "user", text: label },
      { role: "ai", text: `${label}：${contextText}` }
    ]);
  };

  return (
    <div className="flex min-h-screen bg-slate-100 text-slate-900">
      <Sidebar current={page} onChange={setAppPage} />
      <main className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        <div className="min-h-0 flex-1 overflow-auto p-6">
          {page === "overview" && <OverviewPage onNavigate={setAppPage} />}
          {page === "parse" && (
            <ParsePage parseTab={parseTab} setParseTab={setParseTab} onMatrix={() => setAppPage("matrix")} />
          )}
          {page === "matrix" && (
            <MatrixPage rows={filteredRows} filter={matrixFilter} setFilter={setMatrixFilter} onOpen={setDrawerRow} />
          )}
          {page === "generate" &&
            (generationReady ? (
              <GeneratePage
                chapters={chapters}
                activeChapter={activeChapter}
                selectedChapter={selectedChapter}
                setSelectedChapter={(chapter) => {
                  setSelectedChapter(chapter);
                  setAssistantContext((context) =>
                    context.source === "generate" || context.source === "general"
                      ? { source: "generate", chapter }
                      : { ...context, chapter }
                  );
                }}
                draft={chapterDrafts[selectedChapter] || ""}
                generateOutline={generateOutline}
                generateBody={generateBody}
                save={() => updateChapterStatus(selectedChapter, "已生成")}
                confirm={() => updateChapterStatus(selectedChapter, "已确认")}
                review={() => setAppPage("review")}
                adoptedDocs={adoptedDocs}
                setAdoptedDocs={setAdoptedDocs}
                knowledgeFiles={knowledgeFiles}
              />
            ) : (
              <TenderCreatePage onComplete={() => setGenerationReady(true)} />
            ))}
          {page === "review" && (
            <ReviewPage
              risks={risks}
              onJump={jumpToChapter}
              onHandled={markRiskHandled}
              onAiFix={(risk) =>
                openAssistant(
                  { source: "review", chapter: risk.chapter, risk: risk.title },
                  `已带入风险问题：“${risk.title}”。关联章节：${risk.chapter}。建议优先处理：${risk.suggestion}`
                )
              }
            />
          )}
          {page === "knowledge" && <KnowledgePage files={knowledgeFiles} setFiles={setKnowledgeFiles} />}
          {page === "export" && <ExportPage unhandledHigh={unhandledHigh} onExport={() => setExportModal(true)} />}
        </div>
      </main>
      {drawerRow && (
        <MatrixDrawer
          row={drawerRow}
          onClose={() => setDrawerRow(null)}
          onJump={jumpToChapter}
          onAiHandle={(row) =>
            openAssistant(
              { source: "matrix", chapter: row.chapter, requirement: row.source },
              `已带入响应要求：${row.source}\n推荐章节：${row.chapter}\n推荐策略：${row.strategy}`
            )
          }
        />
      )}
      {exportModal && (
        <ConfirmModal highCount={unhandledHigh} onClose={() => setExportModal(false)} onConfirm={() => setExportModal(false)} />
      )}
      <FloatingAIAssistant
        open={assistantOpen}
        minimized={assistantMinimized}
        currentPage={page}
        generationReady={generationReady}
        currentChapter={selectedChapter}
        context={assistantContext}
        messages={assistantMessages}
        setMessages={setAssistantMessages}
        onToggle={() => {
          setAssistantOpen(true);
          setAssistantMinimized((value) => !value);
          if (!assistantOpen && page === "generate" && generationReady) {
            setAssistantContext({ source: "generate", chapter: selectedChapter });
          }
        }}
        onClose={() => {
          setAssistantOpen(false);
          setAssistantMinimized(true);
        }}
        onQuickAction={handleAssistantQuickAction}
        onInsertToChapter={appendToCurrentChapter}
      />
    </div>
  );
}

function Sidebar({ current, onChange }: { current: PageKey; onChange: (page: PageKey) => void }) {
  return (
    <aside className="flex w-64 shrink-0 flex-col bg-[#191d3a] text-white">
      <div className="border-b border-white/10 px-5 py-5">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-brand-500 shadow-lg shadow-brand-900/30">
            <Sparkles size={20} />
          </div>
          <div>
            <div className="text-lg font-semibold">comlan智标</div>
            <div className="text-xs text-indigo-200">AI 标书撰写与审查</div>
          </div>
        </div>
      </div>
      <nav className="space-y-1 p-3">
        {navItems.map((item) => {
          const Icon = item.icon;
          const active = current === item.key;
          return (
            <button
              key={item.key}
              onClick={() => onChange(item.key)}
              className={`flex h-11 w-full items-center gap-3 rounded-md px-3 text-left text-sm transition ${
                active ? "bg-white text-brand-700 shadow-sm" : "text-indigo-100 hover:bg-white/10 hover:text-white"
              }`}
            >
              <Icon size={18} />
              <span>{item.label}</span>
            </button>
          );
        })}
      </nav>
      <div className="mt-auto border-t border-white/10 p-4 text-xs text-indigo-200">本地知识库已连接 · mock 模式</div>
    </aside>
  );
}

function TopBar() {
  return (
    <header className="flex h-16 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-6">
      <div>
        <div className="text-sm text-slate-500">当前项目</div>
        <div className="font-semibold text-slate-900">{project.name}</div>
      </div>
      <div className="flex items-center gap-3 text-sm">
        <Pill tone="indigo">{project.stage}</Pill>
        <span className="text-slate-500">截止：{project.deadline}</span>
        <button className="rounded-md border border-slate-200 px-3 py-2 text-slate-700 hover:bg-slate-50">切换项目</button>
      </div>
    </header>
  );
}

function OverviewPage({ onNavigate }: { onNavigate: (page: PageKey) => void }) {
  const progresses = [
    ["解析完成度", project.parse],
    ["响应矩阵完成度", project.matrix],
    ["商务标完成度", project.business],
    ["技术标完成度", project.technical],
    ["审查完成度", project.review]
  ] as const;
  return (
    <div className="space-y-5">
      <SectionTitle title="项目概览" desc="围绕废标风险、评分项覆盖和章节生成进度进行项目级监控。" />
      <div className="grid grid-cols-[1.4fr_1fr] gap-5">
        <Panel>
          <div className="grid grid-cols-2 gap-4">
            <Info label="项目名称" value={project.name} />
            <Info label="招标单位" value={project.owner} />
            <Info label="项目编号" value={project.code} />
            <Info label="截止时间" value={project.deadline} />
            <Info label="当前阶段" value={project.stage} />
            <Info label="评分点覆盖率" value={`${Math.round((stats.responded / stats.scoreTotal) * 100)}%`} />
          </div>
        </Panel>
        <Panel>
          <div className="grid grid-cols-2 gap-3">
            <Stat label="高风险" value={stats.high} tone="red" />
            <Stat label="中风险" value={stats.medium} tone="amber" />
            <Stat label="低风险" value={stats.low} tone="blue" />
            <Stat label="已通过" value={stats.passed} tone="green" />
          </div>
        </Panel>
      </div>
      <div className="grid grid-cols-5 gap-4">
        {progresses.map(([label, value]) => (
          <Panel key={label}>
            <div className="mb-3 flex items-center justify-between">
              <span className="text-sm font-medium text-slate-700">{label}</span>
              <span className="font-semibold text-brand-700">{value}%</span>
            </div>
            <div className="h-2 rounded-full bg-slate-100">
              <div className="h-2 rounded-full bg-brand-500" style={{ width: `${value}%` }} />
            </div>
          </Panel>
        ))}
      </div>
      <div className="grid grid-cols-[1fr_1fr] gap-5">
        <Panel title="评分点响应">
          <div className="mb-4 grid grid-cols-3 gap-3">
            <Stat label="评分项" value={stats.scoreTotal} tone="indigo" />
            <Stat label="已响应" value={stats.responded} tone="green" />
            <Stat label="待补充" value={stats.pending} tone="amber" />
          </div>
          <button onClick={() => onNavigate("matrix")} className="inline-flex items-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-sm text-white hover:bg-brand-700">
            查看响应矩阵 <ArrowRight size={16} />
          </button>
        </Panel>
        <Panel title="待办事项">
          {["补充第三个医疗设备合同案例", "核对报价文件前后一致性", "完善技术偏离表关键参数数值", "确认 ISO9001 证书有效期截图", "完成资格证明文件章节审查"].map((todo, index) => (
            <div key={todo} className="flex items-center justify-between border-b border-slate-100 py-3 last:border-0">
              <div className="flex items-center gap-3">
                <span className="flex h-6 w-6 items-center justify-center rounded-full bg-indigo-50 text-xs text-brand-700">{index + 1}</span>
                <span className="text-sm">{todo}</span>
              </div>
              <Pill tone={index < 2 ? "amber" : "slate"}>{index < 2 ? "今日" : "待处理"}</Pill>
            </div>
          ))}
        </Panel>
      </div>
    </div>
  );
}

function ParsePage({
  parseTab,
  setParseTab,
  onMatrix
}: {
  parseTab: string;
  setParseTab: (tab: string) => void;
  onMatrix: () => void;
}) {
  return (
    <div className="space-y-5">
      <SectionTitle title="招标解析" desc="上传招标文件后提取资格、废标、评分和目录模板信息，并允许人工校正。" />
      <Panel>
        <div className="grid grid-cols-[1fr_auto] gap-6">
          <div className="rounded-lg border border-dashed border-brand-300 bg-indigo-50/60 p-6">
            <div className="flex items-center gap-4">
              <div className="flex h-12 w-12 items-center justify-center rounded-lg bg-white text-brand-600 shadow-sm">
                <Upload size={22} />
              </div>
              <div>
                <div className="font-semibold">上传招标文件</div>
                <div className="mt-1 text-sm text-slate-500">支持 doc、docx、pdf，可拖拽或点击选择文件</div>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <button className="inline-flex items-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-sm text-white">
              <Play size={16} /> 开始解析
            </button>
            <button className="inline-flex items-center gap-2 rounded-md border border-slate-200 px-4 py-2 text-sm">
              <RefreshCw size={16} /> 重新解析
            </button>
          </div>
        </div>
      </Panel>
      <Panel>
        <div className="mb-4 flex border-b border-slate-200">
          {parseTabs.map((tab) => (
            <button
              key={tab}
              onClick={() => setParseTab(tab)}
              className={`px-4 py-3 text-sm font-medium ${parseTab === tab ? "border-b-2 border-brand-600 text-brand-700" : "text-slate-500"}`}
            >
              {tab}
            </button>
          ))}
        </div>
        <textarea key={parseTab} className="min-h-72 w-full resize-none rounded-md border border-slate-200 bg-slate-50 p-4 text-sm leading-7 outline-none focus:border-brand-400" defaultValue={mockParseText(parseTab)} />
        <div className="mt-5 flex justify-end gap-3">
          <button className="rounded-md border border-slate-200 px-4 py-2 text-sm">确认解析结果</button>
          <button onClick={onMatrix} className="rounded-md bg-brand-600 px-4 py-2 text-sm text-white">生成响应矩阵</button>
        </div>
      </Panel>
    </div>
  );
}

function TenderCreatePage({ onComplete }: { onComplete: () => void }) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [fileInfo, setFileInfo] = useState<FileInfo | null>(null);
  const [selectedLibraries, setSelectedLibraries] = useState(knowledgeLibraries);
  const [alert, setAlert] = useState("");
  const [isParsing, setIsParsing] = useState(false);
  const [activeStep, setActiveStep] = useState(-1);

  const handleFile = (file?: File) => {
    if (!file) return;
    const extension = file.name.split(".").pop()?.toLowerCase();
    if (!extension || !["doc", "docx", "pdf"].includes(extension)) {
      setAlert("仅支持 Word 或 PDF 文件");
      return;
    }
    setAlert("");
    setFileInfo({ name: file.name, size: file.size, type: extension.toUpperCase() });
  };

  const startParse = () => {
    if (!fileInfo) {
      setAlert("请先上传招标文件");
      window.alert("请先上传招标文件");
      return;
    }
    setAlert("");
    setIsParsing(true);
    setActiveStep(0);
    parseSteps.forEach((_, index) => {
      window.setTimeout(() => {
        setActiveStep(index);
        if (index === parseSteps.length - 1) {
          window.setTimeout(() => {
            setIsParsing(false);
            onComplete();
          }, 650);
        }
      }, index * 650);
    });
  };

  const toggleLibrary = (library: string) => {
    setSelectedLibraries((items) => (items.includes(library) ? items.filter((item) => item !== library) : [...items, library]));
  };

  return (
    <div className="mx-auto flex min-h-[calc(100vh-112px)] max-w-4xl items-center">
      <div className="w-full space-y-5">
        <div className="text-center">
          <h1 className="text-2xl font-semibold">标书项目创建</h1>
          <p className="mx-auto mt-3 max-w-2xl text-sm leading-6 text-slate-500">
            上传招标文件，系统将自动解析投标文件组成、评分项、响应要求，并按章节辅助生成商务标和技术标。
          </p>
        </div>
        <Panel className="p-6">
          <div className="grid grid-cols-3 gap-3">
            <TenderTypeCard title="技术标" desc="撰写技术方案、实施方案、售后服务方案、培训方案等" active />
            <TenderTypeCard title="商务标" desc="撰写资格文件、商务响应、业绩证明、承诺函等" active />
            <TenderTypeCard title="报价标" desc="整理报价文件、报价明细、开标一览表等" disabled />
          </div>
          <div
            onDragOver={(event: any) => event.preventDefault()}
            onDrop={(event: any) => {
              event.preventDefault();
              handleFile(event.dataTransfer.files[0]);
            }}
            onClick={() => fileRef.current?.click()}
            className="mt-6 cursor-pointer rounded-lg border border-dashed border-brand-300 bg-indigo-50/50 p-8 text-center hover:bg-indigo-50"
          >
            <input
              ref={fileRef}
              type="file"
              className="hidden"
              accept=".doc,.docx,.pdf,application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
              onChange={(event: any) => handleFile(event.target.files?.[0])}
            />
            <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-xl bg-white text-brand-600 shadow-sm">
              <Upload size={24} />
            </div>
            <div className="mt-4 font-semibold">点击上传或拖拽招标文件到此处</div>
            <div className="mt-2 text-sm text-slate-500">支持 .doc、.docx、.pdf，建议 50MB 以内</div>
          </div>
          {alert && <div className="mt-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{alert}</div>}
          {fileInfo && (
            <div className="mt-4 flex items-center justify-between rounded-md border border-slate-200 bg-white p-3">
              <div className="flex items-center gap-3">
                <FileText className="text-brand-600" size={20} />
                <div>
                  <div className="text-sm font-medium">{fileInfo.name}</div>
                  <div className="mt-1 text-xs text-slate-500">
                    {formatFileSize(fileInfo.size)} · {fileInfo.type}
                  </div>
                </div>
              </div>
              <button
                onClick={(event: any) => {
                  event.stopPropagation();
                  setFileInfo(null);
                  if (fileRef.current) fileRef.current.value = "";
                }}
                className="rounded-md border border-slate-200 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50"
              >
                删除
              </button>
            </div>
          )}
          <div className="mt-6 rounded-lg border border-slate-200 p-4">
            <div className="mb-3 flex items-center justify-between">
              <div>
                <div className="font-semibold">引用知识库选择</div>
                <div className="mt-1 text-sm text-slate-500">AI生成时将优先引用所选知识库资料</div>
              </div>
              <button onClick={() => setSelectedLibraries(knowledgeLibraries)} className="text-sm text-brand-700">全选</button>
            </div>
            <div className="grid grid-cols-5 gap-2">
              {knowledgeLibraries.map((library) => (
                <label key={library} className="flex cursor-pointer items-center gap-2 rounded-md border border-slate-200 px-3 py-2 text-sm">
                  <input type="checkbox" checked={selectedLibraries.includes(library)} onChange={() => toggleLibrary(library)} />
                  {library}
                </label>
              ))}
            </div>
          </div>
          {isParsing && (
            <div className="mt-5 rounded-lg bg-slate-50 p-4">
              <div className="mb-3 flex items-center justify-between text-sm">
                <span className="font-medium">解析进度</span>
                <span className="text-brand-700">{Math.round(((activeStep + 1) / parseSteps.length) * 100)}%</span>
              </div>
              <div className="mb-3 h-2 rounded-full bg-slate-200">
                <div className="h-2 rounded-full bg-brand-600 transition-all" style={{ width: `${((activeStep + 1) / parseSteps.length) * 100}%` }} />
              </div>
              <div className="grid grid-cols-5 gap-2 text-xs">
                {parseSteps.map((step, index) => (
                  <div key={step} className={`rounded-md px-2 py-2 ${index <= activeStep ? "bg-indigo-50 text-brand-700" : "bg-white text-slate-400"}`}>
                    {step}
                  </div>
                ))}
              </div>
            </div>
          )}
          <div className="mt-6 flex justify-end gap-3">
            <button className="rounded-md border border-slate-200 px-4 py-2 text-sm">查看解析历史</button>
            <button onClick={startParse} disabled={isParsing} className="rounded-md bg-brand-600 px-5 py-2 text-sm text-white hover:bg-brand-700 disabled:opacity-60">
              开始解析
            </button>
          </div>
        </Panel>
      </div>
    </div>
  );
}

function TenderTypeCard({ title, desc, active, disabled }: { title: string; desc: string; active?: boolean; disabled?: boolean }) {
  return (
    <div className={`relative rounded-lg border p-4 ${disabled ? "border-slate-200 bg-slate-50 text-slate-400" : active ? "border-brand-300 bg-indigo-50" : "border-slate-200 bg-white"}`}>
      <div className="font-semibold">{title}</div>
      <p className="mt-2 min-h-10 text-sm leading-5">{desc}</p>
      {disabled && <span className="absolute right-3 top-3 rounded-full bg-slate-200 px-2 py-1 text-xs text-slate-500">暂未开放</span>}
    </div>
  );
}

function MatrixPage({
  rows,
  filter,
  setFilter,
  onOpen
}: {
  rows: MatrixRow[];
  filter: { type: string; risk: string; status: string };
  setFilter: (filter: { type: string; risk: string; status: string }) => void;
  onOpen: (row: MatrixRow) => void;
}) {
  return (
    <div className="space-y-5">
      <SectionTitle title="响应矩阵" desc="建立招标要求、投标响应、知识库资料与标书章节的追踪关系。" />
      <Panel>
        <div className="mb-4 flex items-center gap-3">
          <Filter size={18} className="text-slate-500" />
          <Select value={filter.type} onChange={(type) => setFilter({ ...filter, type })} options={["全部", "资格项", "废标项", "商务项", "技术项", "评分项", "报价项"]} />
          <Select value={filter.risk} onChange={(risk) => setFilter({ ...filter, risk })} options={["全部", "高风险", "中风险", "低风险", "格式提示"]} />
          <Select value={filter.status} onChange={(status) => setFilter({ ...filter, status })} options={["全部", "已响应", "待补充", "高风险", "人工确认"]} />
        </div>
        <div className="overflow-hidden rounded-lg border border-slate-200">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50 text-xs uppercase text-slate-500">
              <tr>
                {["编号", "招标要求原文", "类型", "分值", "必须", "推荐章节", "知识库资料", "响应位置", "状态", "操作"].map((head) => (
                  <th key={head} className="px-3 py-3 font-semibold">{head}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 bg-white">
              {rows.map((row) => (
                <tr key={row.id} onClick={() => onOpen(row)} className="cursor-pointer hover:bg-indigo-50/50">
                  <td className="px-3 py-3 font-medium text-brand-700">{row.id}</td>
                  <td className="max-w-xs px-3 py-3 text-slate-700">{row.source}</td>
                  <td className="px-3 py-3"><Pill tone="indigo">{row.type}</Pill></td>
                  <td className="px-3 py-3">{row.score}</td>
                  <td className="px-3 py-3">{row.required ? "是" : "否"}</td>
                  <td className="px-3 py-3">{row.chapter}</td>
                  <td className="max-w-[160px] px-3 py-3 text-slate-600">{row.knowledge}</td>
                  <td className="px-3 py-3">{row.position}</td>
                  <td className="px-3 py-3"><StatusPill status={row.status} /></td>
                  <td className="px-3 py-3">
                    <div className="flex gap-2 text-brand-700">
                      <Eye size={16} />
                      <Edit3 size={16} />
                      <MapPin size={16} />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}

function GeneratePage(props: {
  chapters: Chapter[];
  activeChapter: Chapter;
  selectedChapter: string;
  setSelectedChapter: (name: string) => void;
  draft: string;
  generateOutline: () => void;
  generateBody: () => void;
  save: () => void;
  confirm: () => void;
  review: () => void;
  adoptedDocs: Record<string, boolean>;
  setAdoptedDocs: (docs: Record<string, boolean>) => void;
  knowledgeFiles: KnowledgeFile[];
}) {
  return (
    <div className="grid h-[calc(100vh-112px)] grid-cols-[260px_minmax(0,1fr)_320px] gap-5">
      <Panel className="overflow-hidden p-0">
        <div className="border-b border-slate-200 p-4 font-semibold">章节目录</div>
        <div className="scrollbar-thin h-full overflow-auto p-3">
          {props.chapters.map((chapter) => (
            <button
              key={chapter.name}
              onClick={() => props.setSelectedChapter(chapter.name)}
              className={`mb-2 w-full rounded-md border p-3 text-left transition ${
                chapter.name === props.selectedChapter ? "border-brand-300 bg-indigo-50" : "border-slate-200 bg-white hover:bg-slate-50"
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="font-medium">{chapter.name}</span>
                <ChapterStatusPill status={chapter.status} />
              </div>
              <div className="mt-2 flex gap-2 text-xs text-slate-500">
                <span>风险 {chapter.risks}</span>
                <span>评分项 {chapter.scores}</span>
              </div>
            </button>
          ))}
        </div>
      </Panel>
      <Panel className="flex min-w-0 flex-col overflow-hidden">
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            <h2 className="text-xl font-semibold">{props.selectedChapter}</h2>
            <div className="mt-2 flex flex-wrap gap-2 text-xs">
              <Pill tone="indigo">绑定评分项 {props.activeChapter.scores}</Pill>
              <Pill tone="blue">绑定招标要求 {props.activeChapter.requirements}</Pill>
              <Pill tone="green">已引用资料 {props.activeChapter.references}</Pill>
            </div>
          </div>
          <div className="flex flex-wrap justify-end gap-2">
            <ActionButton icon={Wand2} label="生成本章大纲" onClick={props.generateOutline} />
            <ActionButton icon={Sparkles} label="生成本章正文" onClick={props.generateBody} />
            <ActionButton icon={Save} label="保存" onClick={props.save} plain />
            <ActionButton icon={CircleCheck} label="标记为已确认" onClick={props.confirm} plain />
            <ActionButton icon={ShieldCheck} label="章节审查" onClick={props.review} plain />
          </div>
        </div>
        <div className="word-paper scrollbar-thin flex-1 overflow-auto rounded-lg border border-slate-200 bg-white p-8 shadow-inner">
          <pre className="whitespace-pre-wrap text-[15px] leading-8 text-slate-800">
            {props.draft || "当前章节尚未生成内容。请点击“生成本章大纲”或“生成本章正文”，系统将基于绑定招标要求、评分项和知识库资料生成本章内容。"}
          </pre>
        </div>
      </Panel>
      <div className="scrollbar-thin space-y-4 overflow-auto">
        <Panel title="本章任务卡">
          <Task label="本章绑定招标要求" value={`${props.activeChapter.requirements} 项`} />
          <Task label="本章绑定评分项" value={`${props.activeChapter.scores} 项`} />
          <Task label="当前响应状态" value={props.activeChapter.status} />
          <Task label="待补充项" value={`${Math.max(0, props.activeChapter.risks - 1)} 项`} />
        </Panel>
        <Panel title="本章绑定评分项">
          {["技术参数响应完整性", "实施计划可落地性", "售后服务承诺", "培训与验收安排"].map((item, index) => (
            <div key={item} className="flex items-center justify-between border-b border-slate-100 py-2 text-sm last:border-0">
              <span>{item}</span>
              <span className="font-medium text-brand-700">{index + 2}分</span>
            </div>
          ))}
        </Panel>
        <Panel title="本章推荐知识库资料">
          <div className="space-y-2">
            {props.knowledgeFiles.slice(0, 5).map((doc) => {
              const adopted = props.adoptedDocs[doc.id] || props.adoptedDocs[doc.name];
              const insertableCount = doc.assets.filter((asset) => asset.insertable).length;
              return (
                <div key={doc.id} className="rounded-md border border-slate-200 p-3">
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <span className="text-sm font-medium">{doc.name}</span>
                    <Pill tone={adopted ? "green" : "slate"}>{adopted ? "已采用" : "推荐"}</Pill>
                  </div>
                  <div className="mb-2 grid grid-cols-2 gap-2 text-xs text-slate-500">
                    <span>{doc.category}</span>
                    <span className="truncate">{doc.tags.join("、") || "未设置标签"}</span>
                  </div>
                  {insertableCount > 0 && (
                    <div className="mb-2 rounded bg-emerald-50 px-2 py-1 text-xs text-emerald-700">
                      含可插入图片资产 {insertableCount} 个
                    </div>
                  )}
                  <div className="flex flex-wrap gap-2 text-xs">
                    <button onClick={() => props.setAdoptedDocs({ ...props.adoptedDocs, [doc.id]: true })} className="text-emerald-700">采用</button>
                    <button onClick={() => props.setAdoptedDocs({ ...props.adoptedDocs, [doc.id]: false })} className="text-slate-600">取消采用</button>
                    <button className="text-brand-700">查看资产</button>
                  </div>
                </div>
              );
            })}
          </div>
        </Panel>
        <Panel title="本章风险提示">
          <Task label="高风险" value={props.activeChapter.risks > 2 ? "1 个" : "0 个"} danger={props.activeChapter.risks > 2} />
          <Task label="中低风险" value={`${props.activeChapter.risks} 个`} danger={props.activeChapter.risks > 0} />
        </Panel>
        <Panel title="章节审查结果">
          <div className="rounded-md bg-indigo-50 p-3 text-sm leading-6 text-brand-700">
            当前章节已完成基础检查。建议继续核对评分项覆盖、附件页码和资质有效期。
          </div>
        </Panel>
      </div>
    </div>
  );
}

function ReviewPage({
  risks,
  onJump,
  onHandled,
  onAiFix
}: {
  risks: RiskItem[];
  onJump: (chapter: string, riskTitle?: string) => void;
  onHandled: (id: string) => void;
  onAiFix: (risk: RiskItem) => void;
}) {
  return (
    <div className="space-y-5">
      <SectionTitle title="审查中心" desc="审查废标风险、评分覆盖、资料缺失、资质过期、内容一致性和签字盖章问题。" />
      <Panel>
        <div className="grid grid-cols-3 gap-4">
          <Info label="当前审查文件名" value="XX市卫健委医疗设备采购项目_技术标_v0.8.docx" />
          <Info label="审查完成时间" value="2026-06-15 10:42" />
          <Info label="审查状态" value="发现风险，待处理" />
        </div>
      </Panel>
      <div className="grid grid-cols-5 gap-4">
        <Stat label="高风险" value={stats.high} tone="red" />
        <Stat label="中风险" value={stats.medium} tone="amber" />
        <Stat label="低风险" value={stats.low} tone="blue" />
        <Stat label="格式提示" value={5} tone="slate" />
        <Stat label="已通过项" value={stats.passed} tone="green" />
      </div>
      <Panel title="风险等级说明">
        <div className="grid grid-cols-4 gap-3 text-sm">
          <RiskLegend level="高风险" desc="可能废标，必须处理" />
          <RiskLegend level="中风险" desc="可能扣分，建议处理" />
          <RiskLegend level="低风险" desc="表述不足，建议优化" />
          <RiskLegend level="格式提示" desc="人工确认" />
        </div>
      </Panel>
      <Panel title="风险分类">
        <div className="flex flex-wrap gap-2">
          {riskCategories.map((item) => <Pill key={item} tone="slate">{item}</Pill>)}
        </div>
      </Panel>
      <div className="space-y-4">
        {risks.map((risk) => (
          <Panel key={risk.id}>
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="mb-2 flex items-center gap-2">
                  <RiskPill level={risk.level} />
                  <Pill tone="slate">{risk.type}</Pill>
                  {risk.handled && <Pill tone="green">已处理</Pill>}
                </div>
                <h3 className="font-semibold">{risk.title}</h3>
                <div className="mt-3 grid grid-cols-2 gap-3 text-sm text-slate-600">
                  <Info label="关联招标条款" value={risk.clause} compact />
                  <Info label="定位位置" value={risk.position} compact />
                  <Info label="影响说明" value={risk.impact} compact />
                  <Info label="修改建议" value={risk.suggestion} compact />
                </div>
              </div>
              <div className="flex shrink-0 flex-col gap-2">
                <button className="rounded-md border border-slate-200 px-3 py-2 text-sm">定位原文</button>
                <button onClick={() => onJump(risk.chapter, risk.title)} className="rounded-md border border-brand-200 px-3 py-2 text-sm text-brand-700">跳转章节</button>
                <button onClick={() => onAiFix(risk)} className="rounded-md border border-brand-200 px-3 py-2 text-sm text-brand-700">AI修复建议</button>
                <button onClick={() => onHandled(risk.id)} className="rounded-md bg-emerald-600 px-3 py-2 text-sm text-white">标记已处理</button>
                <button className="rounded-md border border-slate-200 px-3 py-2 text-sm">忽略</button>
              </div>
            </div>
          </Panel>
        ))}
      </div>
    </div>
  );
}

function KnowledgePage({
  files,
  setFiles
}: {
  files: KnowledgeFile[];
  setFiles: (files: KnowledgeFile[] | ((files: KnowledgeFile[]) => KnowledgeFile[])) => void;
}) {
  const uploadRef = useRef<HTMLInputElement>(null);
  const [alert, setAlert] = useState("");
  const [search, setSearch] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("全部分类");
  const [selectedFile, setSelectedFile] = useState<KnowledgeFile | null>(null);
  const [detailTab, setDetailTab] = useState("基础信息");
  const [previewAsset, setPreviewAsset] = useState<{ file: KnowledgeFile; asset: KnowledgeAsset } | null>(null);

  const filteredFiles = files.filter((file) => {
    const searchText = `${file.name} ${file.category} ${file.tags.join(" ")} ${file.recentProject}`.toLowerCase();
    const searchOk = !search.trim() || searchText.includes(search.trim().toLowerCase());
    const categoryOk = categoryFilter === "全部分类" || file.category === categoryFilter;
    return searchOk && categoryOk;
  });

  const handleUpload = (file?: File) => {
    if (!file) return;
    const extension = file.name.split(".").pop()?.toLowerCase();
    if (!extension || !["doc", "docx", "pdf", "png", "jpg", "jpeg", "webp"].includes(extension)) {
      setAlert("仅支持 Word、PDF 或图片文件");
      return;
    }
    const objectUrl = file.type.startsWith("image/") ? URL.createObjectURL(file) : undefined;
    setAlert("");
    setFiles((items) => [createKnowledgeFile(file, extension, objectUrl), ...items]);
    if (uploadRef.current) uploadRef.current.value = "";
  };

  const updateFile = (id: string, patch: Partial<KnowledgeFile>) => {
    setFiles((items) => items.map((file) => (file.id === id ? { ...file, ...patch } : file)));
    setSelectedFile((file) => (file?.id === id ? { ...file, ...patch } : file));
  };

  const deleteFile = (id: string) => {
    setFiles((items) => items.filter((file) => file.id !== id));
    setSelectedFile((file) => (file?.id === id ? null : file));
  };

  const updateAsset = (fileId: string, assetId: string, patch: Partial<KnowledgeAsset>) => {
    setFiles((items) =>
      items.map((file) =>
        file.id === fileId
          ? { ...file, assets: file.assets.map((asset) => (asset.id === assetId ? { ...asset, ...patch } : asset)) }
          : file
      )
    );
    setSelectedFile((file) =>
      file?.id === fileId
        ? { ...file, assets: file.assets.map((asset) => (asset.id === assetId ? { ...asset, ...patch } : asset)) }
        : file
    );
  };

  return (
    <div className="space-y-5">
      <SectionTitle title="企业资料资产库" desc="管理源文件、解析文本和可插入图片资产，为后续标书生成提供可追溯资料来源。" />
      <Panel>
        <div
          onDragOver={(event: any) => event.preventDefault()}
          onDrop={(event: any) => {
            event.preventDefault();
            handleUpload(event.dataTransfer.files[0]);
          }}
          className="mb-5 rounded-lg border border-dashed border-brand-300 bg-indigo-50/50 p-5"
        >
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-4">
              <div className="flex h-12 w-12 items-center justify-center rounded-lg bg-white text-brand-600 shadow-sm">
                <Paperclip size={22} />
              </div>
              <div>
                <div className="font-semibold">上传企业资料源文件</div>
                <div className="mt-1 text-sm text-slate-500">支持 Word、PDF、PNG、JPG、WebP。可点击上传，也可拖拽文件到此区域。</div>
              </div>
            </div>
            <input
              ref={uploadRef}
              type="file"
              className="hidden"
              accept=".doc,.docx,.pdf,.png,.jpg,.jpeg,.webp,application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,image/png,image/jpeg,image/webp"
              onChange={(event: any) => handleUpload(event.target.files?.[0])}
            />
            <button onClick={() => uploadRef.current?.click()} className="inline-flex items-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-sm text-white">
              <Upload size={16} /> 上传资料
            </button>
          </div>
          {alert && <div className="mt-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{alert}</div>}
        </div>
        <div className="mb-4 flex items-center justify-between gap-4">
          <div className="flex flex-1 items-center gap-3">
            <div className="relative max-w-sm flex-1">
              <Search className="absolute left-3 top-2.5 text-slate-400" size={18} />
              <input value={search} onChange={(event: any) => setSearch(event.target.value)} className="w-full rounded-md border border-slate-200 py-2 pl-10 pr-3 text-sm" placeholder="搜索资料名称、标签、项目" />
            </div>
            <Select value={categoryFilter} onChange={setCategoryFilter} options={["全部分类", ...knowledgeLibraries]} />
            <Select value="全部标签" onChange={() => undefined} options={["全部标签", "质量体系", "同类业绩", "技术参数", "售后服务"]} />
          </div>
          <Pill tone="indigo">资料 {files.length} 份</Pill>
        </div>
        <div className="overflow-hidden rounded-lg border border-slate-200">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50 text-xs text-slate-500">
              <tr>
                {["文件名称", "文件类型", "资料分类", "标签", "有效期", "资产数量", "最近引用项目", "状态", "操作"].map((head) => (
                  <th key={head} className="px-3 py-3 font-semibold">{head}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {filteredFiles.map((file) => (
                <tr key={file.id} className="bg-white">
                  <td className="px-3 py-3 font-medium text-slate-900">{file.name}</td>
                  <td className="px-3 py-3">{file.type}</td>
                  <td className="px-3 py-3">{file.category}</td>
                  <td className="max-w-[180px] px-3 py-3 text-slate-600">{file.tags.join("、") || "未设置"}</td>
                  <td className="px-3 py-3">{file.validTo || "未设置"}</td>
                  <td className="px-3 py-3">{file.assets.length} 个</td>
                  <td className="px-3 py-3">{file.recentProject}</td>
                  <td className="px-3 py-3"><DocStatus status={file.status} /></td>
                  <td className="px-3 py-3">
                    <div className="flex gap-2 text-xs text-brand-700">
                      <button onClick={() => { setSelectedFile(file); setDetailTab("基础信息"); }}>查看</button>
                      <button onClick={() => { setSelectedFile(file); setDetailTab("基础信息"); }}>编辑</button>
                      <button onClick={() => deleteFile(file.id)} className="text-red-600">删除</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
      {selectedFile && (
        <KnowledgeDetailModal
          file={selectedFile}
          tab={detailTab}
          setTab={setDetailTab}
          onClose={() => setSelectedFile(null)}
          onUpdate={updateFile}
          onPreview={(asset) => setPreviewAsset({ file: selectedFile, asset })}
          onUpdateAsset={updateAsset}
        />
      )}
      {previewAsset && (
        <AssetPreviewModal
          file={previewAsset.file}
          asset={previewAsset.asset}
          onClose={() => setPreviewAsset(null)}
        />
      )}
    </div>
  );
}

function KnowledgeDetailModal({
  file,
  tab,
  setTab,
  onClose,
  onUpdate,
  onPreview,
  onUpdateAsset
}: {
  file: KnowledgeFile;
  tab: string;
  setTab: (tab: string) => void;
  onClose: () => void;
  onUpdate: (id: string, patch: Partial<KnowledgeFile>) => void;
  onPreview: (asset: KnowledgeAsset) => void;
  onUpdateAsset: (fileId: string, assetId: string, patch: Partial<KnowledgeAsset>) => void;
}) {
  const tabs = ["基础信息", "源文件", "解析文本", "图片资产"];
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-900/35 p-6">
      <div className="flex max-h-[88vh] w-[980px] flex-col overflow-hidden rounded-lg bg-white shadow-2xl">
        <div className="flex items-center justify-between border-b border-slate-200 p-5">
          <div>
            <div className="text-xs text-slate-500">资料详情</div>
            <h3 className="mt-1 font-semibold">{file.name}</h3>
          </div>
          <button onClick={onClose} className="rounded-md p-2 hover:bg-slate-100"><X size={18} /></button>
        </div>
        <div className="flex border-b border-slate-200 px-5">
          {tabs.map((item) => (
            <button
              key={item}
              onClick={() => setTab(item)}
              className={`px-4 py-3 text-sm font-medium ${tab === item ? "border-b-2 border-brand-600 text-brand-700" : "text-slate-500"}`}
            >
              {item}
            </button>
          ))}
        </div>
        <div className="scrollbar-thin overflow-auto p-5">
          {tab === "基础信息" && (
            <div className="grid grid-cols-2 gap-4">
              <Info label="文件名称" value={file.name} />
              <Info label="文件类型" value={file.type} />
              <Info label="文件大小" value={formatFileSize(file.size)} />
              <Info label="上传时间" value={file.uploadedAt} />
              <EditableSelect label="资料分类" value={file.category} options={knowledgeLibraries} onChange={(category) => onUpdate(file.id, { category: category as KnowledgeCategory })} />
              <EditableInput label="标签" value={file.tags.join("、")} onChange={(value) => onUpdate(file.id, { tags: splitTags(value) })} />
              <EditableInput label="有效期" value={file.validTo || ""} placeholder="例如 2028-12-31" onChange={(value) => onUpdate(file.id, { validTo: value, status: getStatusByValidTo(value) })} />
              <Info label="状态" value={file.status} />
              <EditableInput label="适用行业" value={file.applicableIndustry} onChange={(value) => onUpdate(file.id, { applicableIndustry: value })} />
              <EditableInput label="适用产品" value={file.applicableProduct} onChange={(value) => onUpdate(file.id, { applicableProduct: value })} />
              <div className="col-span-2">
                <EditableInput label="备注" value={file.remark} onChange={(value) => onUpdate(file.id, { remark: value })} />
              </div>
            </div>
          )}
          {tab === "源文件" && (
            <div className="space-y-4">
              <div className="grid grid-cols-3 gap-4">
                <Info label="源文件名称" value={file.sourceFile.name} />
                <Info label="文件类型" value={file.sourceFile.type} />
                <Info label="文件大小" value={formatFileSize(file.sourceFile.size)} />
              </div>
              {file.sourceFile.objectUrl ? (
                <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
                  <img src={file.sourceFile.objectUrl} alt={file.name} className="max-h-96 rounded-md object-contain" />
                </div>
              ) : (
                <div className="flex min-h-52 items-center justify-center rounded-lg border border-dashed border-slate-300 bg-slate-50">
                  <div className="text-center">
                    <FileText className="mx-auto text-brand-600" size={38} />
                    <div className="mt-3 font-medium">{file.sourceFile.name}</div>
                    <div className="mt-1 text-sm text-slate-500">预览占位</div>
                  </div>
                </div>
              )}
              <div className="rounded-md bg-indigo-50 p-3 text-sm leading-6 text-brand-700">
                源文件将被保留，后续生成标书时可引用原始文件或提取页面图片插入文档。
              </div>
            </div>
          )}
          {tab === "解析文本" && (
            <div className="space-y-4">
              <DrawerBlock title="摘要" text={file.parsedText.summary} />
              <DrawerBlock title="关键词" text={file.parsedText.keywords.join("、")} />
              <DrawerBlock title="OCR文本" text={file.parsedText.ocrText} />
              <DrawerBlock title="可用于哪些章节" text={file.parsedText.chapters.join("、")} />
              <DrawerBlock title="可用于哪些评分项" text={file.parsedText.scoreItems.join("、")} />
            </div>
          )}
          {tab === "图片资产" && (
            <div className="grid grid-cols-3 gap-4">
              {file.assets.map((asset) => (
                <div key={asset.id} className="rounded-lg border border-slate-200 bg-white p-4">
                  <div className="mb-3 flex h-28 items-center justify-center rounded-md bg-slate-100">
                    {asset.previewUrl ? (
                      <img src={asset.previewUrl} alt={asset.name} className="h-full max-w-full rounded object-contain" />
                    ) : (
                      <div className="text-center text-slate-500">
                        <FileText className="mx-auto mb-2" size={28} />
                        资产预览占位
                      </div>
                    )}
                  </div>
                  <div className="font-medium">{asset.name}</div>
                  <div className="mt-2 space-y-1 text-xs text-slate-500">
                    <div>资产类型：{asset.assetType}</div>
                    <div>来源页码：{asset.sourcePage}</div>
                    <div>推荐用途：{asset.recommendedUsage}</div>
                    <div>是否可插入标书：{asset.insertable ? "是" : "否"}</div>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2 text-xs">
                    <button onClick={() => onPreview(asset)} className="text-brand-700">预览</button>
                    <button onClick={() => onUpdateAsset(file.id, asset.id, { insertable: true })} className="text-emerald-700">设为可插入</button>
                    <button onClick={() => onUpdateAsset(file.id, asset.id, { insertable: false })} className="text-slate-600">取消插入</button>
                    <button className="text-amber-700">裁剪</button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function AssetPreviewModal({ file, asset, onClose }: { file: KnowledgeFile; asset: KnowledgeAsset; onClose: () => void }) {
  const modeText: Record<InsertMode, string> = {
    page_width: "页面宽度适配",
    half_width: "半页宽度适配",
    original_ratio: "原比例插入",
    table_cell: "表格内插入"
  };
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-6">
      <div className="w-[680px] rounded-lg bg-white p-5 shadow-2xl">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <div className="text-xs text-slate-500">图片资产预览</div>
            <h3 className="mt-1 font-semibold">{asset.name}</h3>
          </div>
          <button onClick={onClose} className="rounded-md p-2 hover:bg-slate-100"><X size={18} /></button>
        </div>
        <div className="flex min-h-80 items-center justify-center rounded-lg border border-slate-200 bg-slate-50">
          {asset.previewUrl || file.sourceFile.objectUrl ? (
            <img src={asset.previewUrl || file.sourceFile.objectUrl} alt={asset.name} className="max-h-80 max-w-full rounded object-contain" />
          ) : (
            <div className="h-64 w-48 rounded border border-dashed border-slate-300 bg-white p-5 text-center text-slate-500">
              <FileText className="mx-auto mb-3 text-brand-600" size={36} />
              PDF/Word mock 资产占位图
            </div>
          )}
        </div>
        <div className="mt-4 grid grid-cols-2 gap-4 text-sm">
          <Info label="推荐插入方式" value={modeText[asset.insertMode]} />
          <Info label="推荐用途" value={asset.recommendedUsage} />
        </div>
        <div className="mt-4 rounded-md bg-indigo-50 p-3 text-sm leading-6 text-brand-700">
          后续生成 Word 时，系统将按所选方式自动缩放并插入该图片。
        </div>
      </div>
    </div>
  );
}

function EditableInput({ label, value, placeholder, onChange }: { label: string; value: string; placeholder?: string; onChange: (value: string) => void }) {
  return (
    <label className="block">
      <span className="text-xs text-slate-500">{label}</span>
      <input value={value} placeholder={placeholder} onChange={(event: any) => onChange(event.target.value)} className="mt-2 w-full rounded-md border border-slate-200 px-3 py-2 text-sm outline-none focus:border-brand-400" />
    </label>
  );
}

function EditableSelect({ label, value, options, onChange }: { label: string; value: string; options: string[]; onChange: (value: string) => void }) {
  return (
    <label className="block">
      <span className="text-xs text-slate-500">{label}</span>
      <select value={value} onChange={(event: any) => onChange(event.target.value)} className="mt-2 w-full rounded-md border border-slate-200 px-3 py-2 text-sm outline-none focus:border-brand-400">
        {options.map((option) => <option key={option}>{option}</option>)}
      </select>
    </label>
  );
}

function ExportPage({ unhandledHigh, onExport }: { unhandledHigh: number; onExport: () => void }) {
  return (
    <div className="space-y-5">
      <SectionTitle title="导出定稿" desc="导出前汇总生成状态、审查状态、未处理风险和人工确认项。" />
      <div className="grid grid-cols-3 gap-4">
        <Panel><Info label="商务标生成状态" value="已生成，待最终复核" /></Panel>
        <Panel><Info label="技术标生成状态" value="已生成，存在待优化项" /></Panel>
        <Panel><Info label="报价文件状态" value="人工确认中" /></Panel>
        <Panel><Info label="审查状态" value="发现风险，待处理" /></Panel>
        <Panel><Info label="未处理风险数量" value={`${unhandledHigh + stats.medium + stats.low} 项`} /></Panel>
        <Panel><Info label="人工确认项数量" value="6 项" /></Panel>
      </div>
      <Panel title="导出文件">
        <div className="grid grid-cols-5 gap-3">
          {["导出商务标 Word", "导出技术标 Word", "导出审查报告", "导出响应矩阵", "导出知识库引用清单"].map((label) => (
            <button key={label} onClick={onExport} className="flex h-28 flex-col items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white text-sm font-medium hover:border-brand-300 hover:bg-indigo-50">
              <Download size={22} className="text-brand-600" />
              {label}
            </button>
          ))}
        </div>
      </Panel>
    </div>
  );
}

function MatrixDrawer({
  row,
  onClose,
  onJump,
  onAiHandle
}: {
  row: MatrixRow;
  onClose: () => void;
  onJump: (chapter: string) => void;
  onAiHandle: (row: MatrixRow) => void;
}) {
  return (
    <div className="fixed inset-0 z-40 bg-slate-900/20">
      <aside className="ml-auto flex h-full w-[520px] flex-col bg-white shadow-2xl">
        <div className="flex items-center justify-between border-b border-slate-200 p-5">
          <div>
            <div className="text-sm text-slate-500">{row.id}</div>
            <h3 className="font-semibold">响应详情</h3>
          </div>
          <button onClick={onClose} className="rounded-md p-2 hover:bg-slate-100"><X size={18} /></button>
        </div>
        <div className="scrollbar-thin flex-1 space-y-4 overflow-auto p-5">
          <DrawerBlock title="招标要求原文" text={row.source} />
          <DrawerBlock title="AI解析说明" text={row.analysis} />
          <DrawerBlock title="推荐响应策略" text={row.strategy} />
          <DrawerBlock title="推荐知识库资料" text={row.knowledge} />
          <div className="rounded-md border border-slate-200 p-4">
            <div className="mb-2 text-sm font-semibold">当前风险</div>
            <RiskPill level={row.risk} />
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3 border-t border-slate-200 p-5">
          <button onClick={() => onAiHandle(row)} className="inline-flex items-center justify-center gap-2 rounded-md border border-brand-200 px-4 py-2 text-sm text-brand-700">
            让AI处理 <Bot size={16} />
          </button>
          <button onClick={() => onJump(row.chapter)} className="inline-flex items-center justify-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-sm text-white">
            跳转章节 <ExternalLink size={16} />
          </button>
        </div>
      </aside>
    </div>
  );
}

function FloatingAIAssistant({
  open,
  minimized,
  currentPage,
  generationReady,
  currentChapter,
  context,
  messages,
  setMessages,
  onToggle,
  onClose,
  onQuickAction,
  onInsertToChapter
}: {
  open: boolean;
  minimized: boolean;
  currentPage: PageKey;
  generationReady: boolean;
  currentChapter: string;
  context: AssistantContext;
  messages: AssistantMessage[];
  setMessages: (messages: AssistantMessage[] | ((messages: AssistantMessage[]) => AssistantMessage[])) => void;
  onToggle: () => void;
  onClose: () => void;
  onQuickAction: (label: string) => void;
  onInsertToChapter: (text: string) => void;
}) {
  const [input, setInput] = useState("");
  const lastAiMessage = [...messages].reverse().find((message) => message.role === "ai");
  const canInsert = currentPage === "generate" && generationReady;
  const quickActions =
    context.source === "review" && context.risk
      ? ["解释风险原因", "给出修改建议", "定位相关章节", "生成修复文本"]
      : ["生成本章大纲", "生成本章正文", "按评分项强化", "引用知识库补充案例", "检查本章漏项", "优化为正式投标语言"];

  const contextLines = [];
  if (currentPage === "generate" && generationReady) contextLines.push("当前页面：标书生成");
  if (context.chapter || (currentPage === "generate" && generationReady)) contextLines.push(`当前章节：${context.chapter || currentChapter}`);
  if (context.risk) contextLines.push(`当前风险：${context.risk}`);
  if (context.requirement) contextLines.push(`当前要求：${context.requirement.slice(0, 26)}...`);

  const sendMessage = () => {
    if (!input.trim()) return;
    const userText = input.trim();
    const reply = context.risk
      ? `针对“${context.risk}”，建议补充风险原因说明、章节修复文本和证明材料索引。可插入正文：我方已按招标条款补充完整响应，并对相关附件、页码及承诺内容完成一致性复核。`
      : `已收到：“${userText}”。建议在${currentPage === "generate" ? `“${currentChapter}”` : "当前材料"}中加入更明确的响应承诺、案例支撑和资料来源说明。`;
    setMessages((items) => [...items, { role: "user", text: userText }, { role: "ai", text: reply }]);
    setInput("");
  };

  return (
    <div className="fixed bottom-5 right-5 z-50">
      {open && !minimized && (
        <div className="mb-3 flex h-[520px] w-[360px] flex-col overflow-hidden rounded-lg border border-slate-200 bg-white shadow-2xl">
          <div className="border-b border-slate-200 bg-[#191d3a] p-4 text-white">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 font-semibold">
                <Bot size={18} /> AI助手
              </div>
              <div className="flex gap-1">
                <button onClick={onToggle} title="最小化" className="rounded p-1 hover:bg-white/10"><Minimize2 size={16} /></button>
                <button onClick={onClose} title="关闭" className="rounded p-1 hover:bg-white/10"><X size={16} /></button>
              </div>
            </div>
            <div className="mt-3 rounded-md bg-white/10 p-2 text-xs leading-5 text-indigo-100">
              {contextLines.length ? contextLines.map((line) => <div key={line}>{line}</div>) : "当前暂无上下文"}
            </div>
          </div>
          <div className="scrollbar-thin flex-1 space-y-3 overflow-auto p-4">
            {messages.map((message, index) => (
              <div key={`${message.role}-${index}`} className={`rounded-lg p-3 text-sm leading-6 ${message.role === "ai" ? "bg-indigo-50 text-slate-800" : "ml-10 bg-brand-600 text-white"}`}>
                {message.text}
              </div>
            ))}
          </div>
          <div className="border-t border-slate-200 p-3">
            <div className="mb-3 flex flex-wrap gap-2">
              {quickActions.map((label) => (
                <button key={label} onClick={() => onQuickAction(label)} className="rounded-md bg-slate-100 px-2.5 py-1.5 text-xs text-slate-700 hover:bg-indigo-50 hover:text-brand-700">
                  {label}
                </button>
              ))}
            </div>
            {lastAiMessage && (
              <div className="mb-3 flex gap-2 text-xs">
                {canInsert && (
                  <button onClick={() => onInsertToChapter(lastAiMessage.text)} className="rounded-md bg-emerald-600 px-2.5 py-1.5 text-white">
                    插入到当前章节
                  </button>
                )}
                <button className="rounded-md border border-slate-200 px-2.5 py-1.5">替换当前段落</button>
                <button
                  onClick={() => navigator.clipboard?.writeText(lastAiMessage.text)}
                  className="inline-flex items-center gap-1 rounded-md border border-slate-200 px-2.5 py-1.5"
                >
                  <ClipboardCopy size={13} /> 复制
                </button>
              </div>
            )}
            <div className="flex gap-2">
              <input
                value={input}
                onChange={(event: any) => setInput(event.target.value)}
                onKeyDown={(event: any) => {
                  if (event.key === "Enter") sendMessage();
                }}
                placeholder="请输入你的修改要求，例如“把售后服务方案扩写到3000字”"
                className="min-w-0 flex-1 rounded-md border border-slate-200 px-3 py-2 text-sm outline-none focus:border-brand-400"
              />
              <button onClick={sendMessage} className="rounded-md bg-brand-600 px-3 text-white"><Send size={16} /></button>
            </div>
          </div>
        </div>
      )}
      <button onClick={onToggle} className="inline-flex h-12 items-center gap-2 rounded-full bg-brand-600 px-5 text-sm font-medium text-white shadow-xl shadow-brand-900/20 hover:bg-brand-700">
        <MessageSquareText size={18} /> AI助手
      </button>
    </div>
  );
}

function ConfirmModal({ highCount, onClose, onConfirm }: { highCount: number; onClose: () => void; onConfirm: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/35">
      <div className="w-[460px] rounded-lg bg-white p-6 shadow-2xl">
        <div className="mb-4 flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-red-50 text-red-600">
            <AlertTriangle size={22} />
          </div>
          <div>
            <h3 className="font-semibold">导出风险确认</h3>
            <p className="text-sm text-slate-500">当前未处理高风险：{highCount} 项</p>
          </div>
        </div>
        <p className="text-sm leading-6 text-slate-700">当前仍有高风险问题未处理，可能导致废标，是否继续导出？</p>
        <div className="mt-6 flex justify-end gap-3">
          <button onClick={onClose} className="rounded-md border border-slate-200 px-4 py-2 text-sm">返回处理</button>
          <button onClick={onConfirm} className="rounded-md bg-red-600 px-4 py-2 text-sm text-white">继续导出</button>
        </div>
      </div>
    </div>
  );
}

function Panel({ children, title, className = "" }: { children: React.ReactNode; title?: string; className?: string }) {
  return (
    <section className={`rounded-lg border border-slate-200 bg-white p-5 shadow-panel ${className}`}>
      {title && <h2 className="mb-4 font-semibold">{title}</h2>}
      {children}
    </section>
  );
}

function SectionTitle({ title, desc }: { title: string; desc: string }) {
  return (
    <div>
      <h1 className="text-2xl font-semibold">{title}</h1>
      <p className="mt-1 text-sm text-slate-500">{desc}</p>
    </div>
  );
}

function Info({ label, value, compact = false }: { label: string; value: string; compact?: boolean }) {
  return (
    <div>
      <div className="text-xs text-slate-500">{label}</div>
      <div className={`${compact ? "mt-1 text-sm" : "mt-2 font-medium"} text-slate-900`}>{value}</div>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: number; tone: string }) {
  const color = toneMap(tone);
  return (
    <div className={`rounded-lg border p-4 ${color.soft}`}>
      <div className="text-sm text-slate-600">{label}</div>
      <div className={`mt-2 text-2xl font-semibold ${color.text}`}>{value}</div>
    </div>
  );
}

function Pill({ children, tone }: { children: React.ReactNode; tone: string }) {
  const color = toneMap(tone);
  return <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium ${color.pill}`}>{children}</span>;
}

function StatusPill({ status }: { status: MatrixStatus }) {
  const tone = status === "已响应" ? "green" : status === "高风险" ? "red" : status === "待补充" ? "amber" : "indigo";
  return <Pill tone={tone}>{status}</Pill>;
}

function ChapterStatusPill({ status }: { status: ChapterStatus }) {
  const tone = status === "已确认" ? "green" : status === "待优化" ? "amber" : status === "已生成" ? "blue" : "slate";
  return <Pill tone={tone}>{status}</Pill>;
}

function RiskPill({ level }: { level: RiskLevel }) {
  const tone = level === "高风险" ? "red" : level === "中风险" ? "amber" : level === "低风险" ? "blue" : "slate";
  return <Pill tone={tone}>{level}</Pill>;
}

function DocStatus({ status }: { status: string }) {
  const tone = status === "有效" ? "green" : status === "即将过期" ? "amber" : status === "未设置有效期" ? "slate" : "red";
  return <Pill tone={tone}>{status}</Pill>;
}

function ActionButton({ icon: Icon, label, onClick, plain = false }: { icon: typeof Wand2; label: string; onClick: () => void; plain?: boolean }) {
  return (
    <button
      onClick={onClick}
      title={label}
      className={`inline-flex items-center gap-2 rounded-md px-3 py-2 text-sm ${
        plain ? "border border-slate-200 bg-white text-slate-700 hover:bg-slate-50" : "bg-brand-600 text-white hover:bg-brand-700"
      }`}
    >
      <Icon size={16} /> {label}
    </button>
  );
}

function Task({ label, value, danger = false }: { label: string; value: string; danger?: boolean }) {
  return (
    <div className="flex items-center justify-between border-b border-slate-100 py-3 last:border-0">
      <span className="text-sm text-slate-600">{label}</span>
      <span className={`text-sm font-medium ${danger ? "text-red-600" : "text-slate-900"}`}>{value}</span>
    </div>
  );
}

function DrawerBlock({ title, text }: { title: string; text: string }) {
  return (
    <div className="rounded-md border border-slate-200 p-4">
      <div className="mb-2 text-sm font-semibold">{title}</div>
      <p className="text-sm leading-6 text-slate-600">{text}</p>
    </div>
  );
}

function Select({ value, onChange, options }: { value: string; onChange: (value: string) => void; options: string[] }) {
  return (
    <select value={value} onChange={(event: any) => onChange(event.target.value)} className="rounded-md border border-slate-200 bg-white px-3 py-2 text-sm">
      {options.map((option) => <option key={option}>{option}</option>)}
    </select>
  );
}

function RiskLegend({ level, desc }: { level: RiskLevel; desc: string }) {
  return (
    <div className="rounded-md border border-slate-200 p-3">
      <RiskPill level={level} />
      <div className="mt-2 text-slate-600">{desc}</div>
    </div>
  );
}

function formatFileSize(size: number) {
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function createKnowledgeFile(file: File, extension: string, objectUrl?: string): KnowledgeFile {
  const id = `kf-${Date.now()}`;
  const type = extension.toUpperCase();
  const isImage = ["png", "jpg", "jpeg", "webp"].includes(extension);
  return {
    id,
    name: file.name,
    type,
    size: file.size,
    category: isImage ? "产品资料库" : "常用素材库",
    tags: isImage ? ["图片资产", "待标注"] : ["新上传", "待解析"],
    status: "未设置有效期",
    uploadedAt: new Date().toLocaleString("zh-CN", { hour12: false }),
    sourceFile: {
      name: file.name,
      type,
      size: file.size,
      objectUrl
    },
    parsedText: {
      summary: isImage ? "图片类资料，已生成可插入标书的图片资产。" : "文档类资料，已生成 mock 解析结果，后续可接入后端解析服务。",
      keywords: isImage ? ["图片", "可插入资产"] : ["源文件", "OCR", "资料解析"],
      ocrText: isImage ? "图片 OCR 文本待识别。" : "模拟 OCR 文本：该资料可作为标书章节证明材料或附件引用。",
      chapters: isImage ? ["技术方案", "产品介绍"] : ["资格证明文件", "商务响应表", "技术方案"],
      scoreItems: isImage ? ["产品展示", "方案可视化"] : ["资料完整性", "评分项支撑"]
    },
    assets: isImage
      ? [
          {
            id: `ka-${Date.now()}-1`,
            fileId: id,
            name: `${file.name} 图片资产`,
            assetType: "产品图片",
            sourcePage: "原图",
            previewUrl: objectUrl,
            ocrText: "图片原始文件",
            recommendedUsage: "技术方案或产品介绍中插入",
            insertable: true,
            insertMode: "half_width"
          }
        ]
      : [
          {
            id: `ka-${Date.now()}-1`,
            fileId: id,
            name: "第1页整页截图",
            assetType: "其他",
            sourcePage: "第1页",
            ocrText: "文档首页截图 mock",
            recommendedUsage: "资料附件索引",
            insertable: true,
            insertMode: "page_width"
          },
          {
            id: `ka-${Date.now()}-2`,
            fileId: id,
            name: type === "PDF" ? "证书关键页" : "正文关键页",
            assetType: type === "PDF" ? "证书整页" : "合同关键页",
            sourcePage: "第2页",
            ocrText: "关键页 OCR mock",
            recommendedUsage: "资格证明或商务响应佐证",
            insertable: true,
            insertMode: "half_width"
          },
          {
            id: `ka-${Date.now()}-3`,
            fileId: id,
            name: "盖章页",
            assetType: "盖章页",
            sourcePage: "末页",
            ocrText: "盖章页 OCR mock",
            recommendedUsage: "附件真实性证明",
            insertable: false,
            insertMode: "original_ratio"
          }
        ],
    applicableIndustry: "待设置",
    applicableProduct: "待设置",
    recentProject: "暂未引用",
    remark: "前端 state 保存，后续可接入后端资料解析与资产提取。"
  };
}

function splitTags(value: string) {
  return value
    .split(/[、,，\s]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function getStatusByValidTo(value: string): KnowledgeStatus {
  if (!value.trim()) return "未设置有效期";
  const time = new Date(value).getTime();
  if (Number.isNaN(time)) return "未设置有效期";
  const now = Date.now();
  if (time < now) return "已过期";
  const days = (time - now) / 1000 / 60 / 60 / 24;
  return days <= 90 ? "即将过期" : "有效";
}

function toneMap(tone: string) {
  const colors: Record<string, { soft: string; text: string; pill: string }> = {
    red: { soft: "border-red-100 bg-red-50", text: "text-red-700", pill: "bg-red-50 text-red-700" },
    amber: { soft: "border-amber-100 bg-amber-50", text: "text-amber-700", pill: "bg-amber-50 text-amber-700" },
    blue: { soft: "border-sky-100 bg-sky-50", text: "text-sky-700", pill: "bg-sky-50 text-sky-700" },
    green: { soft: "border-emerald-100 bg-emerald-50", text: "text-emerald-700", pill: "bg-emerald-50 text-emerald-700" },
    indigo: { soft: "border-indigo-100 bg-indigo-50", text: "text-brand-700", pill: "bg-indigo-50 text-brand-700" },
    slate: { soft: "border-slate-100 bg-slate-50", text: "text-slate-700", pill: "bg-slate-100 text-slate-600" }
  };
  return colors[tone] ?? colors.slate;
}

function mockParseText(tab: string) {
  const data: Record<string, string> = {
    项目基本信息: `项目名称：XX市卫健委医疗设备采购项目\n招标单位：XX市卫生健康委员会\n项目编号：XXWJW-2026-CG-042\n投标截止时间：2026-07-08 17:30\n采购内容：医疗设备及配套安装、调试、培训、售后服务。`,
    资格要求: `1. 投标人须具备有效营业执照。\n2. 投标人近三年具有同类医疗设备供货业绩。\n3. 提供质量管理体系认证或等效质量管理证明。\n4. 不接受联合体投标。`,
    废标条款: `1. 投标文件逾期送达或未按要求密封。\n2. 资格证明文件不完整或证书过期。\n3. 技术参数未实质性响应。\n4. 报价前后不一致且无法合理说明。`,
    评分办法: `商务部分：20分，重点考察企业资质、同类业绩、服务承诺。\n技术部分：60分，重点考察技术参数、实施方案、培训方案、售后能力。\n报价部分：20分，按评标基准价计算。`,
    "标书目录/模板": `一、投标函\n二、资格证明文件\n三、商务响应表\n四、技术偏离表\n五、技术方案\n六、实施方案\n七、售后服务方案\n八、培训方案\n九、报价文件`
  };
  return data[tab] ?? "";
}

export default App;
