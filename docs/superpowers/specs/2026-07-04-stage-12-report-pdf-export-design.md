# Stage 12 Report PDF Export Design

## Goal

为本机单机部署的面试 Agent 增加 PDF 报告导出能力，让用户在报告生成完成后可以下载一份可留档、可分享的结构化面试报告。

## Context

当前系统已经支持：

- `GET /api/interviews/{session_id}/report` 返回结构化 `InterviewReport`
- `GET /api/interviews/{session_id}/report/progress` 返回报告生成进度
- `app/static/index.html` 和 `app/static/app.js` 已能在报告完成后渲染总分、五维分、亮点、逐题反馈和引用证据

当前仍缺少：

- 可下载的 PDF 交付物
- 前端复盘区的下载动作
- 对“报告未完成/生成失败时不能下载”的统一处理

本阶段仍以本机 `localhost` 单机部署、单用户使用为前提，不引入登录、用户归属或跨设备同步。

## Approaches

### Approach A: Browser/HTML 渲染后转 PDF

做法：

- 先生成 HTML 模板
- 使用 WeasyPrint、wkhtmltopdf 或浏览器无头渲染为 PDF

优点：

- 视觉上最容易和网页复盘页保持一致
- CSS 控制力强

缺点：

- 需要额外系统依赖或浏览器引擎
- 本机环境差异大，测试稳定性差
- 对当前项目来说引入成本偏高

### Approach B: 服务端纯 Python 直接生成 PDF

做法：

- 新增 `app/services/report_pdf.py`
- 使用 `reportlab` 直接把 `InterviewReport` 渲染成 PDF 字节流

优点：

- 纯 Python，适合当前本机部署模式
- 依赖边界清晰，API 测试简单
- 可通过内建 CID 字体支持中文

缺点：

- 视觉样式不如 HTML 转 PDF 灵活
- 需要单独维护 PDF 布局代码

### Approach C: 先导出 Markdown/纯文本，再由用户自行转 PDF

优点：

- 实现最轻

缺点：

- 不满足“直接下载 PDF”的原型预期
- 用户体验最差

## Recommendation

推荐 **Approach B: 服务端纯 Python 直接生成 PDF**。

原因：

- 最符合当前“本机单机部署”的约束，不依赖额外桌面组件或浏览器引擎
- 能在 FastAPI 路由中直接返回 `application/pdf`
- TDD 成本可控，路由和服务都能做稳定测试
- 后续如果需要更强视觉效果，仍可以把 `report_pdf.py` 替换成 HTML 渲染实现，而不改 API 契约

## Design

### 1. API Contract

新增接口：

```http
GET /api/interviews/{session_id}/report.pdf
```

响应语义：

- `200 OK`：返回 PDF 文件
- `404 Not Found`：`session_id` 不存在
- `409 Conflict`：会话未结束、报告仍在生成中，或报告生成失败

说明：

- 这里把“不可下载”统一建模为 `409`，比 `404` 更能表达资源存在但状态不允许下载
- 现有 JSON 报告接口 `GET /api/interviews/{session_id}/report` 对失败报告返回 `500`；PDF 下载接口返回 `409` 是有意的，因为它表达的是“当前报告状态不允许下载”，调用方不应把两个端点的状态码完全等价处理
- 错误响应继续使用 FastAPI 现有 `{"detail":"..."}` 结构

建议头：

```http
Content-Type: application/pdf
Content-Disposition: attachment; filename="interview-report-{session_id}.pdf"
```

### 2. Rendering Service

新增文件：

```text
app/services/report_pdf.py
```

职责：

- 接收 `InterviewReport`
- 生成 PDF 字节流
- 不关心 HTTP，不依赖 FastAPI

推荐公开函数：

```python
def build_report_pdf(report: InterviewReport) -> bytes:
    ...
```

内部结构建议：

- `_register_pdf_fonts()`：注册中文字体，优先使用 `STSong-Light`
- `_build_story(report)`：将报告转换为 platypus story
- `_dimension_table_rows(report)`：构造五维分表格，并使用与前端一致的中文维度标签
- `_feedback_blocks(report)`：构造逐题反馈与引用证据块

内容范围：

- 报告标题
- `session_id`
- 总分
- 五维分
- summary
- highlights
- 逐题反馈
- references
- fallback 状态提示（若 `is_fallback=True`）

### 3. Frontend Behavior

前端只在报告已完成后允许下载。

推荐行为：

- 在复盘区加入“下载 PDF”按钮
- `renderReport(report)` 时启用按钮
- `resetReport()`、`renderReportProcessing()`、`renderReportError()` 时禁用按钮
- 点击按钮后 `fetch('/api/interviews/${sessionId}/report.pdf')`
- 成功则通过 blob 触发浏览器下载
- `409` 或 `404` 时显示非破坏性错误提示，不清空已经渲染好的报告内容，也不把 JSON 当文件下载

### 4. Testing

测试分三层：

- `tests/test_report_pdf.py`
  - PDF bytes 以 `%PDF` 开头
  - completed report 能导出非空文档
  - fallback report 也能导出
- `tests/test_report_api.py`
  - unknown session -> `404`
  - active interview -> `409`
  - processing report -> `409`
  - failed report -> `409`
  - completed report -> `200` + `application/pdf`
- `tests/test_static_report_ui.py`
  - 页面存在下载按钮
  - JS 调用 `/report.pdf`
  - 报告完成前按钮禁用，完成后启用

## Out Of Scope

本阶段不做：

- 登录、用户隔离、跨设备同步
- 报告中心列表
- PDF 模板主题定制
- 页眉页脚品牌化
- 图表截图、雷达图嵌入
- 报告持久化到数据库

## Risks

- `reportlab` 是新增依赖，需要在本机环境安装
- 中文渲染必须明确使用 CID 字体，否则 PDF 可能出现乱码或方块字
- 如果后续要追求网页一致性，可能需要替换为 HTML-to-PDF 方案

## Acceptance

阶段完成后应满足：

- 结构化报告完成后可下载 PDF
- 未结束/生成中/失败的报告不能下载，并返回明确错误
- PDF 至少包含总分、五维分、summary、highlights、逐题反馈和引用证据
- 当前静态前端能直接触发下载
