# 智标工作台

## 本机启动

在两个 PowerShell 窗口分别执行：

```powershell
cd D:\comlan智标
powershell -ExecutionPolicy Bypass -File .\scripts\start-backend.ps1
```

```powershell
cd D:\comlan智标
powershell -ExecutionPolicy Bypass -File .\scripts\start-frontend.ps1
```

前端：http://127.0.0.1:5173 。后端 API 文档：http://127.0.0.1:8000/docs 。
用 Ctrl+C 停止对应服务。端口被占用时先确认原服务，不要重复启动。
前端代理默认指向 8000；人工检查时使用默认端口。

后端脚本默认使用本机已有 `D:\miniconda3\envs\comlan\python.exe`，可通过 `-Python` 指定其他已配置环境。
前端优先使用 PATH 中的 Node.js，否则尝试本机 Codex 已安装的 Node.js，可通过 `-Node` 指定。
脚本不安装依赖、不调用模型，也不自动执行业务任务；在界面点击生成等操作可能调用已配置的模型。

## 目录导航

| 路径 | 用途 |
| --- | --- |
| `src/app`、`src/pages`、`src/components` | 前端入口、页面和组件 |
| `src/api`、`src/types`、`src/utils` | 前端接口、类型及工具 |
| `src/legacy` | 历史原型，暂保留 |
| `backend/web_api.py`、`backend/app/web` | 后端 Web API 入口和服务 |
| `backend/cli.py` | 命令行入口 |
| `backend/app` | 文件解析、招标分析、写作及审核逻辑 |
| `backend/agents`、`backend/schemas` | Agent 提示词和输出结构 |
| `backend/config`、`backend/config.py` | 模型及运行配置 |
| `backend/tests` | 自动化测试 |
| `backend/scripts`、`backend/tools` | 后端辅助脚本与工具 |
| `backend/data` | 元数据、项目记录和旧向量索引 |
| `backend/storage` | 上传原文件、解析正文和素材 |
| `backend/outputs` | 运行结果、失败草稿和审核产物 |
| `backend/resources`、`backend/samples` | 资源与样例 |
| `scripts` | 项目启动及辅助入口 |
| `archive/2026-10-27-organization` | 历史临时测试目录、调试包及归档清单 |

业务数据、原始文件、运行结果、密钥配置保留原位置，避免破坏已有路径引用。
历史测试目录不作为当前业务运行目录；归档中的文件可能记录旧绝对路径，仅供历史排查。
`archive/2026-10-27-organization/manifest.json` 记录每个归档项的原位置、新位置和移动状态。
未列入归档清单的目录未经本次归档。恢复时按清单逐项恢复，并先确认原位置不存在同名新文件。

## 当前功能边界

旧的假 embedding 已停用；语义检索目前明确报告不可用，结构化与关键词查询仍可使用。
失败草稿可保留查看，不代表审核通过。真实模型调用尚未在本轮修复中验收。
更详细的后端命令说明见 [backend/README.md](backend/README.md)。

## 人工检查与提交

先检查页面打开、现有项目和历史产物可查看、接口请求正常，再检查具体业务功能。
GitHub 推送须在人工检查确认后进行。本次整理没有提交或推送。
归档包仅留在本机，不应随代码上传；推送前还需逐项审查暂存文件，排除业务文档、密钥和运行数据。
