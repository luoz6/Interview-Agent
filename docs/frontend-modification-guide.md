# 前端修改文档：以四个 HTML 原型页替换旧单页入口

## 1. 目标

下一阶段前端不再保留 `app/static/index.html` 作为运行入口，而是按 `app` 目录下四个原型页拆成四个真实页面：

| 页面 | 原型文件 | 建议路由 | 职责 |
| --- | --- | --- | --- |
| 面试准备页 | `app/test4.html` | `/` 或 `/prep` | 输入 JD/简历、保存草稿、生成计划、开始面试 |
| 模拟面试页 | `app/test3.html` | `/interview?session_id=...` | 展示当前题、对话流、题目导航、答题、跳题、结束 |
| 报告生成页 | `app/test2.html` | `/report-processing?session_id=...` | 展示报告生成阶段、事件时间线、RAG 摘要、轮询完成状态 |
| 报告详情页 | `app/test1.html` | `/report-detail?session_id=...` | 展示总分、五维能力分、逐题反馈、证据引用、PDF 下载 |

`app/static/index.html` 应从运行路径中移除。`app/static` 可以继续存放共享 CSS、JS 和本地资源，但不再提供旧单页 HTML。

## 2. 当前不足

| 问题 | 影响 | 修改方向 |
| --- | --- | --- |
| 四个原型页目前是静态 HTML | 无法调用真实接口，展示数据会失真 | 为每页接入对应 API 和页面状态 |
| FastAPI `/` 当前仍服务旧单页 | 与“四页流程”目标冲突 | 改为返回 `app/test4.html` 或准备页模板 |
| 原型页包含后端没有的字段 | 容易生成假数据，如百分位、Worker 名称、站内通知 | 后端没有字段时隐藏，不在前端伪造 |
| 原型页使用 CDN 依赖 | 本机离线部署不稳定 | Tailwind、FontAwesome、Chart.js 需要本地化或改成本地 CSS 实现 |
| 报告原型是四维雷达，后端是五维评分 | 维度不一致 | 前端统一使用五维：知识广度、技术深度、系统设计、工程实践、表达沟通 |

## 3. 页面路由改造

在 FastAPI 静态页面层增加四个页面路由：

```text
GET /                           -> app/test4.html
GET /prep                       -> app/test4.html
GET /interview                  -> app/test3.html
GET /report-processing          -> app/test2.html
GET /report-detail              -> app/test1.html
```

页面之间通过 `session_id` 查询参数衔接：

```text
test4 开始面试成功
-> /interview?session_id={session_id}

test3 主动结束或答完所有题
-> /report-processing?session_id={session_id}

test2 检测报告完成
-> /report-detail?session_id={session_id}
```

## 4. JS 文件拆分建议

不要继续把所有逻辑塞回一个 `app/static/app.js`。建议拆分为：

| 文件 | 职责 |
| --- | --- |
| `app/static/api.js` | 统一封装 `getJson`、`postJson`、SSE 解析、PDF blob 下载 |
| `app/static/prep.js` | `test4.html` 页面逻辑 |
| `app/static/interview.js` | `test3.html` 页面逻辑 |
| `app/static/report-processing.js` | `test2.html` 页面逻辑 |
| `app/static/report-detail.js` | `test1.html` 页面逻辑 |
| `app/static/shared-ui.js` | 标签、状态徽标、错误提示、五维标签映射等共享渲染 |

共享维度映射必须固定为：

```js
const dimensionLabels = {
  breadth: "知识广度",
  depth: "技术深度",
  architecture: "系统设计",
  engineering: "工程实践",
  communication: "表达沟通",
};
```

## 5. 各页面接口接入

### 5.1 `app/test4.html` 面试准备页

接入接口：

| 操作 | 接口 | 行为 |
| --- | --- | --- |
| 页面加载恢复草稿 | `GET /api/interview-drafts/{draft_id}` | 从 `localStorage.interviewDraftId` 读取草稿 ID，成功后填回 JD/简历 |
| 保存草稿 | `POST /api/interview-drafts` | 保存 JD/简历和当前标签，成功后覆盖本地 `draft_id` |
| 生成题目计划 | `POST /api/prep` | 渲染 `title`、`questions`、`job_tags`，不创建会话 |
| 开始面试 | `POST /api/interviews` | 创建会话，成功后跳转 `/interview?session_id=...` |

实现要求：

| 要求 | 说明 |
| --- | --- |
| 不读取硬编码 demo 标签作为真实标签 | 页面加载时标签状态应为空，只有 `/api/prep` 或会话快照返回后才写入 |
| `job_tags` 是响应顶层字段 | 不要把它当成 `InterviewPlan` 模型字段 |
| 保存草稿使用 JS 状态 | 使用 `currentTags` 变量保存标签，不从 DOM 反向读取 |

### 5.2 `app/test3.html` 模拟面试页

接入接口：

| 操作 | 接口 | 行为 |
| --- | --- | --- |
| 页面加载 | `GET /api/interviews/{session_id}` | 渲染当前题、消息、题目状态、进度、标签 |
| 提交回答 | `POST /api/interviews/{session_id}/answer/stream` | 解析 SSE，实时渲染追问片段和最终状态 |
| 兼容非流式 | `POST /api/interviews/{session_id}/answer` | 可作为降级路径 |
| 下一题 / 跳过 | `POST /api/interviews/{session_id}/skip` | 成功后重新加载 session snapshot |
| 结束面试 | `POST /api/interviews/{session_id}/finish` | 成功后跳转报告生成页 |

实现要求：

| 要求 | 说明 |
| --- | --- |
| session snapshot 是状态源 | 答题、跳题、结束后都重新调用 `GET /api/interviews/{session_id}` |
| 题目状态使用后端枚举 | 支持 `current`、`answered`、`skipped`、`unanswered`、`pending` |
| 结束状态跳转 | `status=finished` 时进入 `/report-processing?session_id=...` |

### 5.3 `app/test2.html` 报告生成页

接入接口：

| 操作 | 接口 | 行为 |
| --- | --- | --- |
| 页面加载 | `GET /api/interviews/{session_id}` | 展示会话标题、题目数量、标签 |
| 进度轮询 | `GET /api/interviews/{session_id}/report/progress` | 渲染阶段、百分比、事件、RAG 摘要、`report_job_id` |
| 完成检测 | `GET /api/interviews/{session_id}/report` | 返回 `200` 时跳转报告详情页，返回 `202` 时继续轮询 |

轮询策略：

```text
每 3 秒调用 /report/progress 更新 UI
随后调用 /report 判断是否完成
完成 -> /report-detail?session_id=...
失败 -> 展示失败状态，不清空页面历史进度
```

不要在同一轮轮询里用 `/report/progress` 和 `/report` 的不同 payload 重复渲染同一个进度组件。`/report/progress` 负责 UI，`/report` 只负责完成判断。

### 5.4 `app/test1.html` 报告详情页

接入接口：

| 操作 | 接口 | 行为 |
| --- | --- | --- |
| 页面加载报告 | `GET /api/interviews/{session_id}/report` | 渲染完整 `InterviewReport` |
| 下载 PDF | `GET /api/interviews/{session_id}/report.pdf` | 使用 blob 下载，不直接 `window.location.href` 跳转 |
| 返回报告中心 | `GET /api/reports` | 当前可先做入口按钮，报告中心页面可后续单独设计 |

实现要求：

| 要求 | 说明 |
| --- | --- |
| 使用后端五维评分 | 不使用原型里的四维雷达字段 |
| 不展示虚构百分位 | 后端没有百分位、候选人排名、分享链接时隐藏对应区域 |
| PDF 下载错误不破坏报告视图 | 失败时只显示局部提示，不清空已渲染报告 |

## 6. 样式与资源策略

四个原型页使用浅色卡片风格，当前旧运行页是暗色工作台。下一阶段建议统一采用四个原型页的浅色方向，因为报告阅读和长时间面试更适合高可读性背景。

| 资源 | 处理建议 |
| --- | --- |
| Tailwind CDN | 不作为本机部署依赖；将需要的样式沉淀到本地 CSS |
| FontAwesome CDN | 替换为文本图标、本地 SVG 或纯 CSS 图形 |
| Chart.js CDN | 初期用五维条形图替代；如要雷达图，后续本地化依赖 |
| 共享 CSS | 可以新增 `app/static/prototype.css`，四页共用 |

## 7. 测试计划

| 测试 | 目标 |
| --- | --- |
| 页面路由测试 | `/`、`/prep`、`/interview`、`/report-processing`、`/report-detail` 返回对应 HTML |
| 静态引用测试 | 页面不再引用 `app/static/index.html`，也不出现旧 GPT-4o 硬编码文案 |
| JS 语法测试 | `node --check app/static/*.js` 通过 |
| 准备页集成测试 | mock `/api/prep` 后能渲染 `job_tags` 和题目计划 |
| 面试页集成测试 | mock session snapshot 后能渲染题目状态和消息 |
| 报告生成页集成测试 | mock `/report/progress` 后能渲染阶段、事件和 RAG 摘要 |
| 报告详情页集成测试 | mock `/report` 后能渲染总分、五维分、反馈和引用 |

## 8. 实施顺序

1. 新增四个页面路由，让 `/` 指向 `app/test4.html`，停止服务旧 `app/static/index.html`。
2. 为四个 HTML 添加稳定 DOM id 和本地 JS/CSS 引用。
3. 抽出 `api.js`、`shared-ui.js`，统一错误处理、下载、维度映射和状态标签。
4. 接入 `test4.html` 的草稿、计划生成和开始面试。
5. 接入 `test3.html` 的 session snapshot、流式回答、跳题和结束。
6. 接入 `test2.html` 的报告进度轮询和完成跳转。
7. 接入 `test1.html` 的报告详情和 PDF 下载。
8. 删除或停用 `app/static/index.html` 相关测试和路由断言。

## 9. 验收标准

| 编号 | 标准 |
| --- | --- |
| F1 | `app/static/index.html` 不再是任何运行路由的返回页面 |
| F2 | 四个原型页都能通过后端路由直接访问 |
| F3 | 从准备页开始能完整走通：生成计划 -> 开始面试 -> 答题/跳题/结束 -> 报告生成 -> 报告详情 -> PDF 下载 |
| F4 | 页面不显示后端没有返回的百分位、Worker 名称、站内通知、分享链接 |
| F5 | 报告详情页展示五维中文标签，而不是英文字段名或四维原型字段 |
| F6 | 全量后端测试通过，新增页面路由和静态检查测试通过 |

## 10. 补充建议

报告中心可以后置。当前四个原型页已经覆盖一次面试闭环，先不要把报告中心、知识库管理、登录体系混入这一阶段。

知识库管理 UI 应作为下一阶段单独做，因为它需要上传、切分、embedding、索引、删除、检索预览等后端能力，不只是前端页面问题。
