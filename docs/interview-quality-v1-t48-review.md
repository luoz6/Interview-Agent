# Interview Quality V1 — T48 自动审查

## 结论

T48 Engineering 为 `PASS`。Web、PDF 和历史 report revision 现在消费相同的 summary、priority actions、evidence、逐题反馈和 limitations 字段；Artifact PDF 绑定具体 `report_id + revision + created_at`，历史版本下载不再跟随 active pointer 漂移。

`unscored` PDF 不打印数字总分或五维数字；`partial` 明确打印已评估分子/总分母。技术附录固定放在 PDF 末尾。PDF 导出是只读操作，渲染失败不会修改 Artifact、job 或 active head。

## 实现范围

### Immutable Artifact PDF 身份

`build_report_pdf()` 新增可选的 Artifact 身份参数：

```text
report_id
revision
created_at
```

具体 Artifact 下载端点 `/api/reports/{report_id}.pdf` 将这三个字段写入 PDF 首页、文档 metadata 和末尾技术附录。文件名固定为 `interview-report-r{revision}-{report_id前8位}.pdf`。

session 兼容端点 `/api/interviews/{session_id}/report.pdf` 在 artifact-first 模式下先解析当时的 active Artifact，再调用同一 Artifact PDF renderer；因此即使从旧入口下载，PDF 本身也明确绑定实际 report ID 和 revision。没有可用 Artifact 的 legacy 会话继续使用原兼容路径，不伪造历史身份。

### Web 和 PDF 核心语义一致

PDF story 按与 T47 Web 页面相同的候选人顺序输出：

1. 本轮结论与评分状态：直接使用 `report.summary`；
2. 覆盖度和限制：使用 score/coverage/generation 三条正交状态轴和覆盖分母；
3. 主要优势：优先使用 `report.strengths`，legacy 才回退到 highlights；
4. Top 1-3 改进动作：逐项使用 `title/why_it_matters/practice/completion_criteria/question_refs/evidence_refs`；
5. 逐题证据与回答建议：使用 feedback rationale、critique、better_answer 和 references，并追加结构化 `report.evidence_refs`；
6. 评估限制：逐项使用 `report.limitations[].text`。

PDF 不再从第一条 critique 临时拼接 action，也不再只输出 highlights 而遗漏结构化 v2 字段。

### score/coverage/generation 状态

- `scored`：显示数字总分和五维有效数字；
- `partial`：显示 `总分/100（部分评分 numerator/denominator）`；
- `unscored`：总分显示“未评分”，五维只显示“证据不足/未评估”，不输出数字占位；
- `generation_status=degraded`：显示“降级生成”，不把 generation 状态替代 score 状态；
- coverage 显示 complete/partial/none 对应的候选人文案和实际分母。

### 末尾技术附录

`PageBreak` 后的最后一节固定为“技术附录”，包含：

- report ID 和 revision；
- report schema 和 scoring rubric；
- report path；
- generation/score reason codes；
- limitation reason codes；
- summary generation mode 和 prompt version。

技术附录不会改变候选人一级结论；动态文本统一 HTML escape 后交给 ReportLab，避免回答、证据或 action 中的标记被解释为 PDF 富文本指令。

### 历史版本下载

T47 的 revision 列表现在为每个版本提供独立下载按钮，直接调用 `/api/reports/{该版本report_id}.pdf`。当前 active 下载同样使用具体 report ID，而不是 session pointer URL。

API 回归先发布 revision 1，再发布并激活 revision 2；在 active pointer 指向 revision 2 后下载 revision 1，验证 renderer 收到的仍是 revision 1 payload、ID 和版本号。session 兼容下载则绑定 revision 2。

## Plan 条款映射

| T48 条款 | 当前证据 |
|---|---|
| PDF 绑定具体 report ID/revision | Artifact route、PDF 首页、metadata、附录和文件名全部包含 immutable 身份 |
| 显示 score/coverage/generation 和限制 | PDF story 使用四条 Artifact/Report 状态字段并输出 limitations |
| unscored 不打印数字总分或五维数字 | story/table 回归验证“未评分/未评估”，不含 None 或数字占位 |
| partial 显示分子/分母 | 回归验证 `81/100（部分评分 1/2）` |
| 技术附录置于末尾 | story 顺序检查 + pdfplumber 最后一页文本检查 |
| Web/PDF action、summary、evidence 同字段 | PDF 直接消费与 Web 相同的结构化模型字段，测试逐字段比较原值 |
| 历史下载不受 active pointer 变化影响 | revision 2 激活后请求 revision 1 URL，renderer 仍收到 revision 1 |
| 导出失败不改变 Artifact | 合成 renderer failure 后 Artifact、job list、active head 深比较完全相等 |

## 自动审查发现和修复

### 1. 旧 PDF 丢失 v2 结构化语义

旧 renderer 主要输出 summary、highlights、feedback critique 和 better answer，没有 priority actions、limitations、structured evidence 或技术附录。修复后核心 Web/PDF 字段一一对应，并冻结顺序。

### 2. 报告详情页下载跟随 session pointer

旧页面始终调用 `/interviews/{session_id}/report.pdf`，历史 revision 无法证明下载目标。修复后 active 和历史按钮均调用 immutable report ID URL，文件名同时包含 revision 和 report ID 前缀。

### 3. session PDF 仍可能读取 legacy report record

artifact-first 模式下，session PDF route 现在优先读取 active Artifact 并复用具体 Artifact renderer，避免 Web 显示 v2 active、PDF 却导出 legacy record 的语义分叉。

### 4. 导出异常的持久化不变性缺少证明

新增故障注入测试让 PDF renderer 抛出异常，并在请求前后深比较 Artifact、report head 和 job history。结果全部不变，证明 export failure 不改变报告状态。

### 5. PDF 动态文本可被 ReportLab 当作 markup

所有 session、summary、action、feedback、evidence、limitation 和诊断值现在统一 escape，避免候选人内容或引用中的尖括号改变 PDF 结构。

## 验证结果

```text
PDF/API focused regression: 62 passed
full report regression with PostgreSQL 16: 409 passed
frontend focused report/PDF download: 15 passed
frontend full Vitest: 28 passed
frontend production build: PASS
compileall app/tests: PASS
PDF reopen with pypdf: PASS
PDF text extraction with pdfplumber: PASS
PDF visual PNG review: NOT RUN（执行约束禁止截图/图像工作）
diff check: PASS
secret scan: PASS
provider_calls: 0
```

非阻塞警告：FastAPI TestClient 仍发出既有 `StarletteDeprecationWarning`。PDF 技能建议 PNG 渲染做视觉 QA，但当前任务明确禁止未请求的截图和图像工作，因此本阶段只声明结构、身份、字段和文本语义 PASS，不声明经过了视觉像素审查。

## 真实性边界

- T48 没有 Provider 调用；
- T48 Engineering PASS 不等于 T49 独立人工盲审或 Gate 4 Quality PASS；
- 没有声称 PDF 已完成 PNG/像素级视觉检查；
- 没有声称 v2 优于 v1，或人工判断的经历编造 observed_count 为 0。
