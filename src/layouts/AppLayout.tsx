import { BriefcaseBusiness, Building2, FolderKanban, Sparkles } from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";

const globalItems = [
  { to: "/projects", label: "项目中心", icon: FolderKanban },
  { to: "/work", label: "我的工作", icon: BriefcaseBusiness },
  { to: "/knowledge", label: "企业知识库", icon: Building2 },
];

export function AppLayout() {
  return (
    <div className="flex min-h-screen bg-slate-100 text-slate-900">
      <aside className="fixed inset-y-0 left-0 z-30 flex w-64 flex-col bg-[#191d3a] text-white">
        <div className="border-b border-white/10 px-5 py-5">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-brand-500 shadow-lg shadow-brand-900/30"><Sparkles size={20} /></div>
            <div><div className="text-lg font-semibold">comlan智标</div><div className="text-xs text-indigo-200">AI 投标文件工作台</div></div>
          </div>
        </div>
        <nav className="space-y-1 p-3" aria-label="全局导航">
          {globalItems.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} className={({ isActive }) => `flex h-11 items-center gap-3 rounded-md px-3 text-sm transition ${isActive ? "bg-white text-brand-700 shadow-sm" : "text-indigo-100 hover:bg-white/10 hover:text-white"}`}>
              <Icon size={18} /> {label}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto border-t border-white/10 p-4 text-xs leading-5 text-indigo-200">正式数据模式<br />审核数据只读连接</div>
      </aside>
      <main className="min-w-0 flex-1 pl-64"><Outlet /></main>
    </div>
  );
}
