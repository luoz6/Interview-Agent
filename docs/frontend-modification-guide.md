# 前端实现指南：独立 Vite/React 服务

## 1. 当前架构

项目的产品前端是 `frontend/` 下的独立 Vite/React 应用。FastAPI 不再返回产品 HTML，也不再挂载 `app/static`；后端只提供 API、SSE 与报告文件下载。

```text
浏览器
  -> Vite/React frontend     http://127.0.0.1:5173
       -> /api/*             FastAPI http://127.0.0.1:8000
       -> /test-support/*    仅浏览器测试环境

报告 worker                 独立后台进程
```

开发环境由 Vite 代理 `/api` 到 FastAPI，因此浏览器保持同源请求体验。跨域部署时可通过 `VITE_API_BASE_URL` 指定 API 地址，并通过后端 `FRONTEND_ORIGINS` 设置允许的前端来源。

`app/test0.html` 至 `app/test4.html`、`app/test-help.html` 已经删除并退休，不能恢复为第二套产品前端。`app/static/*.js` 仅作为仍受测试约束的兼容性源码，不是当前页面设计、运行入口或部署契约。新增和修改产品功能应落在 `frontend/src/`。

## 2. 启动与构建

安装依赖：

```powershell
npm.cmd install
npm.cmd --prefix frontend install
```

启动 FastAPI：

```powershell
& 'F:\python3.11\python.exe' -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

启动独立前端：

```powershell
npm.cmd run dev:frontend
```

前端访问地址：`http://127.0.0.1:5173/prep`。后端健康检查：`http://127.0.0.1:8000/api/health`。

需要处理异步报告任务时，另开终端启动 worker：

```powershell
& 'F:\python3.11\python.exe' -m app.services.report_worker
```

生产构建：

```powershell
npm.cmd run build:frontend
```

构建产物位于 `frontend/dist/`。`build:prototype-css` 仅作为兼容命令别名保留，当前同样执行 Vite 构建，不再编译旧 HTML 的 Tailwind 样式。

## 3. 路由与页面组件

`frontend/src/App.jsx` 根据 `window.location.pathname` 选择页面组件。当前不引入 React Router，避免为固定的六页本地工作流增加额外依赖。

| 路由 | React 页面 | 主要职责 |
| --- | --- | --- |
| `/`、`/prep` | `StartPage.jsx` | JD/简历输入、文本导入、草稿恢复、生成计划、启动面试 |
| `/interview` | `InterviewPage.jsx` | 会话快照、题目导航、SSE 答题、跳题、结束、断线恢复 |
| `/report-processing` | `ReportProcessingPage.jsx` | 权威进度快照、阶段、公开消息、失败恢复与完成跳转；诊断构建可按需显示 RAG/运行字段，不展示事件历史 |
| `/report-detail` | `ReportDetailPage.jsx` | 总分、五维评分、逐题反馈、证据、评估链路、PDF |
| `/reports` | `ReportsPage.jsx` | 服务端搜索、筛选、分页、状态统计、重试与下载 |
| `/help` | `HelpPage.jsx` | 工作流说明、草稿/SSE/报告失败恢复指南 |

> Phase 5 运行路径审计（2026-08-05）：`frontend/src/App.jsx` 的 `/` 与 `/prep` 只懒加载 `StartPage.jsx`。旧 `PrepPage.jsx` 和 `styles/start-page.css` 已在证明无正式路由、import 或有效测试依赖后删除。

除准备页外，工作流页面通过 `session_id` 查询参数关联会话：

```text
/prep
  -> /interview?session_id={session_id}
  -> /report-processing?session_id={session_id}
  -> /report-detail?session_id={session_id}
```

未知路由由 `NotFoundPage.jsx` 显示明确的返回入口。

## 4. DESIGN.md 视觉契约

所有视觉和交互实现以仓库根目录的 `DESIGN.md` 为准。CSS 分为唯一的 `tokens.css`、`base.css`、`styles/components/` 共享层和 `styles/pages/` 路由层；共享层中的 `app-shell.css`、`navigation.css`、`dialog.css` 与 `async-state.css` 由主入口加载，页面 CSS 由对应懒加载页面导入，不得重新放回主入口。

### 4.1 Research Canvas

适用于准备页、报告中心、报告详情和帮助页：

- 暖石色背景、深墨色正文和克制的边框；
- 编辑式信息层级与大面积留白；
- 黑色主 CTA，蓝色只表达链接、焦点或交互状态；
- 珊瑚色只用于需要强调的少量状态；
- 长中文内容保持舒适行高、合理行宽和清晰分段。

### 4.2 Agent Workspace

适用于实时面试页：

- 浅色应用外壳包围深海军蓝工作台；
- 当前题、对话流、题目导航和操作区形成明确的实时工作层级；
- 流式生成、恢复、冲突和审核状态必须真实反映服务端状态；
- 专注模式可通过 Escape 退出，不能破坏键盘操作和草稿恢复。

### 4.3 Pipeline Field

适用于报告生成页：

- 深企业绿背景构成独立的流水线场域；
- 当前阶段使用珊瑚色标记，完成与待处理阶段保持清楚区分；
- 百分比、阶段、事件、RAG 与运行元数据均来自真实接口；
- 完成、失败、空数据与暂不可用状态必须具有不同反馈。

## 5. 设计变量与组件约束

- 颜色、间距、圆角、边框、阴影和动效统一使用 CSS 变量。
- 卡片不应无差别堆叠；先用版式、留白和分隔线建立信息结构。
- 主操作使用黑色实心按钮；蓝色用于交互状态；危险操作使用清晰但克制的危险色。
- 控件必须提供 hover、focus-visible、disabled、busy 和 error 状态。
- 移动端关键控件触达尺寸不小于 44px。
- 页面必须支持 `prefers-reduced-motion`，禁用非必要动画和位移。
- 不使用 CDN 字体、图标或图表依赖；保持本地/系统字体栈与可离线运行。
- 不使用 `dangerouslySetInnerHTML` 渲染后端内容。

## 6. API 与状态实现

共享请求逻辑位于 `frontend/src/api/client.js`。页面不得内置伪造业务结果；所有计划、会话、报告、证据和统计必须来自真实 API。

### 6.1 准备页

- `POST /api/prep` 创建带固定 TTL、稳定题目 ID 与版本号的权威 `PrepPlan`。
- `GET/PATCH /api/prep-plans/{plan_id}` 读取或修改当前计划；单题重生成使用计划版本冲突保护。
- `POST /api/interview-drafts` 保存匿名草稿；响应中的 `durability` 与 `expires_at` 决定页面如何描述持久性，删除操作使用真实 DELETE 接口。
- `POST /api/interviews` 携带 `plan_id`、`expected_plan_version` 与稳定 `command_id` 创建会话；相同命令重放返回同一会话。
- 文件导入仅支持 `.txt` 与 `.md`，单文件不超过 1 MiB。
- 证据标识以 `data-evidence-id` 保留，便于后续验证连续性。

### 6.2 面试页与 SSE

- `GET /api/interviews/{session_id}` 是刷新和恢复时的权威快照。
- `POST /api/interviews/{session_id}/answer/stream` 返回 SSE。
- 客户端携带命令 ID 与期望版本，处理重复提交和版本冲突。
- SSE 必须以终止事件结束；提前 EOF 时保留最后事件 ID 并从该游标恢复。
- 重连发送 `Last-Event-ID`，处理 generation reset、reconnect、done、error 与 conflict。
- 页面刷新时读取服务端 `active_stream_url` 恢复仍在运行的生成任务。
- 跳题和结束面试均调用真实写接口，不在前端自行推进权威状态。

### 6.3 报告生成与详情

- 生成页自适应轮询 `/report/progress`；页面隐藏时降到至少 15 秒一次，恢复可见时立即同步，离开页面不会停止后端任务。
- 报告完成后进入详情页；失败时保留错误与历史阶段，不清空上下文。
- 详情页默认只展示产品信息和服务端可靠性摘要；仅当 `VITE_SHOW_RUNTIME_DIAGNOSTICS=true` 时请求并展示允许公开的诊断资源。
- 五维评分固定为知识广度、技术深度、系统设计、工程实践、表达沟通。
- 不生成后端没有提供的百分位、排名、Worker 名称或演示统计。
- PDF 通过 blob 下载；下载失败只显示局部错误，不移除已渲染报告。
- 针对性练习通过 `POST /api/interviews/{session_id}/practice-plan` 创建新的可编辑 PrepPlan，并保留报告与会话题目的双 ID 来源。

### 6.4 报告中心

- 查询、状态、日期、偏移量和页大小由 `/api/reports` 服务端处理。
- 状态统计读取 `status_totals`，不通过拉取全部记录在浏览器中推算。
- 失败任务调用真实 requeue 接口；处理中和完成记录分别进入进度页和详情页。

## 7. 可访问性与响应式

- 每页必须有唯一标题、meta description、主内容 landmark 和跳过导航链接。
- 导航使用 `aria-current`，流程步骤使用当前/完成/待处理的语义状态。
- 输入框有可访问名称；通知、加载和错误状态可被辅助技术识别。
- 所有核心流程可使用键盘完成，焦点样式不可被移除。
- 桌面、平板和手机宽度均不得产生页面级横向溢出。
- 表格和高密度详情在窄屏中使用可读的重排或受控滚动。

## 8. 测试契约

执行顺序：

```powershell
npm.cmd run build:frontend
npm.cmd run analyze:bundle
& 'F:\python3.11\python.exe' -m pytest -q
$env:STAGE41_PYTHON='F:\python3.11\python.exe'
npm.cmd run test:browser
```

测试必须覆盖：

- FastAPI 保持 API-only，产品路由不由后端返回 HTML；
- 六个 React 路由存在并能渲染非空内容；
- 准备、SSE 面试、报告轮询、报告详情、PDF 和失败重试走真实接口契约；
- `Last-Event-ID`、刷新恢复、generation reset、幂等命令与版本冲突；
- 桌面和移动端边界、键盘可用性、减少动态效果；
- UTF-8 中文文案不出现乱码；
- 构建产物不依赖六个旧 HTML 文件。
- `dist/bundle-summary.json` 存在、六个路由仍是动态入口、初始 JS/CSS 未超过 66/20 KiB gzip 预算。

浏览器测试禁止以截图作为常规产物。Playwright 仅在失败时保留 trace，用于诊断而不是作为设计依据。

## 9. 验收标准

| 编号 | 标准 |
| --- | --- |
| F1 | `frontend/` 可独立安装、开发运行和生产构建 |
| F2 | FastAPI 根路径返回 API 边界信息，不服务产品 HTML 或静态资源 |
| F3 | 六个产品路由全部由 React 页面承载并符合 `DESIGN.md` 的对应环境 |
| F4 | 准备、面试、报告生成、详情、报告中心形成真实 API 闭环 |
| F5 | SSE 支持持久游标、断线恢复、刷新恢复、版本冲突和 generation reset |
| F6 | 页面包含加载、空、错误、失败、完成和降级状态，不用演示数据替代 |
| F7 | 桌面/移动端布局、键盘、ARIA、焦点和 reduced-motion 验收通过 |
| F8 | 前端构建、全量 Python 测试和浏览器测试全部通过 |
| F9 | README、运行手册、接口文档和本指南均指向 Vite/React 架构 |

## 10. 修改边界

后续前端开发应优先修改：

```text
frontend/src/pages/
frontend/src/components/
frontend/src/api/
frontend/src/hooks/
frontend/src/styles/base.css
frontend/src/styles/components/
frontend/src/styles/pages/
```

除非任务明确要求清理历史兼容资产，否则不要把 `app/test*.html` 或 `app/static/*.js` 重新接回运行路径，也不要据此还原页面设计。
