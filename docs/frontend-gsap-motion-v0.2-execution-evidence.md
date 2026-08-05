# Frontend GSAP Motion Optimization v0.2 — Execution Evidence

**执行日期：** 2026-08-03 至 2026-08-04  
**计划来源：** `C:\Users\admin\Downloads\2026-08-03-frontend-gsap-motion-optimization-v0.2-revised.md`  
**实现范围：** `frontend/`、现有 `tests/browser/` 契约测试  
**后端/API 变更：** 无  
**最终状态：** committed delivery 完成；条件 Task 5 按 Review Gate A 跳过

```text
PLAN_VERSION=0.2
CHECK_TOOLCHAIN=PASS
FRONTEND_BUILD=PASS
BROWSER_BASELINE=PASS
ROUTE_SPLIT=PASS
REPORT_PROCESSING_GSAP=PASS
PROGRESS_EVENT_ANIMATION=DEFERRED_BY_API_CONTRACT
REPORT_DETAIL_GSAP=NOT_JUSTIFIED
TASK_5=SKIPPED
FINAL_BROWSER_SUITE=PASS
```

## 1. Gate 0 — 可复现基线

### 1.1 环境

- Node：`v22.21.0`，满足根 `package.json` 的 `>=20 <23`。
- 浏览器支持后端：`F:\python3.11\python.exe`，Python 3.11.3。
- Playwright Chromium：按根锁文件中的 Playwright 版本安装。
- 根依赖：`npm ci` 成功。
- 前端依赖：`npm --prefix frontend ci` 成功，从锁文件重建 142 个包，0 vulnerabilities。

默认 Python 曾指向不支持现代类型注解的旧 Anaconda 运行时；按照计划要求，浏览器测试固定复用项目使用的 Python 3.11 解释器，没有修改测试行为。

### 1.2 ESLint 工具链

新增项目本地 flat ESLint 配置和依赖：

- `eslint`；
- `@eslint/js`；
- `globals`；
- `eslint-plugin-react-hooks`；
- `eslint-plugin-react-refresh`；
- `frontend/eslint.config.js`。

最终结果：

```text
npm --prefix frontend run check
exit code: 0
errors: 0
warnings: 4
```

4 个 warning 是计划范围外页面的既有 Hook dependency 提示：

- `InterviewPage.jsx`：2；
- `ReportDetailPage.jsx`：1；
- `ReportsPage.jsx`：1。

它们在 Gate 0 被记录为非阻塞既有基线，没有通过扩大本计划范围或关闭 Hook 规则来隐藏。

### 1.3 初始生产构建

路由拆分前的单体构建：

| 资产 | 原始大小 | gzip |
|---|---:|---:|
| `index-Ce0aiJSA.js` | 476,075 bytes | 127.74 KB |
| `index-eTjRV9Ae.css` | 263,650 bytes | 35.50 KB |
| JS source map | 1,475,732 bytes | — |

### 1.4 初始浏览器基线

安装匹配版本的 Chromium 并使用 Python 3.11 后：

```text
108 tests
70 passed
38 skipped
0 failed
```

skip 来自项目既有的浏览器项目分工或条件测试，不是失败。

## 2. Task 1 — 路由与 CSS 拆分

### 2.1 实现

- 将 `App.jsx` 中六个页面的静态导入改为 `React.lazy()` 动态导入。
- `/` 和 `/prep` 共享同一个 `StartPage` lazy loader。
- `NotFoundPage` 保持 eager。
- 增加一个最小 `Suspense` loading view。
- 新增真实 Error Boundary；动态 import 被拒绝时提供：
  - 安静的错误解释；
  - “重新载入”；
  - “返回准备阶段”。
- 页面 CSS 继续由各自页面模块导入，因此跟随页面 chunk 拆分。

### 2.2 生产构建结果

最终主资产：

| 资产 | 最终大小 | 相对原单体资产 |
|---|---:|---:|
| 主 JS `index-UUhf91wJ.js` | 202,695 bytes | -273,380 bytes / -57.4% |
| 主 CSS `index-DZKhroGe.css` | 123,777 bytes | -139,873 bytes / -53.1% |

主要路由资产：

| Route module | JS | CSS |
|---|---:|---:|
| `StartPage` | 49,539 bytes | 使用共享开始页 CSS |
| `InterviewPage` | 41,496 bytes | 31,597 bytes |
| `ReportProcessingPage` | 110,115 bytes | 19,768 bytes |
| `ReportDetailPage` | 46,245 bytes | 43,802 bytes |
| `ReportsPage` | 26,013 bytes | 27,641 bytes |
| `HelpPage` | 27,791 bytes | 15,465 bytes |

报告生成 chunk 包含 GSAP 与页面逻辑，因此体积高于其他页面；它不在主 JS 中，也不会由不使用动效的页面请求。

### 2.3 路由验收

在 production preview 下验证以下路径均直接返回应用入口，HTTP 200：

- `/`；
- `/prep`；
- `/interview`；
- `/report-processing`；
- `/report-detail`；
- `/reports`；
- `/help`；
- 未知路径。

新增浏览器契约模拟 Help route module 请求失败，验证错误边界的恢复说明和两个操作均可见。结果：1 passed / 1 project skipped / 0 failed。

路由拆分使部分旧几何测试在 lazy fallback 尚未退出时过早测量。共享 `expectGeometry()` 现在等待真实 `.start-app-root` 后再执行原有的严格几何断言，没有放宽内容长度、溢出、按钮或标题标准。

## 3. Task 2 — 限定 GSAP 基础层

新增依赖：

- `gsap`；
- `@gsap/react`。

新增基础文件：

- `frontend/src/motion/config.js`；
- `frontend/src/motion/gsap.js`；
- `frontend/src/hooks/useReducedMotion.js`。

约束：

- GSAP 不从 `main.jsx`、`App.jsx` 或共享 eager shell 导入；
- `useGSAP` 只在授权 lazy route 的 motion module 中注册；
- reduced-motion hook 使用 `useSyncExternalStore` 订阅运行时偏好变化，不只读取首次值；
- duration 与 `DESIGN.md` 对齐：160ms / 200ms / 280ms / 420ms；
- 仅使用 `power2.out`、`power1.in` 和 4px/8px 位移；
- 未安装 ScrollTrigger、Flip、MotionPath 或其他动画库。

## 4. Task 3 — `/report-processing` 连续状态模型

### 4.1 语义状态与显示状态

React 继续拥有权威进度快照；显示状态只暂存阶段标题、消息和 attempt identity。

实现行为：

- 首个成功快照立即显示；
- percent-only 更新不触发阶段 timeline；
- 同阶段消息更新只进行短透明度 handoff；
- 新阶段先退出旧文案，再同步提交新 display snapshot，再进入新文案；
- 快速阶段变化会取消旧 timeline，并以最新快照结束；
- attempt identity 优先使用 `report_job_id` 并包含 `attempt`；
- 新 attempt 的较低进度立即重置，不跨 attempt 反向插值；
- failed、orphaned、stalled、不可恢复 sync error 和 reduced motion 立即提交；
- unmount 会终止 timeline 和 callback。

### 4.2 进度数字与进度条

- `aria-valuenow` 和 `aria-valuetext` 始终直接来自权威 React 状态；
- 可见数字由稳定 DOM ref 显示，并设置为辅助性视觉值；
- React 不再用 `{percent}` 重写 tween 中的 `textContent`；
- 数字和 fill 从当前显示值 retarget 到最新值；
- 使用 `overwrite: "auto"` 和显式 kill；
- fill 只动画 `scaleX`，不动画 width；
- 单次更新不超过 200ms；
- completed 强制权威值与显示值为 100；
- reduced motion 会终止活动 tween 并同步设置最终数字与 fill。

### 4.3 React identity 和 CSS ownership

移除了仅用于重播动画的 dynamic keys：

- 当前阶段编号；
- 阶段标题；
- 阶段消息；
- 可见百分比；
- sync message；
- action guidance；
- runtime status copy。

移除了与新所有权冲突或不再真实需要的处理页动画：

- `processing-state-update`；
- `processing-value-update`；
- `processing-event-enter`；
- `processing-notice-enter`；
- `processing-status-icon`。

保留的 CSS motion：

- 当前 stage row 的一次性 240ms 状态反馈；
- stage anchor 的一次性 240ms 反馈；
- 报告完成后按钮内部图标的 200ms 解锁反馈；
- Spinner、focus、hover、颜色和边框状态。

审计确认，没有已变更元素同时由 CSS 和 GSAP 写入同一个 transform/opacity 属性。

### 4.4 事件账本

后端进度响应仍没有真实、稳定的事件 identity 合同。因此：

- 保留诚实的空事件说明；
- 没有创建随机、索引、时间或内容哈希 ID；
- 没有添加 mock event；
- 没有实现 event-row GSAP animation。

```text
PROGRESS_EVENT_ANIMATION=BLOCKED_BY_PROGRESS_EVENT_CONTRACT
```

## 5. Task 4 — 浏览器行为契约

新增/强化的契约覆盖：

1. 20 -> 35 -> 55 的连续 retarget；
2. ARIA 权威值立即更新；
3. 可见百分比节点 identity 稳定；
4. 数字更新过程中不回退；
5. fill 最终 transform 精确到 0.55；
6. unchanged snapshot 不重挂 stage copy；
7. retrieving -> analyzing -> evaluating 快速更新 latest-wins；
8. critical failure 立即显示 alert 与恢复操作；
9. 新 job/attempt 60 -> 10 只产生直接提交，不产生中间插值；
10. reduced motion 在 load 前和 mounted session 内切换时均立即设置最终值；
11. route unmount 后没有迟到 mutation 或 page error；
12. `/prep`、`/reports`、`/help` 不请求 GSAP module；
13. lazy route module rejection 显示可恢复错误界面。

报告生成专项结果：

```text
22 tests across projects
11 passed
11 skipped
0 failed
```

几何/页面失败集合修正后的定向回归：

```text
48 tests across projects
24 passed
24 skipped
0 failed
```

## 6. Review Gate A

Gate 结论：

```text
REPORT_DETAIL_GSAP=NOT_JUSTIFIED
TASK_5=SKIPPED
```

理由：

- 报告生成页的连续性契约已经通过，所有 state motion <= 280ms；
- GSAP 被隔离到 `ReportProcessingPage` lazy chunk；
- polling、requeue、attempt reset、critical state、reduced motion 和 unmount 生命周期均有通过的浏览器证据；
- 当前报告详情的 score、Observer、section navigation、download、disclosure、fallback 和 reduced-motion 基线均通过；
- 本轮没有发现一个只能通过迁移到 GSAP 才能解决的报告详情产品问题；
- 仅以“技术一致”为理由不满足 v0.2 的批准条件。

因此没有修改：

- `frontend/src/pages/ReportDetailPage.jsx`；
- `frontend/src/styles/report-detail-app.css`；
- `tests/browser/report-detail-ui.spec.js`。

## 7. 最终验证

### 7.1 Required commands

```text
npm ci                                      PASS
npm --prefix frontend ci                    PASS
npm --prefix frontend run check             PASS (0 errors, 4 recorded warnings)
npm --prefix frontend run build -- --manifest PASS
npm run test:browser -- --reporter=dot      PASS
git diff --check                            PASS
```

### 7.2 Full browser result

```text
122 tests
77 passed
45 skipped
0 failed
duration: approximately 4.0 minutes
```

### 7.3 Bundle/resource evidence

- 主 JS 从 476,075 bytes 降至 202,695 bytes。
- 主 CSS 从 263,650 bytes 降至 123,777 bytes。
- 六个页面均具有独立 JS chunk；五个 route-specific stylesheet 具有独立 CSS chunk。
- 构建产物中包含 GreenSock/GSAP 的唯一 JS owner 是 `ReportProcessingPage-C0esJLO_.js`。
- 浏览器测试确认 `/prep`、`/reports`、`/help` 的 resource graph 不包含 GSAP 请求。
- 没有 duplicate GSAP runtime。

## 8. 明确未变更和延期项

本轮没有修改：

- `app/`；
- progress API；
- 数据库、worker 和 report queue；
- `/interview` 实现和动效；
- `/prep`、`/reports`、`/help` 页面实现和动效；
- report-detail 实现和动效；
- `start-page.css`。

延期项：

- 真实 report progress event contract；
- interview focus-mode 技术方案；
- legacy `start-page.css` 删除决策；
- Review Gate A 未来在出现明确报告详情问题时重新评估。

## 9. 结论

v0.2 的 committed delivery 已完成：检查工具链可复现、路由/CSS 已拆分、GSAP 只存在于授权的报告生成 lazy route、进度和阶段状态具备可中断/可 retarget/可清理的显示生命周期，关键状态与 reduced motion 立即完成，完整浏览器套件通过。
