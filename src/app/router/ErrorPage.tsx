import { Link, isRouteErrorResponse, useRouteError } from "react-router-dom";

export function RouterErrorPage() {
  const error = useRouteError();
  const message = isRouteErrorResponse(error) ? `${error.status} ${error.statusText}` : error instanceof Error ? error.message : "页面无法打开";
  return <div className="p-8"><div className="rounded-xl border border-red-200 bg-red-50 p-6"><h1 className="font-semibold text-red-900">页面加载失败</h1><p className="mt-2 text-sm text-red-700">{message}</p><Link to="/projects" className="mt-4 inline-block text-sm font-medium text-red-800">返回项目中心</Link></div></div>;
}
