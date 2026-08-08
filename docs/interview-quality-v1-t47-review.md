# Interview Quality V1 — T47 自动审查

## 结论

T47 Engineering 为 `PASS`。候选人报告的一级信息架构已经固定为六段：本轮结论与评分状态、覆盖度和限制、主要优势、Top 1–3 改进动作、逐题证据与回答建议、评估限制。候选人无需展开技术附录即可理解报告并开始下一轮练习。

Agent 执行、运行事件、检索路径、job/Artifact/revision、reason codes 和公开诊断现在统一位于默认折叠的技术附录。页面不会再把运行轨迹作为候选人一级导航，也不会让工程诊断压过改进动作。

## 实现范围

### 六段候选人一级内容

`frontend/src/pages/ReportDetailPage.jsx` 的一级导航和正文顺序均固定为：

1. 本轮结论与评分状态；
2. 覆盖度和限制；
3. 主要优势；
4. Top 1–3 改进动作；
5. 逐题证据与回答建议；
6. 评估限制。

评分状态和覆盖状态在第二段成对相邻显示。`partial` 明确显示已评估分子/总分母；`unscored` 显示“未评分 / 无有效覆盖”，不显示数字总分，也不把未评估维度填为 0。

### 可执行动作到逐题证据

优先动作优先消费 v2 `priority_actions`，最多展示三项，并显示：

- 为什么重要；
- 怎么练；
- 完成标准；
- 对应证据题入口。

点击“查看对应题目”会打开目标题目的 `<details>`，平滑定位并把键盘焦点移到题目 summary。目标题由 `question_refs` 决定；缺少直接 question ref 时，才通过 `evidence_refs -> question_id` 的冻结映射解析。

### 技术诊断降为二级内容

默认折叠的技术附录包含：

- 当前报告版本、生成时间、Artifact ID、来源 job、Schema 和 Rubric；
- 历史 revision 的版本号、时间和 active 标记；
- generation/score/limitation reason codes；
- 逐题评审账本和检索路径；
- Agent 执行和运行事件；
- 可公开知识引用清单。

提示词、密钥、绝对路径、候选人完整原文和 Provider 原始错误继续禁止展示。

### active Artifact 和历史版本语义

`frontend/src/reportContract.js` 新增 `reportDetailData()`：

- 同时支持 artifact-first wrapper 和 legacy 裸报告；
- 以 immutable Artifact 的状态轴和身份字段覆盖 payload 中可能陈旧的同名值；
- 保留 payload 中的 summary、strengths、priority actions、limitations 和 feedbacks；
- 独立派生 latest job 的 failed/updating 状态；
- 不因新 job 失败或运行中而移除 active Artifact。

页面顶部显示“第 N 版 + 生成时间”。当新版本失败时，显示“新版本处理失败，当前版本仍可使用”，并继续呈现旧 active 报告；新版本运行中时同样继续显示当前 active 版本。

## Plan 条款映射

| T47 条款 | 当前证据 |
|---|---|
| 一级内容固定为六段 | DOM 页面测试逐一验证六个一级 heading，且均不位于 `<details>` 内 |
| Agent/事件/检索/job/Artifact/revision/reason codes 二级折叠 | 全部收纳在默认关闭的“技术附录” `<details>` |
| 无需技术附录即可理解 | 六段正文独立展示结论、状态、优势、动作、逐题证据和限制 |
| action 跳到证据题 | action 使用 question/evidence ref 映射；点击后打开目标题并聚焦 summary |
| coverage 与 score 状态相邻 | 同一 `report-detail-state-pair` 中并列显示，且总览同步摘要状态 |
| 历史 revision 时间/版本清楚 | 顶部显示当前版号与时间；附录列出全部历史 revision |
| 新版本失败不遮挡旧 active | artifact wrapper 投影保留 active payload并显示 failed-update notice |

## 自动审查发现和修复

### 1. 页面未适配 artifact-first API wrapper

旧页面把 `/report` 响应直接当作 `InterviewReport`，但 artifact-first 模式返回 `{active_artifact, latest_job}`。修复后统一经 `reportDetailData()` 投影，且 Artifact 顶层状态轴优先于 payload 中的同名值，避免显示陈旧分数或状态。

### 2. 工程诊断仍占据一级导航

旧导航为“总览 / 逐题 / 改进 / 证据 / 轨迹”，逐题评审链路和运行轨迹直接出现在正文。修复后一级导航严格对应六段候选人内容，所有技术字段进入默认折叠附录。

### 3. 改进项是静态列表，无法回到证据

改进动作现使用结构化 `priority_actions`，并通过 question/evidence refs 定位逐题反馈。自动测试验证第二题初始折叠时，点击动作可以打开并聚焦第二题。

### 4. 历史版本提示和失败更新语义不清楚

页面新增当前 revision 和时间提示、折叠历史列表，以及 failed/updating job notice。失败更新不会清空或替换 active 报告。

### 5. 页面级 unscored 语义缺少回归

新增组件测试将 Artifact 切换为 `unscored + coverage none`，验证综合评分的无数字 aria-label、“证据不足，未发布数字”和全部状态文案。

## 视觉与可访问性

在原有研究型编辑工作台视觉语言上增加双状态块、优势网格、优先动作卡和克制的技术附录，不引入图片或截图资产。六个一级入口在移动端形成六列可滚动导航；动作和优势在窄屏降为单列。所有关键章节有可访问 heading，技术附录使用原生 `<details>/<summary>`，动作跳转后恢复键盘焦点，并继续支持 `prefers-reduced-motion`。

## 验证结果

```text
ReportDetailPage focused: 4 passed
report contract focused: 9 passed
frontend full Vitest: 26 passed
frontend production build: PASS
diff check: PASS
secret scan: PASS
provider_calls: 0
```

`npm run check` 未计为 PASS：仓库现有 script 调用 `eslint .`，但 package 中没有安装 ESLint，因此命令在执行任何 lint 前即返回 “eslint is not recognized”。T47 没有修改依赖或冒充 lint 结果；JSX 语法、模块解析和生产 bundle 由 Vite build 验证。该既有工具链缺口不影响本阶段功能验收，也不导致 Plan 执行中断。
