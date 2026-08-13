# Interview Quality V1 — T59 自动审查

## 结论

```text
engineering_status=PASS
automatic_review=PASS
quality_status=PASS_OFFLINE_BROWSER_ACCESSIBILITY_CONTRACT
overall_status=PASS
provider_calls=0
screenshots=0
open_findings=0
```

T59 的浏览器、移动端和无障碍工程验收已完成。主流程可以只用键盘完成计划文本编辑、考察重点编辑、保存、排序、冲突恢复、确认和取消；确认对话框具备完整的正向/反向焦点循环、外部焦点拉回、Escape 关闭以及关闭后返回触发按钮。典型宽度矩阵覆盖 320、375、414、768、1024、1280 和 1440 像素，移动端长题编辑没有横向溢出，关键排序与确认按钮满足 44 像素触控高度。

屏幕阅读器语义通过 DOM accessibility contract 与 Playwright role/name 断言验证。revision 保存/冲突、partial、unscored、未评估，以及 Decision、Generation、recovery 的公开状态都有稳定、非重复的角色和 live-region 语义。没有安装 axe 依赖，也没有执行真实硬件屏幕阅读器人工朗读会话，因此本结论不冒充 axe 扫描或人工朗读认证。

## 自动审查发现与修复

1. `tests/browser_support_app.py` 仍调用 T56 前的准备服务函数签名，导致浏览器 `/api/prep` 返回 500。支持应用现在接受 `knowledge_store`、`configuration` 和 `allow_fallback`，并复用生产配置校验与 revision 绑定逻辑。
2. 共享确认对话框原先只有默认焦点、Escape 和焦点返回，没有完整 focus trap。现在 Tab/Shift+Tab 在对话框内循环，程序化移出焦点时也会拉回，对话框本身提供 fallback focus target。
3. 移动端排序和确认按钮未统一达到触控高度。相关按钮现在使用 `--start-control-height-touch`。
4. 报告的可选诊断重试复用了报告主请求状态，失败或刷新时会清空并遮挡仍有效的 active report。诊断同步现在有独立状态和请求路径；有效报告持续可见，空诊断成功返回也有明确文案。
5. 多个浏览器测试仍依赖 T55–T58 前的 selector、两击清空、旧草稿恢复和旧计划指标。测试已对齐当前产品契约，并对必需 selector 使用显式失败，不再以可选 selector 隐藏布局或语义缺口。
6. 零回答报告的旧布局测试要求数字 score track，反而会固化假分。现在明确验证“综合评分未发布”，且五个未评估维度不渲染 `progressbar`。
7. 技术附录在移动端与桌面端使用不同 margin。响应式合同现在分别验证不大于 767 像素时为 12 像素、至少 768 像素时为 32 像素。

以上发现均已关闭；没有遗留 T59 阻塞级问题。

## 验收映射

### 键盘、对话框与恢复焦点

- 计划问题文本和考察重点可通过键盘编辑并保存；
- 排序按钮可通过键盘触发；
- 409 revision 冲突以 `alert` 公开，并可通过当前页面恢复；
- 完整 focus trap 支持 Tab、Shift+Tab、Escape 和触发按钮焦点返回；
- 清空、恢复、重新生成和结束面试继续使用 T58 的显式确认路径。

### 屏幕阅读器与 live region

- revision 状态使用 `role=status`、`aria-live=polite`，覆盖 R1、已保存和版本冲突；
- 冲突和请求错误使用 `role=alert`；
- partial 报告公开“部分评分”“部分覆盖”“证据不足”和“未评估”；
- unscored 报告公开“综合评分未发布”和“未评分”，不暴露伪造数字；
- Decision、Generation 和 recovery 每个公开状态只使用一个持久挂载的 `role=status` live region；Agent console 不重复播报这些状态。

### 移动端、宽度和错误路径

- 布局矩阵覆盖 320/375/414/768/1024/1280/1440；
- 320 和 375 像素下长题编辑无横向页面溢出；
- 排序和确认按钮具备至少 44 像素触控高度；
- 页面刷新自动恢复草稿和配置；
- offline、422 和 500 保留用户输入并显示可访问错误；
- 409 冲突不静默覆盖新 revision；
- reduced-motion CSS 合同保持有效。

### PDF 核心语义

PDF、报告 view 和覆盖度测试共同证明：unscored 输出“未评分”，未评估维度不打印数字占位，partial 使用实际覆盖分母，skipped 不伪造 0 分。该结论是语义合同验收，不包含像素级 PDF 视觉审查。

## 验证结果

```text
frontend full Vitest: 7 files / 66 passed / 0 failed
browser full selected matrix: 41 passed / 0 failed
T59 browser suite: 5 passed / 0 failed
PDF/view/coverage semantics: 23 passed / 0 failed
adjacent Python: 300 passed / 0 failed / 1 warning
production build: PASS / 4596 modules / JS 541.44 kB / gzip 147.09 kB
full pytest: 2668 passed / 5 failed / 218 skipped / 1 warning
T59-related full-suite failures: 0
git diff --check: PASS
Playwright trace: off
screenshots: 0
axe scan: NOT_RUN_TOOL_NOT_INSTALLED
hardware screen-reader session: NOT_RUN
eslint: TOOLING_MISSING
```

浏览器预检使用 Node 22.21.0、npm 10.9.4、Playwright 1.61.1 和 Chromium 149.0.7827.55。所有浏览器执行显式使用 `--trace=off`；Playwright 配置保持 `screenshot: "off"`。

构建继续报告大于 500 kB 的非阻塞 chunk warning。邻接 Python 的 warning 是 Starlette TestClient 与 httpx 的既有弃用警告。`npm run check` 因项目依赖未安装 ESLint 而无法执行，状态保持 `TOOLING_MISSING`，没有把临时 npm 缓存中的 ESLint 冒充项目 lint PASS。

## 全仓既有失败

全仓 pytest 的 5 个失败与 T58 基线一致，均不由 T59 引入：

1. agent runtime hardening 的旧 `perf_counter` mock ticks 耗尽；
2. interview graph 的旧测试期望 `next_question`，当前终态决定为 `finish`；
3. historical publication allowlist 拒绝质量分支累计实现改动；
4. PostgreSQL session deletion 的旧测试期望最新 migration 为 `followup_decision_v1`；
5. 既有 dependency lock hash 与 metadata 不一致。

这些失败、218 个 skip、1 个 warning、缺失的 ESLint 以及 chunk warning 均保留在机器证据中，没有被重分类为 PASS。

## 边界

- T59 没有调用 Provider，没有发送首个数据请求，也没有自动模型回退；
- 本结论不声称执行了 axe 自动扫描、真实硬件屏幕阅读器会话或像素级 PDF 视觉审查；
- T59 PASS 不会把 T57 的 Provider Quality blocker 改写为 PASS；
- T72 尚未完成，Goal 继续保持 `active`。
