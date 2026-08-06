# `/prep` Calm Cobalt Workbench 优化计划 v1.2（最终契约修订版）

> 日期：2026-08-06
> 状态：Implementation-ready after second code review
> 范围：React/Vite `/prep` 前端视觉与交互层
> 设计依据：仓库根目录 `DESIGN.md` 的 `/prep` Calm Cobalt Workbench override
> 审查方法：Hallmark audit + 代码与浏览器测试契约对照

## 0. v1.2 修订摘要

v1.1 已解决权威时长、Workbench 复用、AppShell API、计划组件范围、MobileNav 图标、基础响应式、notice ownership、spinner 视觉状态与遗漏测试等根本问题。v1.2 继续补齐开工前最后的契约缝隙：

1. 区分布局断点与 MobileNav 的 `900px` 共享断点，所有 `≤900px` sticky/fixed CTA 与 status bar 必须保留导航安全间距；
2. 固定 `keyword / completed / empty / degraded` 四态 Knowledge 映射，并明确公共 payload 不包含 `degraded_reason`；
3. 增加 Activity Rail、中央 Workspace 与 Inspector Tab 的状态转换表；
4. 定义 `/prep` 自有 `noticeAction` 重试模型与操作白名单，不修改共享 `StatusNotice` API；
5. 全文统一为 `<1180px` 不渲染并排入口；
6. 允许通过 `.start-prep-*` modifier 增加准备页专属响应式修正，但禁止重写裸 `.start-*` 基础样式；
7. 定义 `PrepStatusBar` 固定字段、移动端当前项和 success 替换规则；
8. 将所有新准备页结构统一到 `start-prep-*` 命名空间，并明确所有生产 `.plan-*` 必须迁移；
9. 将 delayed spinner 统一提升到 `StartPage` 顶层，明确 Topbar、草稿、CTA、状态栏和单题重生成消费者；
10. 锁定三步生命周期、编辑器描述、草稿工具位置和 focus 实现，不保留会改变 DOM 的“或”分支。

## 1. 执行摘要

本轮不能只做“缩短文本框、移动按钮、增加动画”的表面调整。代码审查确认，当前 `/prep` 实现与 `DESIGN.md` 已锁定的工作台结构存在系统性偏差。

设计文档要求桌面端具备：

- 固定应用顶栏；
- 紧凑活动轨；
- 中央文档编辑器；
- 右侧 Inspector；
- 底部状态栏；
- 默认一次只强调一份源文档；
- 唯一主操作始终可见；
- 页面本身不承担长距离叙事滚动，各 Pane 独立滚动。

当前实现则是：

- 居中的单块 `prep-stage`；
- 50/50 双编辑器；
- `textarea` 使用 `clamp(18rem, 43vh, 30rem)`；
- 页面内部整体纵向滚动；
- 没有活动轨；
- 没有 Inspector；
- 没有桌面状态栏；
- 主操作被两个高编辑器推到首屏之外。

现有浏览器测试还明确要求活动轨、Inspector 和状态栏数量为零：

- `tests/browser/prep-ui.spec.js`；
- `tests/browser/phase2-prep-plan.spec.js`。

这意味着测试已经把一个与 `DESIGN.md` 冲突的临时实现冻结成了契约。执行时必须同步迁移测试，不能只修改 CSS。

最终方向：保留全部业务逻辑、API、数据真实性和 Calm Cobalt token，把 `/prep` 从大型双栏表单恢复为真正的应用工作台。

## 2. 设计上下文与锁定决策

- Audience：准备技术面试的使用者，以及需要核对岗位、经历、题目与知识证据的面试操作者。
- Primary use case：输入岗位 JD 与候选人经历，生成并核对权威面试计划，然后开始面试。
- Tone：冷静、技术化、克制、可信。
- Genre：modern-minimal / utilitarian app。
- Macrostructure：Workbench。
- Theme：继续使用现有 Calm Cobalt。
- Enrichment：无；应用功能本身就是视觉主体。
- Motion：Pane entrance、content crossfade、functional loading 三种原语。
- Primary action：生成计划前为“生成并检查面试计划”，生成后为“确认版本并开始面试”。

## 3. 成功目标

### 3.1 首屏与布局

在 1280×900、1440×900、2048×1152 等桌面尺寸中：

- 顶栏、活动轨、中央工作区、Inspector、底部状态栏同时可见；
- 用户无需滚动即可看到当前唯一主操作；
- 页面根容器和浏览器 body 不产生叙事型纵向滚动；
- 编辑器、计划列表和 Inspector 内容在各自 Pane 内滚动；
- 当前活动文档拥有最高视觉层级；
- 首屏不再连续堆叠步骤条、恢复卡片、阶段 kicker、大标题、说明、统计块和两个超高空编辑器。

### 3.2 交互

- “岗位 JD / 候选人经历 / 并排查看”成为真正的文档切换模式；
- 默认只强调一份文档；
- 计划生成前 Inspector 显示诚实准备状态；
- 计划生成后 Inspector 显示计划摘要、知识状态和证据情况；
- 保存、恢复、删除、清空等工具不与主操作争夺视觉权重；
- 全局状态变化不推动工作区上下移动；
- 错误出现在最接近问题的位置；
- 键盘焦点清晰但不形成发光蓝框；
- 快速异步操作不会闪烁 spinner。

### 3.3 必须保持不变

- `POST /api/prep`；
- 草稿保存、900ms 自动保存、恢复与删除；
- `.txt` / `.md` 导入；
- PDF、Word 等不支持文件的诚实提示；
- `PrepPlan` 权威版本；
- 题目排序、重点、必考、排除、单题重新生成；
- Knowledge Agent 证据与降级状态；
- 版本冲突恢复；
- 会话启动幂等逻辑；
- `data-prep-state`；
- ARIA 标签和缺失字段焦点转移；
- 现有中文业务文案的事实含义；
- `prefers-reduced-motion`；
- Phosphor 图标体系。

## 4. 文件范围

### 4.1 预计修改

- `frontend/src/pages/StartPage.jsx`
- `frontend/src/components/PlanEditor.jsx`
- `frontend/src/components/PlanQuestionCard.jsx`
- `frontend/src/components/MobileNav.jsx`
- `frontend/src/styles/pages/prep.css`
- `frontend/src/styles/components/app-shell.css`（仅在复用现有 Workbench 骨架时需要做兼容性修正，不重新定义三栏 Pane）
- `frontend/src/styles/tokens.css`（仅在现有语义 token 不足时扩充）
- `tests/browser/prep-ui.spec.js`
- `tests/browser/phase2-prep-plan.spec.js`
- `tests/browser/reference-ui.spec.js`
- `tests/browser/phase1-safety.spec.js`
- `tests/browser/local-v1.spec.js`
- `tests/browser/reference-ui-geometry.js`
- `tests/test_react_frontend.py`
- `tests/test_frontend_phase5.py`

### 4.2 建议新增

- `frontend/src/components/PrepActivityRail.jsx`
- `frontend/src/components/PrepInspector.jsx`
- `frontend/src/components/PrepStatusBar.jsx`
- `frontend/src/hooks/useDelayedPending.js`

这些组件只接收状态和回调，不复制网络请求、草稿、计划或会话业务逻辑。业务状态继续由 `StartPage.jsx` 持有。

### 4.3 不删除、不引入

- 不删除生产文件、路由、页面、接口或测试目录；
- 不引入 UI 框架；
- 不引入新图标库；
- 不引入新动画依赖；
- 不用 GSAP 实现按钮、输入框、文档切换或提示；
- 不修改后端和 API schema；
- 不重写 React/Vite 架构；
- 不编辑 `frontend/dist`；
- 不恢复旧 HTML 前端。

## 5. Batch A：先锁定现有契约，再复用 Workbench 宏观结构

### Task A0：现有 Workbench 骨架复用审计（阻塞任务）

开始实施前必须先审计并记录以下现有样式，不得从零重新定义第二套 Workbench：

- `.start-app-shell`：`frontend/src/styles/components/app-shell.css:281`
- `.start-activity-rail`：`frontend/src/styles/components/app-shell.css:293`
- `.start-editor-workspace`：`frontend/src/styles/components/app-shell.css:354`
- `.start-inspector`：`frontend/src/styles/components/app-shell.css:864`
- `.start-status-bar`：`frontend/src/styles/components/app-shell.css:1420`
- 中等宽度 Inspector 下沉：`frontend/src/styles/components/app-shell.css:1531`
- 移动端单列、sticky rail、sticky CTA/status：`frontend/src/styles/components/app-shell.css:1610`
- reduced-motion：`frontend/src/styles/components/app-shell.css:1858`

同时复核已有 token：

- `--start-app-rail-width: 4.75rem`
- `--start-app-inspector-width: 22.5rem`
- `--start-app-status-height: 2rem`
- `--start-duration-fast: 160ms`
- `--start-duration-state: 200ms`
- `--start-duration-spinner: 1000ms`

这一步的产出不是第二套 Workbench CSS，而是一份复用决策：哪些现有 `.start-*` 选择器直接使用、哪些旧 `.prep-*` 规则必须删除或收窄。允许通过准备页 modifier 增加唯一必要的路由级断点修正：

```jsx
<main className="start-app-shell start-prep-app-shell">...</main>
```

`prep.css` 可以定义 `.start-prep-app-shell`、`.start-prep-*` 子组件及其响应式规则，但禁止重新定义裸 `.start-app-shell`、`.start-inspector`、`.start-status-bar` 的基础样式，禁止复制共享 Pane 的边界、阴影、overflow、focus 和 animation 契约。

### Task A1：复用现有三栏工作台 DOM 契约

重构 `StartPage.jsx` 的主要 DOM，使其直接挂载到已有 `.start-app-shell`：

```text
AppShell
├── Topbar
├── Prep workbench
│   ├── Activity rail
│   ├── Central workspace
│   └── Inspector
└── Status bar
```

不要扩展 `AppShell` 的公共 props。与 `InterviewPage` 的既有模式一致，在 `AppShell` 的 children 中直接渲染 `/prep` 所需的 `.start-app-shell` 和 `PrepStatusBar`，利用现有第三网格行与 `MobileNav` 布局契约。

桌面端直接复用现有 token：

```css
grid-template-columns:
  var(--start-app-rail-width)
  minmax(0, 1fr)
  var(--start-app-inspector-width);
```

要求：

- 中央区域使用 `min-width: 0`、`min-height: 0`、`overflow: hidden`；
- 各 Pane 自己负责滚动；
- 删除 `.prep-flow` 的居中 `90rem` 页面小岛逻辑；
- 不增加第二套近似宽度常量。

验收：

- 1280×900 下三栏同时可见；
- 中央工作区保留合理可编辑宽度；
- 2048×1152 下充分利用应用宽度；
- body 与页面根节点不横向溢出；
- 到达主操作不依赖整页纵向滚动。

### Task A2：新增 PrepActivityRail

提供三个 Pane：

- 资料；
- 蓝图；
- 证据。

状态规则：

- “资料”始终可用；
- “蓝图”在计划生成前只显示诚实空状态；
- “证据”在生成前显示不可用或诚实空状态；
- 生成成功后自动切换到“蓝图”；
- Knowledge 降级时“证据”仍可打开，但只能显示基于公开 `knowledge_status` 的诚实通用说明；公共 payload 不包含具体 `degraded_reason`。

可访问性语义必须先固定，不保留“二选一”：

- Activity Rail 是工作区 Pane 导航，使用真实 `<button>`，保留 `aria-pressed`、`aria-controls`、`data-state` 和明确的当前状态；
- Inspector 内部的“计划 / 证据 / 准备状态”必须使用完整 tab 语义：`role="tablist"`、`role="tab"`、`role="tabpanel"`、稳定 `id`、`aria-controls`、`aria-labelledby`、`aria-selected`；
- Inspector tab 切换后焦点留在当前 tab，不跳到页面顶部；
- 图标对辅助技术隐藏；
- 当前状态通过背景、文字、图标联合表达；
- 桌面点击目标不低于 40px，移动端不低于 44px。

视觉：

- 当前项用淡钴蓝表面、钴蓝文字和短指示线；
- 未选中项用透明或近白表面；
- 不使用大面积钴蓝填充；
- 不显示大段步骤解释。

### Task A3：锁定 Workbench 与 Inspector 状态机

不继续维护旧 `workspaceView` 与新 rail 状态并存的双轨模型。实施时将 `workspaceView` 替换为：

```text
activePane: "sources" | "plan" | "evidence"
activeInspectorTab: "readiness" | "plan" | "evidence"
```

`activeDocument` 继续独立表示 `jd | resume | split`。状态转换固定如下：

| 事件 | `activePane` | `activeInspectorTab` | 焦点行为 |
|---|---|---|---|
| 首次进入 | `sources` | `readiness` | 不强制移动焦点 |
| 点击 Activity Rail“资料” | `sources` | `readiness` | 留在 rail button |
| 点击“蓝图”，尚无计划 | `plan`，中央显示诚实空状态 | `plan` | 留在 rail button；主操作仍可生成计划 |
| 生成计划成功 | `plan` | `plan` | 不抢走当前操作焦点；通过 live region 宣布成功 |
| 点击 Activity Rail“证据” | `evidence`，中央显示证据 Pane | `evidence` | 留在 rail button |
| Inspector 内切换 tab | `activePane` 保持不变 | 切到目标 tab | 焦点留在目标 tab |
| 字段校验失败 | `sources` | `readiness` | 聚焦缺失 textarea |
| 修改源资料导致计划失效 | `sources` | `readiness` | 保持当前编辑器焦点 |
| 恢复 `?plan_id=` 成功 | `plan` | `plan` | 主内容可见，不自动聚焦 tab |
| 恢复 `?plan_id=` 失败 | `sources` | `readiness` | 焦点进入错误恢复动作或主内容 |

桌面与移动端共享同一状态机；`≤767px` 只改变 Inspector 的视觉位置，不创建第二套状态。Activity Rail 可以同步改变中央 Pane 与 Inspector tab；Inspector tab 只改变右侧/原位辅助内容，不反向切换中央 Pane。

### Task A4：压缩步骤与工作区标题

1. 删除中央工作区内的 `prep-stage-kicker`；
2. 不再重复展示“步骤 1 · 输入资料”；
3. 删除独立三步 stepper，由 Activity Rail 的“资料 / 蓝图 / 证据”当前态和底部当前请求状态共同表达流程；不再新增顶部生命周期行；
4. 桌面端删除 42rem 居中步骤条的独占布局；
5. 工作区标题改为约 19px / 600；
6. 说明控制为 13–14px、最多一至两行；
7. `0/2` 完成度与标题同行；
8. 不再使用最大 32px 的网页式 H1。

验收：

- 页面不出现重复步骤编号；
- 工作区标题符合 `DESIGN.md` 的 19px 约束；
- 标题区桌面高度约 72–96px。

## 6. Batch B：重构资料编辑工作区

### Task B1：将固定双栏改为文档标签工作区

模式：

- 岗位 JD；
- 候选人经历；
- 并排查看。

规则：

- 默认打开岗位 JD；
- 当前文档占据中央编辑面主体；
- 另一个文档通过标签显示完成状态；
- state 仍由 `StartPage` 控制，切换不丢内容；
- “并排查看”仅在 `min-width: 1180px` 出现；
- `1024–1179px` 明确禁用并排模式，不保留隐藏/禁用二选一；
- 移动端始终只显示一个文档；
- 完成状态不堆叠 Badge，只用小图标和短文本。

### Task B2：重新控制编辑器高度

删除：

```css
min-height: clamp(18rem, 43vh, 30rem);
```

桌面改为使用工作区剩余高度：

```css
flex: 1 1 auto;
min-height: 0;
```

textarea 自己滚动：

```css
height: 100%;
min-height: 0;
overflow: auto;
resize: none;
```

移动端自然流中保留约 20–24rem 最小输入高度。

验收：

- 空编辑器不再人为占用 43vh；
- 桌面 textarea 使用 Pane 剩余空间；
- 长内容只让 textarea 自己滚动；
- 移动端仍有舒适输入高度。

### Task B3：简化编辑器头部

目标：

```text
[文件图标] 岗位 JD · 职责、技术栈和约束             导入文本
```

实施：

- 移除 36px 浅蓝 icon tile；
- 使用 16–18px Phosphor 图标；
- 标题 16px / 600；
- 描述降为 12px；`≥768px` 固定单行省略，`≤767px` 不渲染描述；
- 导入按钮保持次级工具属性；
- 不改变 file input 行为与 accept 类型。

### Task B4：合并 metadata 与反馈

底部固定状态槽示例：

```text
未导入文件 · 0 / 50,000 字            支持粘贴或导入 .txt / .md
resume.md · 3,240 / 50,000 字          ✓ 内容已就绪
未导入文件 · 0 / 50,000 字            ! 请填写候选人经历
```

规则：

- 普通 hint 不增加额外层；
- ready 不展示长句成功说明；
- error 使用固定槽，不改变编辑器尺寸；
- textarea 始终保持白色，不整块铺红；
- 校验失败继续切换到缺失文档并聚焦 textarea。

### Task B5：迁移计划编辑器的命名空间

`DESIGN.md` 要求 `/prep` 专属组件使用隔离的 `start-*` 命名空间。当前 `PlanEditor.jsx` 和 `PlanQuestionCard.jsx` 仍输出大量 `.plan-*` 类名，必须纳入本次范围，而不能只改外层 Workbench。

修改：

- `frontend/src/components/PlanEditor.jsx`
- `frontend/src/components/PlanQuestionCard.jsx`
- `frontend/src/styles/pages/prep.css`

以下为主要映射；迁移范围是所有生产 `.plan-*` 类，最终以 `rg` 零结果为验收：

```text
plan-editor              → start-plan-editor
plan-editor-heading      → start-plan-editor-heading
plan-editor-kicker       → start-plan-editor-kicker
plan-editor-list         → start-plan-editor-list
plan-editor-count        → start-plan-editor-count
plan-question            → start-plan-question
plan-question-index      → start-plan-question-index
plan-question-body       → start-plan-question-body
plan-question-meta       → start-plan-question-meta
plan-question-evidence   → start-plan-question-evidence
plan-question-actions    → start-plan-question-actions
plan-exclude-action      → start-plan-exclude-action
plan-kind                → start-plan-kind
plan-required-label      → start-plan-required-label
plan-excluded-label      → start-plan-excluded-label
plan-focus-editor        → start-plan-focus-editor
plan-compact-action      → start-plan-compact-action
plan-evidence-content    → start-plan-evidence-content
plan-source-signals      → start-plan-source-signals
plan-evidence-fallback   → start-plan-evidence-fallback
plan-enable-action       → start-plan-enable-action
prep-launch-bar          → start-prep-launch-bar
```

要求：

- 保留现有 patch/CAS/regenerate 行为；
- 保留 `data-enabled`、`data-required`、`aria-*` 语义；
- CSS 选择器、组件 className 和所有浏览器测试同步迁移；
- 不通过兼容 class 永久维持两套命名空间；
- 迁移完成后用 `rg` 确认生产代码不再输出旧 `.plan-*` class；
- 新增准备页结构统一使用 `start-prep-*`：`start-prep-app-shell`、`start-prep-notice-slot`、`start-prep-status-bar`、`start-prep-inspector-*`、`start-prep-activity-*`；
- 不再新增新的 `.prep-*` 结构类；遗留 `.prep-*` 应在迁移过程中删除或收窄为兼容期内可消除的规则。

### Task B6：统一 `/prep` 图标来源

`MobileNav.jsx` 当前使用手写 SVG，而 `/prep` 的锁定规则要求使用随前端打包的 Phosphor 图标。

修改 `frontend/src/components/MobileNav.jsx`：

- 使用 `@phosphor-icons/react` 对应图标替换手写 `NavIcon` SVG；
- 保留现有导航 URL、`aria-current`、点击行为和移动布局；
- 对图标设置 `aria-hidden="true"`；
- 回归所有使用 MobileNav 的路由，不只验证 `/prep`。

## 7. Batch C：建立 Inspector 与操作层级

### Task C1：新增 PrepInspector

#### 生成前

显示真实准备状态：

- 岗位 JD：已填写 / 待填写；
- 候选人经历：已填写 / 待填写；
- 草稿状态；
- 知识证据：等待计划生成；
- 主操作：“生成并检查面试计划”。

不得显示空岗位标签、假题目数、假预计时长、假模型名、假检索状态或假评分。

#### 生成中

- 显示 spinner 和真实进行中文案；
- 主操作维持钴蓝 loading 形态；
- 原资料内容保持可见；
- 只禁用必要操作；
- 不使用全屏遮罩；
- 不插入推动布局的新横条。

#### 生成后

显示真实计划摘要：

- 计划标题；
- 真实题目数量；
- 可选的前端估算时长，必须明确标注为“估算”，例如“按当前启用题数估算约 20–30 分钟”；
- 真实岗位标签；
- Knowledge 状态；
- 证据可用、降级或无公开证据状态；
- 计划版本；
- 主操作：“确认版本并开始面试”。

题目编辑继续位于中央计划 Pane，不在 Inspector 重复全部题目。

当前 `InterviewPlan` 只有 `title`、`questions`、`prep_context` 等权威字段，没有 `duration` 或 `estimated_minutes`。本计划不新增后端字段、不修改 API schema，因此禁止把前端按题数计算的值写成“真实预计时长”。如果未来产品需要权威时长，必须另立后端契约计划，扩展模型、持久化结构、公开 payload 和测试后再接入。

Knowledge 公共状态必须完整映射，状态与 evidence count 分开判断：

| 公共 `knowledge_status` | 中文状态 | Evidence UI |
|---|---|---|
| `keyword` | 基于资料关键词准备 | 显示“当前计划依据 JD 与候选人经历中的关键词生成，未附加公开知识引用” |
| `completed` 且有 evidence | 知识证据可用 | 显示公开 evidence refs |
| `completed` 且无 evidence | 检索完成 | 诚实显示“检索已完成，当前没有可展示的公开引用” |
| `empty` | 未找到公开证据 | 显示中性空状态，不等同于 degraded |
| `degraded` | 知识检索已降级 | 显示通用降级说明；如果仍有部分公开 evidence，继续展示这些引用 |

约束：

- 不直接暴露 `keyword`、`completed`、`empty`、`degraded` 原始枚举；
- `degraded` 不等于“没有 evidence”；必须独立读取 evidence refs；
- 公共 payload 会移除内部 `binding_snapshot`，前端拿不到具体 `degraded_reason`；
- 不推断 `knowledge_unavailable`、`invalid_knowledge_metadata`、`corpus_manifest_mismatch` 等内部原因；
- 不把 `keyword` 错误映射为“未找到公开证据”或“检索完成”。

### Task C2：固定唯一主操作

桌面端：

- 主操作固定在 Inspector 底部；
- 48px 高；
- 接近全宽；
- Inspector 内容滚动时保持可见；
- 任意时刻只有一个最强按钮。

平板端：

- 主操作进入中央工作区底部 sticky action bar。

移动端：

- 主操作固定在 MobileNav 上方；
- 考虑 `safe-area-inset-bottom`；
- 不遮挡 textarea 最后一行；
- 页面预留相应 bottom clearance。

验收：

- 1280×900、1440×900、2048×1152 首屏可见；
- 375×900 不遮挡文档反馈；
- 生成前后均只有一个钴蓝主按钮。

### Task C3：降低草稿工具权重

- 自动保存状态长期可见，例如“14:32 已持久保存”；
- 手动保存固定保留在编辑器 command bar，使用轻量工具按钮；
- 恢复、删除已保存草稿、清空当前画布统一进入 Inspector 的“数据操作”折叠区；
- 移动端“数据操作”折叠区进入原位 Inspector 分段 Pane；
- 不新增“更多操作”popover，避免额外 click-outside、Escape 和焦点恢复逻辑；
- 删除和清空继续使用危险语义；
- 清空继续保留当前二次明确确认；
- 不增加普通确认弹窗；
- 删除已保存草稿后继续保留当前画布内容。

### Task C4：锁定 PrepStatusBar 内容契约

`PrepStatusBar` 使用共享 `.start-status-bar` 表面并增加 `.start-prep-status-bar` modifier。字段顺序固定，不在运行时插入新的 item：

| 顺序 | 项目 | 真实来源 | 示例 |
|---:|---|---|---|
| 1 | 当前请求 | `status`、`statusLabel` | 正在生成 / 计划就绪 / 需要处理 |
| 2 | 岗位 JD | `Boolean(jobDescription.trim())` | 已填写 / 待填写 |
| 3 | 候选人经历 | `Boolean(resumeText.trim())` | 已填写 / 待填写 |
| 4 | 草稿 | `draftMeta` 与保存状态 | 14:32 已持久保存 / 进程内临时保存 / 尚未保存 |
| 5 | Knowledge | `plan?.prep_context?.knowledge_status` | 证据可用 / 关键词准备 / 无公开证据 / 已降级 |

规则：

- “当前请求”始终是第一项，并带 `.start-status-current`；
- success 临时替换“当前请求”的文案与语义图标，不额外插入第六项；
- success 展示结束后回到稳定状态，例如“计划就绪”或“资料可编辑”；
- Knowledge 在尚无计划时显示“等待生成”，不显示空枚举；
- 移动端优先保证“当前请求”完整可见，其余 item 可以在状态栏内部水平滚动；
- 状态栏不重复顶部 notice 的长错误文本，错误时只显示短状态“需要处理”，详细说明留在 notice wrapper；
- 所有字段来自现有 React 状态或公开 plan payload，不增加推导型运行指标。

## 8. Batch D：状态、错误、焦点与按钮

### Task D1：建立准备页固定 notice wrapper 与底部 status bar

`StatusNotice` 继续保持“无内容时返回 `null`”的共享组件契约，不修改其公共 DOM 语义。固定高度由 `/prep` 自己拥有：

```jsx
<div className="start-prep-notice-slot">
  <StatusNotice notice={notice} />
</div>
```

规则：

- `.start-prep-notice-slot` 始终保留固定 `min-height` 或 `block-size`，无 notice 时为空白但不进入可访问树；
- 网络、服务端和计划错误进入该 slot；
- 字段错误继续留在编辑器 footer；
- success 优先下沉到 `PrepStatusBar`；
- notice wrapper 属于 `StartPage`，不属于共享 `StatusNotice`；
- 底部 `PrepStatusBar` 与顶部 notice slot 是两个不同概念，不得重复展示同一状态。

状态更新规则：

- info、loading、warning、error 在同一 slot 内更新；
- 文案变化不重建整个布局块；
- success 优先进入底部状态栏，使用 silent success；
- 字段错误留在编辑器底部；
- 网络与服务端错误进入固定全局槽；
- 只有命中下方白名单的可重试错误才提供页面自有重试动作。

重试动作由 `StartPage` 的 notice wrapper 所有，不扩展共享 `StatusNotice`：

```text
noticeAction: null | {
  operation: "generate" | "save" | "restore",
  label: string
}
```

`retryNoticeAction()` 根据 `operation` 分派到已有 `generatePlan`、`saveDraft`、`restoreDraft`，不把函数本身存进 React state。新操作开始、操作成功、不可重试错误或 notice 清除时同步清除 `noticeAction`。

| 错误来源 | 顶部重试动作 | 规则 |
|---|---|---|
| 生成计划 | `error.retryable === true` 时显示“重试生成” | 复用当前源资料和现有 `generatePlan` |
| 手动保存草稿 | `error.retryable === true` 时显示“重试保存” | 自动保存失败不抢占用户焦点，但可在状态栏说明 |
| 恢复草稿 | `error.retryable === true` 时显示“重试恢复” | 继续使用同一 draft id |
| 单题重生成 | 不显示顶部重试 | 使用题目级“换一道”按钮，避免重复入口 |
| 版本冲突 | 不显示普通重试 | 继续自动加载服务端最新版本 |
| 启动面试 | 不显示顶部重试 | 继续通过主按钮和同一 `command_id` 安全重试 |
| 404 / 410 | 不可重试 | 引导重新生成或返回资料 |
| 字段校验 | 不是网络重试 | 切换文档并聚焦缺失字段 |

渲染时，`StatusNotice` 与 wrapper-owned action 并列在 `.start-prep-notice-slot` 内；action 使用正常按钮语义并进入自然 tab 顺序。

移除 `/prep` 基于提示文本强制重新挂载的 key：

```jsx
key={`${notice.tone}-${notice.text}`}
```

### Task D2：在 `/prep` 作用域移除 side-stripe 提示

本次计划不直接改变所有路由共用的 `StatusNotice` 视觉契约。先在 `/prep` 的工作台作用域增加覆盖，避免影响 `/interview`、`/report-processing`、`/report-detail`、`/reports` 的既有验收；如果产品决定全站移除 side-stripe，再单独提交共享样式迁移。

在 `/prep` 作用域覆盖：

```css
box-shadow: inset var(--start-rule-strong) 0 0 ...;
```

替换为：

- 四周 1px 边界；
- 语义图标；
- 短标题；
- 浅色状态面；
- 必要时的重试操作。

状态映射：

- info：钴蓝图标 + 浅信息面；
- success：克制绿图标 + 近白或浅绿面；
- warning：琥珀图标 + 近白或浅琥珀面；
- error：危险红图标和文字 + 浅危险面。

共享组件变更后回归 `/interview`、`/reports`、`/report-processing` 和 `/report-detail`。

### Task D3：修复焦点环

- 删除准备页对所有按钮的多层 box-shadow 焦点覆盖；
- `:focus-visible` 使用 2px 实线 outline；
- outline offset 为 2px；
- 焦点环即时出现；
- focus ring 不参与 transition；
- 鼠标点击不保留不必要的蓝色光圈；
- 错误输入使用危险色 outline；
- 不取消键盘可访问焦点。

删除 `/prep` 的多层 box-shadow focus 覆盖；所有准备页控件统一复用共享 2px outline，focus 不参与 transition。主按钮的准备页规则不得 transition `outline` 或用 box-shadow 模拟焦点环。

### Task D4：补全交互八态

检查对象：

- 生成计划；
- 开始面试；
- 导入文本；
- 保存；
- 恢复；
- 删除；
- 清空；
- Inspector tab；
- 文档 tab。

状态：default、hover、focus-visible、active、disabled、loading、error、success。

规则：

- 主按钮保持钴蓝实色；
- hover 只改变背景明度；
- active 下压 1px；
- 不使用 scale 或 glow；
- disabled 同时改变背景、边界、文字与光标；
- loading 保持尺寸不变；
- 图标与 spinner 用 opacity 交叉切换；
- 文案变化不能导致按钮宽度跳动；
- success 不播放庆祝动画。

## 9. Batch E：排版、颜色与图标精修

### Task E1：严格恢复排版层级

- 应用标题：14–15px；
- 工作区标题：19px；
- Inspector 标题：17px；
- 文档标题：16px；
- 题目：14px；
- 编辑正文：15px / 1.75；
- 主操作：14px / 48px；
- metadata：11–13px。

中文排版：

- 标题保持正体；
- 不使用过强负字距；
- 不使用全大写英文 kicker；
- 点击文本保持单行；
- 数字状态使用正文体加 `tabular-nums`；
- 等宽字体只用于真实证据 ID 等技术内容。

字体第一版继续使用现有 Aptos / Microsoft YaHei UI / PingFang SC 栈，不临时添加未知授权字体。

### Task E2：增强表面层级，不更换主题

继续保留：

- 冷雾灰 app canvas；
- 近白中央工作面；
- 深海军文字；
- 单一钴蓝动作色。

调整：

- app canvas 稍深于中央工作面；
- textarea 接近白色；
- 淡钴蓝只用于当前 tab、rail item 与信息状态；
- 主钴蓝只用于唯一主操作、必要链接和当前选择；
- Inspector 与中央编辑器使用 1px 冷灰边界；
- 不给每一行增加 card 边框；
- 不增加渐变、玻璃、彩色阴影和装饰背景。

优先复用现有 `--start-*` token；如果必须新增，只增加语义 token，禁止在页面选择器中散落写入原始颜色值。

### Task E3：精简图标容器

- 继续只使用 Phosphor；
- rail 图标 20px；
- 文档与工具图标 16px；
- 主操作与提示图标 18px；
- 删除重复浅蓝 icon tile；
- 图标与文字并排时设置 `aria-hidden`；
- 不用 CSS 圆点作为唯一状态线索；
- 完成、警告与失败同时使用图形和文字。

## 10. Batch F：动效优化

### Task F1：主 Pane 首次进入

只对主 Pane 首次进入执行：

```text
opacity: 0 → 1
translateY: 6px → 0
duration: 320ms
ease: --start-ease-out
```

删除或收窄当前 `.prep-flow > *` 的通用进入动画。禁止 stepper、恢复提示、状态提示和 stage 各自独立上浮。

### Task F2：文档与 Inspector 切换

- 统一使用 160ms opacity crossfade；
- 默认不横向滑动整张 Pane；
- 不改变外层尺寸；
- 不触发页面滚动跳跃；
- 切换后的焦点落点符合操作语义。

### Task F3：功能性加载

- spinner：1000ms linear；
- 延迟约 150ms 显示；
- 一旦显示至少保持约 300ms；
- 使用独立的视觉状态，不延迟真实业务状态：

  ```text
  pending       = 真实请求是否仍在进行
  showSpinner   = 延迟后、满足最短显示窗口的视觉状态
  ```

- `aria-busy`、按钮 disabled 和业务状态立即反映真实请求；
- 只有视觉 spinner 延迟；
- 组件卸载时清理 delay/minimum-visible timer；
- 新请求开始时不复用上一次请求的最短显示窗口；
- 150ms 内完成的保存不显示 spinner；
- spinner 与完成图标不同时存在；
- reduced-motion 下使用静态 loading 图标或弱化动画。

新增 `frontend/src/hooks/useDelayedPending.js`，只在 `StartPage` 顶层调用一次，API 固定为：

```text
useDelayedPendingOperation(status, {
  pendingStates: ["saving", "restoring", "generating", "starting", "updating", "regenerating"],
  delay: 150,
  minimumVisible: 300
})
```

返回：

```text
{
  showSpinner: boolean,
  operation: null | "saving" | "restoring" | "generating" | "starting" | "updating" | "regenerating"
}
```

不要让同一个 `status` 在多个子组件中分别启动 timer。`StartPage` 计算一次后将视觉布尔值传给各消费者：

| 消费者 | 显示条件 |
|---|---|
| Topbar `RuntimeStatus` | `showSpinner === true`，显示当前 operation 的统一视觉 loading |
| `DraftSaveState` | `showSpinner && operation === "saving"` |
| 生成主 CTA | `showSpinner && operation === "generating"` |
| 启动面试 CTA | `showSpinner && operation === "starting"` |
| `PrepStatusBar` 当前请求项 | 任意 `showSpinner`；文案仍来自真实 `statusLabel` |
| 单题重生成 | `showSpinner && operation === "regenerating" && activePlanQuestionId === questionId` |
| 普通计划更新 | 状态栏/Topbar 可显示；题目操作按钮保持真实 disabled，不额外闪 spinner |

该 hook 只负责视觉状态与 timer 清理，不改变请求取消、错误处理、`aria-busy`、按钮 disabled 或真实 `status`。`prefers-reduced-motion` 只改变 spinner 的动画表现，不改变 pending 语义或延迟状态机。

### Task F4：恢复提示

- “上次面试已结束”压缩为固定状态槽中的恢复入口；
- 初次出现与关闭只做 160ms opacity；
- 不动画高度；
- 不让关闭动作导致中央工作区弹动。

## 11. 响应式计划（锁定布局矩阵与 MobileNav clearance）

不得使用“水平 switcher 或收窄图标轨”“下方 Pane 或可展开侧 Pane”等二选一描述。实施与测试统一采用以下矩阵。

共享 `MobileNav` 在 `≤900px` 显示；`767px` 只决定工作台布局形态，不决定导航是否存在。实施与测试统一采用以下矩阵：

| 视口宽度 | Activity Rail | Inspector | 文档并排 | 主操作与底部 clearance |
|---|---|---|---|---|
| `≥1180px` | 左侧 76px 垂直 rail，复用 `--start-app-rail-width` | 右侧 360px，复用 `--start-app-inspector-width` | 可用 | Inspector 底部固定；无需 MobileNav clearance |
| `1024–1179px` | 左侧 60px 垂直 rail | 中央工作区下方固定 Pane | 不渲染入口 | 中央 sticky action bar；无需 MobileNav clearance |
| `901–1023px` | 顶部水平 Pane switcher | 中央工作区下方固定 Pane | 不渲染入口 | 中央 sticky action bar；位于 status bar 之上 |
| `768–900px` | 顶部水平 Pane switcher | 中央工作区下方固定 Pane | 不渲染入口 | 中央 sticky action bar；必须叠加 `--mobile-nav-height + env(safe-area-inset-bottom)` clearance |
| `≤767px` | 顶部水平 Pane switcher | 原位分段 Pane，不渲染独立桌面 Inspector | 不渲染入口 | CTA 和 status bar 均位于 MobileNav 上方，并预留同一安全间距 |

各断点统一规则：

- 所有布局必须 `min-width: 0`；
- 不出现横向滚动；
- 320、375、414px 可点击控件不低于 44px；
- `<1180px` 不渲染并排入口，而不是创建 disabled tab 或只用 CSS 隐藏；
- 1024–1179px Inspector 只采用“中央下方固定 Pane”，不再增加可展开侧 Pane 分支；
- 768–1023px Activity Rail 只采用“顶部水平 Pane switcher”，通过 `.start-prep-*` modifier 重排同一组控制，不维护两套 React 状态；
- ≤767px Inspector 只采用“原位分段 Pane”，不创建独立 desktop Inspector DOM；
- 所有 `≤900px` 的 sticky/fixed CTA、status bar 和内容 bottom padding 必须使用共享 `--mobile-nav-height` 与 safe-area clearance；
- 不修改 MobileNav 的全站 900px 显示断点；
- 主操作的焦点顺序和滚动容器在每个断点保持稳定。

## 12. 测试迁移

### 12.1 修正旧结构断言

删除测试中“activity rail / inspector / status bar 必须不存在”的断言，改为：

- 桌面 activity rail 可见；
- 桌面 Inspector 可见；
- desktop status bar 可见；
- 中等宽度按响应式规则重排；
- 移动端不要求三栏同时存在；
- 测试使用新业务语义，不继续使用 `oldActivityRail` 等命名。

同步审查并迁移以下直接依赖旧 `/prep` DOM 或旧 class 的文件：

- `tests/browser/phase1-safety.spec.js`：`.prep-stage` 等页面存在性断言；
- `tests/browser/local-v1.spec.js`：`.plan-question`、`.plan-question-evidence` 选择器；
- `tests/browser/reference-ui-geometry.js`：768 / 1024 / 1280 等公开 viewport 矩阵；
- `tests/test_react_frontend.py`：`.prep-flow`、`.prep-stage`、`.prep-launch-bar` 和旧计划 class 合约；
- `tests/test_frontend_phase5.py`：页面 CSS 归属、组件引用和结构性安全检查。

如果执行 `plan-* → start-plan-*` 命名迁移，上述所有测试必须同步迁移，不能通过同时输出新旧 class 长期维持兼容。

### 12.2 首屏主操作可见性

覆盖 viewport：

- 1280×900；
- 1440×900；
- 2048×1152；
- 1024×900；
- 901×900；
- 900×900；
- 844×900；
- 768×900；
- 414×900；
- 375×900；
- 320×900。

检查主操作 rect 位于 topbar 以下、viewport 底部以上；`900px` 与 `844px` 必须明确断言 CTA/status bar 位于 MobileNav 上方，`901px` 必须断言不应用 MobileNav clearance。

### 12.3 Pane 几何

桌面检查：

- rail 宽度符合 token；
- Inspector 宽度符合 token；
- 中央工作区宽度大于合理下限；
- 三栏不覆盖；
- editor 可内部滚动，外层工作台不溢出。

移动端检查：

- 只显示一个编辑器；
- 不显示并排模式；
- `scrollWidth <= clientWidth`；
- 可见按钮高度 ≥43.5px。

### 12.4 状态槽无布局位移

在导入、保存、生成、字段错误前后记录中央 Pane 边界。要求：

- status 文案变化不改变 Pane 顶部位置；
- 主操作不随 notice 跳动；
- 字段 error 槽高度稳定。

### 12.5 焦点

键盘聚焦文档 tab、textarea、导入、主按钮、Inspector tab 和“数据操作”折叠区，检查：

- focus-visible 存在；
- outline 不为 `none`；
- 焦点环即时出现；
- 不依赖发光型多层 box-shadow；
- 错误 textarea 继续使用 `aria-invalid="true"`。

当前 `phase2-prep-plan.spec.js` 中“主按钮 box-shadow 不为 none”的断言需要迁移为新的 outline 契约。

### 12.6 加载闪烁

- 100ms 内完成的保存：不显示 spinner；
- 500ms 的生成：显示 spinner；
- spinner 一旦显示保持足够可读时长；
- loading 中按钮高度仍 ≥48px；
- loading 前后按钮宽度不明显变化。
- 100ms 保存期间 Topbar、`DraftSaveState`、CTA 与 `PrepStatusBar` 均不得闪 spinner；
- 500ms 生成期间 Topbar、生成 CTA 与 `PrepStatusBar` 使用同一个顶层视觉 pending 状态；
- 单题重生成只让当前 `activePlanQuestionId` 的题目显示 spinner；
- `aria-busy` 在真实请求开始时立即成立，不等待 150ms；
- hook 卸载与连续请求测试确认 timer 不泄漏、不复用旧 minimum-visible 窗口。

### 12.7 Knowledge 四态与 evidence 独立性

浏览器或组件契约测试必须覆盖：

- `keyword` 显示“基于资料关键词准备”，不暴露原始枚举；
- `completed` + evidence 展示公开引用；
- `completed` + 无 evidence 显示检索完成但无可展示引用；
- `empty` 显示中性“未找到公开证据”；
- `degraded` 显示通用降级说明，不出现内部 `degraded_reason`；
- `degraded` + 部分 evidence 继续展示公开引用，证明状态与 evidence count 是独立条件。

### 12.8 Workbench 状态转换与重试白名单

测试转换表中的关键路径：

- 首次进入 `sources/readiness`；
- 点击蓝图但尚无计划时显示诚实空状态；
- 生成成功同步为 `plan/plan`，不抢焦点；
- 点击证据同步为 `evidence/evidence`；
- Inspector tab 切换不反向切换中央 Pane；
- 校验失败同步回 `sources/readiness` 并聚焦缺失 textarea；
- `?plan_id=` 恢复成功进入 `plan/plan`；
- 生成、手动保存、恢复的 retryable error 显示唯一 wrapper-owned action；
- 单题重生成、版本冲突、启动面试、404/410、字段校验不显示错误的顶部重试入口。

### 12.9 MobileNav 与共享路由回归

因为 `MobileNav.jsx` 将手写 SVG 迁移为 Phosphor，需要回归所有共用 AppShell 的路由，而不是只检查 `/prep`：

- `/prep`
- `/interview`
- `/reports`
- `/report-processing`
- `/report-detail`
- `/help`

检查导航 URL、`aria-current`、激活色、图标尺寸、移动端点击目标和横向溢出。

### 12.10 必须保留的功能回归

- 不支持文件的文本粘贴回退；
- JD 与经历缺失时的文档切换、聚焦和 `aria-invalid`；
- 900ms 自动保存 debounce；
- 手动保存、恢复、删除和清空二次确认；
- 生成真实计划题；
- 排序、CAS、重点、必考与排除；
- 单题重新生成；
- 失败时保留原题；
- 版本冲突恢复；
- 知识证据正常与降级状态；
- 启动面试；
- reduced-motion。

## 13. 提交批次

### Commit 1：契约决策与测试迁移

在写新 UI 前先锁定并写进测试：

- 时长只显示“前端估算”，不声明后端权威时长；
- 复用现有 `.start-*` Workbench CSS，不新增第二套 Pane 骨架；
- `≥1180 / 1024–1179 / 901–1023 / 768–900 / ≤767` 唯一导航感知响应式矩阵，并锁定 `≤900px` MobileNav clearance；
- Activity Rail 使用 button + `aria-pressed` + `aria-controls`；
- Inspector 固定使用完整 tab/tabpanel 语义；
- 顶部 `/prep` notice wrapper 与底部 status bar 的职责分离；
- `plan-* → start-plan-*` 命名迁移范围；
- 更新 `prep-ui`、`phase2-prep-plan`、`reference-ui`、`phase1-safety`、`local-v1`、`reference-ui-geometry` 和 Python 合约测试。

### Commit 2：复用 Workbench 骨架

- `StartPage` 接入已有 `.start-app-shell`、`.start-activity-rail`、`.start-editor-workspace`、`.start-inspector`、`.start-status-bar`；
- 新增三个 Prep 展示组件；
- 不扩展 AppShell 公共 API；
- 删除或隔离 `.prep-flow` 的 90rem 页面岛和旧全页滚动规则；
- 先完成三栏几何和 Pane 滚动契约。

### Commit 3：文档编辑器与计划命名空间

- 文档 tab、单活动文档、宽桌面并排模式；
- 锁定 1180px 并排断点；
- textarea 占据 Pane 剩余高度；
- 迁移 `PlanEditor.jsx` 和 `PlanQuestionCard.jsx` 的 `start-plan-*` class；
- 同步所有 `plan-question`、`plan-question-evidence` 和 `prep-launch-bar` 测试选择器；
- 保留 patch/CAS/regenerate 全部业务行为。

### Commit 4：Inspector、主操作、状态与草稿工具

- 明确 `PrepInspector` props 和 tab 语义；
- 真实映射 `prep_context.knowledge_status`；
- 证据 `keyword / completed / empty / degraded` 四态；状态与 evidence count 独立判断，`degraded` 不推断公共 payload 未提供的具体原因；
- 时长只显示带“估算”标签的前端计算值；
- 主操作固定可见；
- 保存、恢复、删除、清空降为工具；
- 增加 `/prep` 自有 notice wrapper；
- success 优先进入底部状态栏；
- 只在 `/prep` 作用域覆盖 side-stripe，暂不改变共享 StatusNotice 公共契约。

### Commit 5：焦点、动效、响应式与完整验收

- 删除准备页 focus 的 box-shadow 覆盖；
- Pane 首次进入只执行一次；
- 内容 160ms crossfade；
- `useDelayedPending` 延迟 spinner，并清理 timer；
- reduced-motion；
- MobileNav 手写 SVG 迁移为 Phosphor；
- 五行布局矩阵与 `900px` MobileNav clearance 契约；
- 全 viewport 几何、共享路由和浏览器套件回归。

## 14. 验证命令

```powershell
npm --prefix frontend run check
npm run build:frontend
pytest tests/test_react_frontend.py tests/test_frontend_phase5.py
npm run test:browser:preflight
npm run test:browser
git diff --check
```

可以先运行准备页定向浏览器测试以缩短反馈周期，但最终交付前必须运行完整浏览器套件。

## 15. Definition of Done

- `/prep` 桌面端呈现活动轨、中央工作区、Inspector 和底部状态栏；
- 三栏直接复用现有 `.start-*` Workbench 骨架，不存在第二套互相覆盖的 Pane CSS；
- 当前资料编辑器是最高层级表面；
- 默认只强调一份文档；
- 宽桌面支持“并排查看”；
- `<1180px` 不渲染并排入口，不创建 disabled 入口，也不只依赖 CSS 隐藏；
- 响应式只存在 `≥1180 / 1024–1179 / 901–1023 / 768–900 / ≤767` 一套锁定矩阵，不保留二选一实现分支；
- 所有 `≤900px` sticky/fixed CTA、status bar 与内容底部留白均叠加 `--mobile-nav-height + env(safe-area-inset-bottom)`，且不修改共享 MobileNav 的 `900px` 断点；
- 主操作在全部目标 viewport 首屏可见；
- 页面根容器不承担长距离叙事滚动；
- 编辑器与计划列表在 Pane 内滚动；
- 阶段 kicker 被移除；
- 工作区标题约 19px；
- 全局状态变化不推动工作区；
- 字段错误就近显示并自动聚焦；
- 提示不再使用粗侧边状态条；
- `/prep` 顶部 notice wrapper 与底部 status bar 职责不重复；
- Activity Rail、中央 Pane 与 Inspector 遵守同一状态转换契约：Rail 可以同步 Pane/Inspector，Inspector tab 不反向切换中央 Pane，桌面与移动端共享同一状态机；
- 校验失败回到 `sources/readiness` 并聚焦缺失 textarea；生成或恢复成功进入 `plan/plan`，但不抢走当前操作焦点；
- retryable 的生成、手动保存与恢复错误只显示一个 `/prep` wrapper-owned 重试入口；单题重生成、版本冲突、启动面试、404/410 与字段校验不生成顶部重试入口；
- `PrepStatusBar` 固定按“当前请求、JD、经历、草稿、Knowledge”排序；success 替换当前请求项，不插入额外状态项，移动端优先保留当前请求；
- 键盘焦点即时、清晰、无发光动画；
- 快速操作不闪 spinner；
- spinner 视觉状态与真实 pending / `aria-busy` 语义分离；
- 顶层只调用一次 `useDelayedPendingOperation`，Topbar、草稿状态、生成/启动 CTA、`PrepStatusBar` 与活动单题按固定消费者契约复用同一视觉 pending 结果；
- 计划时长若展示，必须明确标注为前端估算，不伪装成后端事实；
- `keyword / completed / empty / degraded` 四态均使用中文公共映射，状态与 evidence count 独立；`degraded` 可继续展示已有公开 evidence，但不推断、不显示内部 `degraded_reason` 或 `binding_snapshot`；
- 所有新准备页结构使用 `start-prep-*`；`PlanEditor`、`PlanQuestionCard` 和相关 CSS/测试完成 `start-plan-*` 命名迁移，生产代码不再输出旧 `.plan-*` class；
- MobileNav 使用 Phosphor 图标并通过共享路由回归；
- 动效不超过三种原语；
- reduced-motion 生效；
- 没有新增 UI 框架、图标库或动画依赖；
- 没有修改 API 或后端业务逻辑；
- 没有虚构题目、证据、状态、指标或模型信息；
- 320、375、414px 无横向滚动；
- 移动端可见控件至少 44px；
- lint、build、Python 合约测试与 Playwright 回归全部通过；
- `git diff --check` 通过；
- 所有修改进入清晰、可追溯的提交。

## 16. 最低可交付切分

如果必须分两轮完成，第一轮至少包含：

1. 恢复 Workbench 三栏结构；
2. 单活动文档；
3. 主操作固定可见；
4. 固定状态槽；
5. 修正焦点环；
6. 更新冻结旧结构的浏览器测试。

第二轮完成：

1. Inspector 细节；
2. 草稿工具折叠；
3. icon tile 精简；
4. spinner 延迟；
5. 文档与 Pane crossfade；
6. 全 viewport 视觉精修。

该顺序确保每一批都先解决真实结构问题，而不是继续在错误宏观布局上打磨边角。
