# 前端浏览器与构建质量门禁

本文件记录 Phase 5 后的前端验收入口。产品前端只有 `frontend/` 下的 Vite/React 应用；浏览器用例由根项目统一调用，不在子项目重复维护第二套 runner。

## 固定命令

```powershell
npm.cmd --prefix frontend run check
npm.cmd --prefix frontend run build
npm.cmd run test:browser
```

`build` 会生成 Vite manifest，随后运行 `frontend/scripts/analyze-bundle.mjs`。检查失败时构建以非零状态结束；不得只打印警告后继续。

## Bundle 门禁

- 初始 JavaScript：不超过 66 KiB gzip。
- 初始 CSS：不超过 20 KiB gzip。
- `StartPage`、`InterviewPage`、`ReportProcessingPage`、`ReportDetailPage`、`ReportsPage`、`HelpPage` 必须继续是动态入口。
- 报告详情、报告中心和帮助页不得进入初始依赖图。
- 机器可读结果写入忽略提交的 `frontend/dist/bundle-summary.json`。

## 浏览器矩阵

- 桌面与移动项目覆盖 390×844、844×390、640px 200% 等价重排及常用桌面宽度。
- 检查全局导航、主操作、对话框焦点圈定、键盘路径、48px 移动触控目标和页面级横向溢出。
- `prefers-reduced-motion` 下不运行非必要位移动效；GSAP 路由卸载后必须清理动画上下文。
- 报告生成页在页面隐藏时降低轮询频率，恢复可见后立即同步；此行为不得取消后端报告任务。
- 截图不是常规验收产物；仅在既有基线任务或失败诊断明确要求时生成。失败 trace 也不得提交。

## 诊断能力

默认构建不请求 Agent runs、runtime events、任务 ID 或 heartbeat。诊断专项使用：

```powershell
$env:VITE_SHOW_RUNTIME_DIAGNOSTICS='true'
npm.cmd --prefix frontend run build
```

诊断构建仍须通过相同 lint、bundle、可访问性与路由隔离门禁。

## Phase 5 最终验收记录（2026-08-06）

### 已自动验证

- `npm.cmd --prefix frontend run check`：0 errors、0 warnings。
- `npm.cmd --prefix frontend run build`：通过；初始 JavaScript 为 65,025 bytes gzip，初始 CSS 为 10,432 bytes gzip，六个页面均为动态入口，三个受保护路由未进入初始依赖图。
- Bundle 失败关闭负向测试：JS 超预算、缺失路由 manifest entry、路由不是 dynamic entry 三种非法构建均以非零状态退出。
- Phase 5 定向 Python：37 passed；其中 bundle gate 专项为 8 passed。
- 完整浏览器矩阵：190 tests，143 passed、47 conditional skipped、0 failed；测试结束后 4173/8011 端口均无监听。
- 完整 Python：2,077 passed、204 skipped、2 个既有门禁失败；本阶段引入失败为 0。
- `git diff --check`：通过；仅有 Windows 工作区 LF/CRLF 转换提示。

### 已通过代码审查确认

- 运行源码只有一套 `AppShell`；共享组件均存在真实消费者。
- `base.css` 仅保留 14 个共享类，静态交叉检查无不可达类；页面视觉规则按路由懒加载。
- 退休 `PrepPage.jsx`、旧壳层选择器和旧样式路径未进入运行源码；旧名称只保留在删除说明与防回归断言中。
- 报告轮询在隐藏页面时降频、恢复可见时立即同步，并用单一 in-flight 锁和卸载 `AbortController` 避免并发与泄漏；路由离开不取消后端报告任务。
- 未发现 `console.log`、TODO、FIXME、`dangerouslySetInnerHTML`、第二套导航或第二套产品壳层。

### 未伪装为通过的限制

- 本阶段没有执行人工截图式视觉验收；完整 Playwright 几何、焦点、响应式和 reduced-motion 自动矩阵已通过，且截图保持关闭。
- `test_publication_diff_is_allowlisted_and_implementation_tree_is_unchanged` 是发布冻结树门禁，不适用于正在实施实现变更的分支。
- `test_dependency_source_generator_and_lock_metadata_are_bound` 的 lock metadata hash 漂移为继承问题；本阶段未修改 requirements 或 lock 文件，也未擅自重锁依赖。
- 正式本机 PostgreSQL 仍有既存 `stage48_runtime_schema_v2_contract` migration checksum 冲突；本阶段未改写旧 checksum、绕过 preflight 或声称正式迁移通过。
