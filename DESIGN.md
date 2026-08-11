---
version: 1.1.0
name: Interview-Agent-design-system
status: project-specific
updated: 2026-08-03
description: >-
  Interview Agent 的项目级设计规范。视觉方向受 Cohere 的企业 AI、研究出版物与
  Agent Console 语言启发，但所有颜色、排版、组件、页面和状态规则均针对本项目的
  本地单用户技术面试流程、中文长文本、流式问答、异步报告、五维评分和 RAG 证据展示重写。

colors:
  canvas: "#ffffff"
  page: "#f6f6f3"
  surface-stone: "#eeece7"
  surface-blue: "#f1f5ff"
  surface-green: "#edf8f3"
  ink: "#21211f"
  ink-strong: "#17171c"
  ink-secondary: "#4f514d"
  ink-muted: "#73756f"
  ink-faint: "#9b9d97"
  line: "#dcddd8"
  line-strong: "#bfc1ba"
  cta: "#17171c"
  cta-hover: "#2b2b31"
  on-cta: "#ffffff"
  action-blue: "#2457d6"
  action-blue-hover: "#1d47b3"
  action-blue-subtle: "#eef3ff"
  focus-ring: "#4c6ee6"
  agent-navy: "#071829"
  agent-navy-elevated: "#10283c"
  agent-navy-soft: "#17364f"
  agent-green: "#003c33"
  agent-green-elevated: "#075446"
  on-agent: "#f7fbfa"
  on-agent-muted: "#b9cac5"
  coral: "#ff7759"
  coral-strong: "#b7472f"
  coral-subtle: "#fff0eb"
  success: "#17845e"
  success-strong: "#116b4c"
  success-subtle: "#edf8f3"
  warning: "#b7791f"
  warning-strong: "#8e5b13"
  warning-subtle: "#fff7e8"
  danger: "#c2413b"
  danger-strong: "#96332e"
  danger-subtle: "#fff1f0"

typography:
  display:
    fontFamily: "Space Grotesk, Noto Sans SC, PingFang SC, Microsoft YaHei, system-ui, sans-serif"
    fontSize: 40px
    fontWeight: 500
    lineHeight: 1.12
    letterSpacing: -0.6px
  page-title:
    fontFamily: "Space Grotesk, Noto Sans SC, PingFang SC, Microsoft YaHei, system-ui, sans-serif"
    fontSize: 28px
    fontWeight: 500
    lineHeight: 1.25
    letterSpacing: -0.3px
  section-title:
    fontFamily: "Noto Sans SC, PingFang SC, Microsoft YaHei, Segoe UI, system-ui, sans-serif"
    fontSize: 20px
    fontWeight: 600
    lineHeight: 1.35
    letterSpacing: 0
  card-title:
    fontFamily: "Noto Sans SC, PingFang SC, Microsoft YaHei, Segoe UI, system-ui, sans-serif"
    fontSize: 16px
    fontWeight: 600
    lineHeight: 1.45
    letterSpacing: 0
  body:
    fontFamily: "Noto Sans SC, PingFang SC, Microsoft YaHei, Segoe UI, system-ui, sans-serif"
    fontSize: 15px
    fontWeight: 400
    lineHeight: 1.65
    letterSpacing: 0
  ui:
    fontFamily: "Noto Sans SC, PingFang SC, Microsoft YaHei, Segoe UI, system-ui, sans-serif"
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: 0
  label:
    fontFamily: "Noto Sans SC, PingFang SC, Microsoft YaHei, Segoe UI, system-ui, sans-serif"
    fontSize: 12px
    fontWeight: 600
    lineHeight: 1.4
    letterSpacing: 0.2px
  mono-label:
    fontFamily: "JetBrains Mono, Cascadia Code, SFMono-Regular, Consolas, monospace"
    fontSize: 12px
    fontWeight: 500
    lineHeight: 1.5
    letterSpacing: 0.35px

rounded:
  xs: 4px
  control: 6px
  card: 10px
  panel: 14px
  feature: 20px
  pill: 9999px

spacing:
  0: 0px
  1: 4px
  1-5: 6px
  2: 8px
  2-5: 10px
  3: 12px
  4: 16px
  5: 20px
  6: 24px
  8: 32px
  10: 40px
  12: 48px
  16: 64px
  20: 80px

components:
  button-primary:
    backgroundColor: "{colors.cta}"
    textColor: "{colors.on-cta}"
    rounded: "{rounded.pill}"
    height: 40px
    padding: "0 20px"
  button-secondary:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    borderColor: "{colors.line-strong}"
    rounded: "{rounded.control}"
    height: 40px
    padding: "0 16px"
  button-danger:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.danger}"
    borderColor: "{colors.danger}"
    rounded: "{rounded.control}"
    height: 40px
    padding: "0 16px"
  app-card:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    borderColor: "{colors.line}"
    rounded: "{rounded.card}"
    padding: 24px
  agent-console:
    backgroundColor: "{colors.agent-navy}"
    textColor: "{colors.on-agent}"
    rounded: "{rounded.panel}"
    padding: 20px
  processing-band:
    backgroundColor: "{colors.agent-green}"
    textColor: "{colors.on-agent}"
    rounded: "{rounded.feature}"
    padding: 32px
  evidence-row:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    borderColor: "{colors.line}"
    rounded: "{rounded.control}"
    padding: 16px
---

# Interview Agent Design System

## 0. Locked Direction — Assessment Editorial Workbench

### 0.0.1 `/prep` App Override — Calm Cobalt Workbench

用户已明确要求 `/prep` 完全重新开始设计，并进一步确认它必须表现为可操作的应用工作台，而不是网页、落地页、编辑出版物或长滚动展示。本节仅覆盖 `/` 与 `/prep` 的宏观构图、色彩、组件声音和响应式形态；其他五条路由继续遵守 Assessment Editorial Workbench。

- `/prep` 必须占满可用视口，由固定应用顶栏、56–68px 紧凑活动轨、中央文档编辑器、340–400px 右侧 Inspector 和 28–34px 底部状态栏组成。
- 禁止营销 Hero、超大展示标题、章节式长页面、连续大色块叙事、装饰性 START 编号和依赖整页滚动的内容顺序。
- 桌面端页面本身不承担叙事滚动；文档编辑器、问题列表和证据列表必须在各自 Pane 内滚动。中等宽度下 Inspector 进入下方分栏，移动端再转为自然的单列应用流。
- 取消准备页旧 232px 流程侧栏、Research Canvas 双栏卡片和森林绿 Knowledge 签名面；允许紧凑活动轨，但它只切换“资料、蓝图、证据”等应用 Pane，不展示大段步骤说明。
- 中央编辑器是最高优先级表面，使用“岗位 JD、候选人经历、并排查看”文档标签；默认一次只强调一份文档，导入、字符计数、保存、恢复与清空作为编辑器工具存在。
- 右侧 Inspector 固定承载面试计划、证据和准备状态。计划生成前显示诚实空状态；生成后显示真实标题、题目、预计时长、岗位标签、Knowledge Agent 路径和证据引用。
- `/prep` 的视觉主题改为 Calm Cobalt：冷雾灰应用底色、近白工作面、深海军文字和单一钴蓝操作色。此前的骨白纸张、黑色大顶栏、荧光酸绿和工业终端感不得继续使用。
- 桌面端活动轨、编辑器和 Inspector 是三个有明确层级的独立工作面，使用统一 10–14px 圆角、极轻冷色阴影和低对比边界；禁止把每一行都框起来，也禁止玻璃拟态与浮夸高光。
- 顶栏必须是轻量应用栏而不是深色命令条；主按钮使用钴蓝实色与白色文字，选中态优先使用淡蓝底、钴蓝文字或细下划线，不使用整块黑底反转。
- 页面只允许一个可用主操作：无计划时为“生成面试计划”，有计划后为“开始本次面试”。重新生成、草稿和清空始终保持次级层级。
- 保留 `/api/prep`、`/api/interview-drafts`、`/api/interviews`、匿名草稿 ID、文本导入、真实错误、`data-prep-state` 和现有可访问名称。不得伪造连接速度、模型名、延迟、Worker、候选人评分或运行指标。
- 320、375、414px 必须无横向滚动；移动端点击目标最小 44px，可点击文本保持单行，并保留减少动态偏好。
- 准备页由独立的 `StartPage.jsx`、`styles/pages/prep.css` 与共享 Calm Cobalt Token 实现；旧 `PrepPage.jsx` 和被否决的 `start-page.css` 已在 Phase 5 删除，不得恢复为运行页面、测试夹具或设计来源。
- `/prep` 的图标只使用随前端打包的 Phosphor 图标体系：工具与标签 16px、主操作与提示 18px、活动轨 20px。图标与文字并列时只承担辅助识别并对辅助技术隐藏，不允许用单字、Emoji 或混合图标库代替。
- 排版层级固定为：应用标题 14–15px、工作区主标题 19px、Inspector 标题 17px、文档标题 16px、题目 14px、编辑正文 15px / 1.75、主操作 14px / 48px、元信息 11–13px。标题保持正体，正文、标题和等宽状态最多使用三套字体栈；`/prep` 的等宽字体只用于品牌缩写和真实证据 ID，其余状态与数字使用正文体配合 tabular numerals，避免技术标签过量。
- 所有交互控件必须具备默认、悬停、键盘焦点、按下、禁用和加载状态。主按钮使用钴蓝实色；次按钮使用近白表面和冷灰边界；禁用不能只降低透明度，还必须切换背景、边界、文字和光标。
- 信息、成功和错误提示分别使用信息蓝、克制绿和错误红的独立浅色表面与语义图标。字段错误保留固定提示槽；校验失败后必须切换到缺失文档并把焦点送入对应编辑区，不能只在远处显示通用 Toast。
- 应用三个主 Pane 只在首次进入时使用 320ms 的 opacity + 6px transform；文档切换、Inspector 切换和普通提示统一使用 160ms 纯透明度交叉淡入，错误提示立即出现。加载图标使用 1000ms 线性旋转；全页最多保留“Pane 进入、内容交叉淡入、功能性加载”三种动画原语，不使用列表错峰、摇晃、弹跳、发光、统一缩放或会改变布局的动画。
- `/prep` 的状态反馈必须区分 Action、Success、Warning、Danger 与 Neutral：资料完成和检索完成使用克制绿；检索降级使用琥珀色 Warning；没有公开证据使用中性信息态；只有不可恢复失败和破坏性操作使用错误红。后端 `completed`、`degraded`、`empty` 等稳定值必须映射为可读中文，不直接作为醒目 UI 文案暴露。
- 编辑器底部保留固定状态槽，但正常状态不得显示空白占位：空文档说明支持的导入方式与上限，已有内容显示“内容已就绪”，校验失败才切换为红色错误与下一步。文本编辑面不因普通 Hover 整块变色。
- 保存与恢复进入 Loading 时仍保持次级工具按钮的边界、表面和尺寸；清空当前画布属于会丢失未保存文本的破坏性操作，必须经过第二次明确确认。移动端底部状态栏将当前请求置于可见首位，不能要求用户横向滚动后才能发现正在生成或失败。
- 顶栏运行状态、文档完成状态、Inspector 知识状态和底部状态栏都使用 Phosphor 图形 + 文字 + 语义色联合表达；不得再以纯 CSS 小圆点作为唯一形状线索。Inspector Tab 必须具备完整 `id`、`aria-controls`、`role="tabpanel"` 与 `aria-labelledby` 关系。
- `/prep` 的错误色只占用必要反馈区域：顶部通知、字段焦点轮廓和固定错误状态行可以使用 Danger，但大面积 textarea 编辑面始终保持白色工作表面，不能因空字段校验整块铺成红色或粉色。
- `/prep` 专属组件不得复用会被其他路由全局样式命中的通用展示类，例如 `.knowledge-section`、`.plan-metrics`、`.plan-question`；必须使用 `start-*` 命名空间显式隔离表面、文字和边界。
- Inspector 遵守渐进披露：计划生成前只显示计划空状态，不渲染空岗位标签、空指标行或空问题列表；证据生成前只显示证据空状态，不渲染检索标题、等待 Badge、摘要和空主题标签。真实计划返回后再展开对应信息结构。

本节是当前前端视觉与交互实现的最高优先级规范。它在不改变业务逻辑、API、真实状态、可访问性与测试契约的前提下，覆盖本文档中与视觉表达冲突的旧示例。任何页面不得退回到等权卡片网格、营销落地页 Hero、通用后台模板或全站暗色仪表盘。

### 0.0.2 `/interview` App Override — Focused Calm Cobalt Workbench

`/interview` 与 `/prep` 属于同一条应用工作流，必须直接继承 Calm Cobalt 的顶栏、冷雾灰应用底色、近白工作面、钴蓝操作色、圆角、轻边界、按钮状态、图标体系和底部状态栏；旧版整片深海军 Agent Console、米黄色题目侧栏、珊瑚粗线及彼此割裂的三栏表面不再使用。

- 桌面端使用“题目导航 / 中央面试工作区 / 会话 Inspector”三栏，但三栏共享同一套面板语言；中央工作区仍是唯一最高层级表面，不把每一个事实拆成同权卡片。
- 左侧题目导航宽度约 208–240px，使用连续问题清单。当前题以淡蓝表面、钴蓝细指示和 `aria-current="step"` 联合表达；已回答、跳过、未回答和待进行必须同时具有图形或编号、文字状态与语义色。
- 中央工作区顺序固定为：紧凑工作区标题、当前题工具栏、当前问题、真实对话记录、回答编辑器。当前问题只出现一次，不再同时使用巨大问题卡和重复标题。
- 对话区改为浅色 Calm Cobalt 工作面：雾灰记录底、白色 Agent 消息、淡蓝候选人消息，通过 Phosphor 角色图标、标签、对齐和表面共同区分。不得恢复整片 `#071829` 背景，也不得模拟模型思考、延迟、Worker 或在线状态。
- 回答编辑器是工作区底座，textarea 高度 124–192px；左侧持续展示真实本地草稿状态与字符数，右侧只允许“提交回答”为钴蓝主按钮，“跳过此题”为次按钮，“结束面试”为静默危险操作。
- 右侧 Inspector 只显示真实完成进度、已用时、预计剩余、岗位标签、逐题评审数量、当前题号与会话 ID。缺失值显示“--”“尚未返回”或诚实空状态，不填充示例指标。
- 应用顶栏状态和 Inspector 状态必须将 `loading/active/submitting/finishing/finished/error` 映射为中文，并以 Phosphor 图标、文案和语义色联合表达；禁止直接用英文后端枚举或纯色圆点作为主要反馈。
- 专注模式隐藏两侧 Pane，使中央工作区最大约 1088px；保留 `aria-pressed`、Escape 恢复、当前问题、草稿和滚动位置语义，不改变 API 或会话状态。
- 320、375、414px 下题目导航变为横向问题清单，工作区与 Inspector 变为单列；所有按钮至少 44px、可点击文字保持单行、页面无横向滚动。768–1099px 下 Inspector 进入工作区下方的事实分栏。
- 动效只允许 Pane 首次进入、状态内容淡入和功能性加载三类。普通状态使用 160–200ms opacity/transform，Pane 使用 320ms opacity + 小位移，加载图标使用 1000ms 线性旋转；不使用发光、弹跳、摇晃、按钮整体缩放或无意义持续动画。
- 保留 `/interview?session_id=...`、快照恢复、SSE 与重连、回答草稿、提交、跳题、结束、逐题评审、`data-interview-state`、`data-review-state`、`data-interview-phase` 和现有可访问名称。
- `/interview` 第二轮细节规范：当前问题正文桌面端使用 18px / 600，320–479px 降至 16px；对话正文使用 15px / 1.72，角色标签和 Inspector 元数据不得低于 12px。问题、消息与辅助状态之间必须依靠字号、字重和留白建立层级，不能只换颜色。
- Phosphor 图标按语义统一：活动/导航入口可用 `duotone`，普通工具与方向动作用 `bold`，稳定完成或错误状态使用 `fill`；同一按钮只保留一个主图标，禁止在“提交回答”中同时堆叠发送与箭头图标。
- 题目计划行是只读状态，不得使用指针光标或普通 Hover 填色暗示可点击；只在题目变为当前题或已完成时执行一次 220ms `opacity + translateY(4px)` 状态反馈。
- 当前题切换使用 280ms 内容进入；新消息、进度数字、评审数字和状态文案使用 160–220ms 同源进入；按钮只移动内部方向图标约 2px。全页不得新增第四种动画原语，流式光标和真实 Spinner 仍属于功能性加载。
- 空回答必须显示“提示标题 + 原因/下一步”的两层行内反馈，将 `aria-invalid="true"` 设置到回答框并立即恢复焦点；用户重新输入有效文本后应清除该字段错误。不可恢复请求失败使用 `role="alert"` 且立即出现，不执行空间动画。
- 提交请求期间主按钮保持钴蓝表面、原尺寸、Spinner 与“正在提交”文字；结束和跳题保持次级。所有按钮 Hover 只改变表面或内部图标，Focus ring 立即出现，按下最多下移 1px。

### 0.1 设计命题

Interview Agent 不是“聊天机器人套壳”，而是一套把岗位资料、候选人经历、实时问答、评分证据和运行轨迹组织成可信评估出版物的本地工作台。

- 产品语气：editorial workflow · command-center precision · evidence publication。
- 视觉基因：Airtable 55%（工作流与签名色块）、WIRED 25%（报告出版结构）、Sanity 20%（Agent 与 Pipeline 命令中心）。
- 核心感受：输入像源文件，计划像编辑简报，面试像运行中的控制台，报告像可审阅的技术刊物。
- 品牌锚点：企业绿、深海军蓝、珊瑚色、奶油纸张色；蓝色只承担操作、焦点和链接职责。

### 0.2 全局构图规则

1. 页面先建立一个清晰的主叙事面，再安排工具和辅助信息。禁止把所有内容拆成大小相同、权重相同的卡片。
2. 主要表面采用开放网格、细规则线、色块分区和不对称列宽。阴影只用于悬浮操作条或确有层级变化的面板。
3. 每页最多允许一到两个签名色面。珊瑚、森林绿和深海军蓝必须表达产品阶段或信息性质，不得作为无意义装饰。
4. 标题使用宽阔、紧凑的编辑式排版；正文保持中文长文可读性；运行 ID、阶段、时间和状态使用等宽字体。
5. 小标签只用于状态、类型和稳定标识。不得在每个标题上堆叠 eyebrow，也不得把所有文本做成胶囊。
6. 所有指标、分数、耗时、引用、Agent 状态和生成阶段必须来自真实接口。缺失时明确显示“暂无”“未提供”“处理中”或降级原因，禁止填充演示数据。
7. 交互动画只允许 opacity 与 transform，常规持续时间 160–300ms；进度使用 scaleX。减少动态偏好下必须立即完成。

### 0.3 路由级设计职责

#### `/prep` — Interview Setup Workbench

- JD 与简历是应用内的两份可切换源文档，由中央编辑器承载，不再作为页面章节或两张展示卡片。
- 面试计划是右侧可滚动 Inspector：顶部显示真实状态和指标，中部显示问题清单，底部固定当前唯一主行动。
- Knowledge Agent 使用 Inspector 内独立证据 Pane，清楚区分“尚未检索、可用、降级和无公开证据”，不再用大面积主题色制造页面章节。
- 底部状态栏只汇总本地草稿、两份输入、Knowledge 状态和当前请求状态；所有内容都必须来自真实前端状态。

#### `/interview` — Agent Command Center

- 中央工作区采用 Calm Cobalt 的浅色多层工作面；当前问题是最高视觉焦点，对话记录次之，回答编辑器是稳定的工作台底座。
- 左侧题目轨道是连续问题清单而非卡片集合；右侧只显示紧凑会话事实、考察点和逐题评审状态。
- Agent 与候选人回答以白色 / 淡蓝表面、角色图标、标签和左右对齐快速区分，不再使用整片深海军背景。
- 聚焦模式、Escape 恢复、流式文本、跳题和结束会话行为不得因重设计改变。

#### `/report-processing` — Observable Pipeline

- `/report-processing` 与 `/prep`、`/interview`、`/reports`、`/help` 共用 Calm Cobalt 应用框架，不再使用企业绿整页背景、米色工作流侧栏、珊瑚状态线或深色任务卡。
- 白色主进度工作区必须直接显示真实百分比、当前阶段、后端消息、自适应同步状态和钴蓝进度轨道；前台轮询按已等待时间从 1 秒放缓到 2 秒、5 秒，页面隐藏时至少 15 秒一次，恢复可见后立即同步。百分比保持紧凑应用层级，不能膨胀为营销数字。
- 左侧四阶段轨道表达“准备 / 面试 / 生成 / 报告”产品生命周期；中央区域只展示当前阶段、已完成阶段、真实进度、公开消息与恢复动作。默认产品模式不展示事件历史，也不保留重复技术指标 Inspector。
- 不得用无限 Spinner 或抽象插画代替后端状态；Spinner 只作为真实轮询/处理中状态的辅助符号，失败、重试、降级和轮询停止必须同时用图标、文字和语义色说明。

#### `/report-detail` — Assessment Publication

- 产品阅读顺序固定为：结论与可靠性 → 最优先改进动作 → 五维能力 → 逐题依据 → 改进答案与知识证据 → 再练习。技术附录只在显式诊断模式出现。
- 总分采用封面级大字号；维度与结论使用不同列宽和强规则线，不得回到三个等权统计卡。
- 逐题反馈使用可展开的文章条目；评分依据、不足、更好回答、引用和评分证据形成正文分栏。
- 优势与改进是森林绿/暖珊瑚的对照跨页；证据区保持明亮可读；运行轨迹仅作为诊断模式中的深色技术附录。
- 保留章节 ID、章节导航、IntersectionObserver、PDF 下载和固定操作条。

#### `/reports` — Editorial Archive

- 搜索与排序始终先于聚合指标，避免用户先读到脱离筛选条件的数字。
- 统计区是细薄状态条；报告记录是出版索引条目，以岗位、摘要、状态、分数、时间和操作建立清晰阅读顺序。
- 筛选器是开放目录，不使用独立悬浮卡。空态、加载、失败、分页和重新排队必须维持真实行为。

#### `/help` — Recovery Manual

- `/help` 与 `/prep`、`/reports` 共用唯一 Calm Cobalt AppShell；正文采用单栏恢复手册和可达的页内目录，不保留没有独立业务价值的右侧检查器或静态底栏。
- 页内目录按“准备资料 / 进行面试 / 恢复会话 / 报告失败 / 草稿与数据”组织，入口与恢复说明放入真实正文上下文，不伪造运行状态。
- 恢复手册采用规则线、开放正文和克制语义图标，不使用巨型签名色入口、营销卡片、客服机器人、登录入口或外部支持承诺。

### 0.4 响应式与可访问性锁定

- 支持并验证 320、375、414、768、1024、1280 宽度；横向溢出必须保持裁切。
- 768 以下工作流轨道转为横向步骤带，面试三栏转为单列，报告章节导航可横向滚动。
- 触控控件最小高度 44px；可点击文本不得折行；标题、ID、URL、错误文本和长中文必须安全换行。
- 颜色不能成为唯一状态线索；焦点环、ARIA、语义标题、表单 label、progress 语义和键盘路径必须保留。
- 旧六页 HTML 不是实现来源；独立 Vite/React 前端是唯一页面实现。

本文件是 Interview Agent 的视觉与交互单一事实来源。任何新增页面、修改现有页面、增加组件或调整 CSS 的工作，都必须先阅读本文件。

它不是 Cohere 官网的复制版，也不是营销落地页规范。Cohere 只提供设计灵感：克制的企业 AI 气质、深浅环境切换、研究型信息排版、深色 Agent Console、矿物色表面和小范围珊瑚色强调。最终规则必须服务于本项目真实存在的功能与数据。

## 1. 产品背景与边界

### 1.1 产品是什么

Interview Agent 是一个本地单用户技术面试助手，核心闭环为：

1. 输入岗位 JD 与候选人简历。
2. 由 LLM 与 Knowledge Agent 生成面试计划、岗位标签、考察点与证据绑定。
3. 进行可恢复、可跳题、支持流式追问的模拟面试。
4. 异步执行逐题评审、知识证据复用、报告聚合与改进建议生成。
5. 阅读五维评分、逐题反馈、评分证据、RAG 引用与 Agent 运行轨迹。
6. 在报告中心检索历史报告，并下载 PDF。

### 1.2 当前运行页面

| 路由 | 页面文件 | 设计职责 |
|---|---|---|
| `/`、`/prep` | `frontend/src/pages/StartPage.jsx` | 在应用工作台内编辑资料、恢复草稿、生成计划、检查知识证据并开始面试 |
| `/interview` | `frontend/src/pages/InterviewPage.jsx` | 问题导航、实时对话、回答草稿、SSE 恢复、跳题、结束、轮次评审 |
| `/report-processing` | `frontend/src/pages/ReportProcessingPage.jsx` | 报告当前进度快照、阶段、公开安全消息与恢复动作；RAG 摘要和生成路径仅进入受控诊断，本轮不承诺事件历史 |
| `/report-detail` | `frontend/src/pages/ReportDetailPage.jsx` | 总分、五维能力、逐题反馈、证据、优势改进、运行轨迹、PDF |
| `/reports` | `frontend/src/pages/ReportsPage.jsx` | 报告统计、筛选、搜索、分页、查看或重新排队 |
| `/help` | `frontend/src/pages/HelpPage.jsx` | 使用说明、恢复协议和常用入口 |

### 1.3 产品边界

- 当前是本地单机、单用户产品，不设计登录、头像菜单、团队空间、权限角色或跨设备同步。
- 不展示后端没有返回的百分位、候选人排名、虚构 Worker 名称、分享链接或伪造统计。
- 不把知识库管理 UI、部署监控大盘或管理后台功能混入当前面试闭环。
- 不依赖 CDN、在线字体、远程图标库或必须联网才能显示的图片。
- 不为了视觉效果暴露简历、JD、回答原文、提示词、密钥、绝对路径或未经允许的运行时敏感信息。

## 2. 设计定位

### 2.1 核心概念

设计主题：**AI Assessment Lab / AI 技术评估实验室**。

用户应感受到：

- 这是一个严谨、可信、可解释的技术评估工具。
- AI 正在工作，但界面不会用夸张光效伪装智能。
- 每个评分和结论都能逐层追溯到问题、回答、评审和证据。
- 实时面试有专注感，报告阅读有编辑与出版物般的清晰度。
- 系统状态透明，失败和回退不会被隐藏。

### 2.2 三种视觉环境

#### A. Research Canvas / 研究画布

用于准备页、报告详情、报告中心和帮助页。

- 白色与暖矿物浅灰是主要表面。
- 用开放空间、细分隔线和规则化列表组织内容。
- 长文本以中文阅读舒适度优先，行高保持 1.6–1.7。
- 卡片只用于真正独立的对象，不把每段文字都包进卡片。

#### B. Agent Workspace / Agent 工作区

用于实时面试对话、Agent 状态、部分运行轨迹。

- 深海军蓝是实时推理和对话环境，不是全站暗色模式。
- 深色区域必须被浅色应用外壳包围，让用户知道自己仍在同一流程中。
- 技术标签、Session ID、命令 ID 与 Agent 名称使用等宽字体。
- 深色层级通过表面色、细边框和文字明度区分，不使用霓虹发光。

#### C. Pipeline Field / 流水线场域

用于报告生成页和知识检索、评审聚合等高状态密度区域。

- 深企业绿用于“系统正在执行”的主场景。
- 珊瑚色仅标记当前步骤、提醒或回退原因。
- 成功、警告、失败必须使用语义色、图形或文字共同表达。
- 进度必须来自服务端当前权威快照，不能只展示一个模糊 loading 动画；本轮没有持久化事件历史能力，产品 UI 不得把固定空 `events` 数组包装成历史记录。

### 2.3 色彩比例

- 70%：白色、暖浅灰、浅矿物表面。
- 20%：深海军蓝或深企业绿的 Agent / Pipeline 区域。
- 10%：操作蓝、珊瑚色与语义状态色。

珊瑚色不是主按钮色。操作蓝不是大面积背景。深色不是全站默认。

## 3. 设计原则

### 3.1 Evidence over decoration / 证据优先

视觉重点应落在问题、回答、得分、缺失项、改进答案、证据引用和运行路径上。装饰不能抢过这些信息。

### 3.2 Progressive disclosure / 渐进披露

默认先显示用户最需要的结果，再提供详细追溯：

```text
总分与结论
  → 五维能力
    → 逐题反馈
      → 评分证据与改进答案
        → RAG 引用与 Agent 运行轨迹
```

### 3.3 One dominant action / 单一主操作

每个视图最多只有一个视觉上最强的按钮：

- 准备页：生成面试计划或开始面试，取决于当前状态。
- 面试页：提交回答。
- 报告生成页：完成前是后台继续，完成后是查看完整报告。
- 报告详情页：返回报告中心或下载 PDF，根据任务上下文选择其一为主。

### 3.4 State is content / 状态本身就是内容

处理中、重试、回退、复用、失败和已完成不是辅助信息，而是产品可信度的一部分。不要用一个通用灰色 spinner 替代所有状态。

### 3.5 Calm density / 克制的信息密度

桌面端允许高信息密度，但必须通过对齐、字号、分组、分隔线和留白控制。禁止通过无限缩小字体来塞入更多内容。

### 3.6 Offline-native / 离线原生

设计必须在本地资源环境下完整成立。渐变、图形、图标、进度和图表应优先用 CSS、本地 SVG 或原生 HTML 实现。

## 4. Token 架构

所有实现使用三层 Token：Primitive → Semantic → Component。

```text
Primitive：原始颜色、间距、字号、圆角、阴影、时长
    ↓
Semantic：页面、文字、主操作、Agent、状态等用途
    ↓
Component：按钮、卡片、输入框、对话、报告行等组件专用值
```

组件 CSS 禁止直接写新的十六进制颜色。若需要新颜色，先在 Primitive 层定义，再映射到 Semantic 层。

### 4.1 Primitive Color Tokens

```css
:root {
  /* Neutral */
  --primitive-white: #ffffff;
  --primitive-stone-50: #fafaf8;
  --primitive-stone-100: #f6f6f3;
  --primitive-stone-200: #eeece7;
  --primitive-stone-300: #dcddd8;
  --primitive-stone-400: #bfc1ba;
  --primitive-stone-500: #9b9d97;
  --primitive-stone-600: #73756f;
  --primitive-stone-700: #4f514d;
  --primitive-ink-800: #21211f;
  --primitive-ink-900: #17171c;

  /* Agent navy */
  --primitive-navy-700: #17364f;
  --primitive-navy-800: #10283c;
  --primitive-navy-900: #071829;

  /* Pipeline green */
  --primitive-green-50: #edf8f3;
  --primitive-green-200: #bde2d5;
  --primitive-green-600: #17845e;
  --primitive-green-700: #116b4c;
  --primitive-green-800: #075446;
  --primitive-green-900: #003c33;

  /* Action blue */
  --primitive-blue-50: #eef3ff;
  --primitive-blue-200: #c8d8ff;
  --primitive-blue-500: #4c6ee6;
  --primitive-blue-600: #2457d6;
  --primitive-blue-700: #1d47b3;

  /* Coral accent */
  --primitive-coral-50: #fff0eb;
  --primitive-coral-300: #ffad9b;
  --primitive-coral-500: #ff7759;
  --primitive-coral-700: #b7472f;

  /* Semantic status primitives */
  --primitive-amber-50: #fff7e8;
  --primitive-amber-600: #b7791f;
  --primitive-amber-700: #8e5b13;
  --primitive-red-50: #fff1f0;
  --primitive-red-600: #c2413b;
  --primitive-red-700: #96332e;
}
```

### 4.2 Semantic Color Tokens

```css
:root {
  --color-page: var(--primitive-stone-100);
  --color-surface: var(--primitive-white);
  --color-surface-muted: var(--primitive-stone-200);
  --color-surface-blue: #f1f5ff;

  --color-text: var(--primitive-ink-800);
  --color-ink-strong: var(--primitive-ink-900);
  --color-ink-secondary: var(--primitive-stone-700);
  --color-muted: var(--primitive-stone-600);
  --color-ink-faint: var(--primitive-stone-500);

  --color-line: var(--primitive-stone-300);
  --color-control-border: var(--primitive-stone-400);

  --color-cta: var(--primitive-ink-900);
  --color-cta-hover: #2b2b31;
  --color-on-cta: var(--primitive-white);

  --color-primary: var(--primitive-blue-600);
  --color-primary-hover: var(--primitive-blue-700);
  --color-primary-strong: var(--primitive-blue-700);
  --color-primary-subtle: var(--primitive-blue-50);
  --color-primary-ring: var(--primitive-blue-200);
  --color-focus-ring: var(--primitive-blue-500);

  --color-agent: var(--primitive-navy-900);
  --color-agent-elevated: var(--primitive-navy-800);
  --color-agent-soft: var(--primitive-navy-700);
  --color-on-agent: #f7fbfa;
  --color-on-agent-muted: #b9cac5;

  --color-pipeline: var(--primitive-green-900);
  --color-pipeline-elevated: var(--primitive-green-800);

  --color-accent: var(--primitive-coral-500);
  --color-accent-strong: var(--primitive-coral-700);
  --color-accent-subtle: var(--primitive-coral-50);

  --color-success: var(--primitive-green-600);
  --color-success-strong: var(--primitive-green-700);
  --color-success-subtle: var(--primitive-green-50);
  --color-warning: var(--primitive-amber-600);
  --color-warning-strong: var(--primitive-amber-700);
  --color-warning-subtle: var(--primitive-amber-50);
  --color-danger: var(--primitive-red-600);
  --color-danger-strong: var(--primitive-red-700);
  --color-danger-subtle: var(--primitive-red-50);
}
```

### 4.3 Semantic Support Tokens

边框状态和组件状态使用以下支持 Token；它们仍属于语义层，不应在组件内部重复硬编码：

```css
:root {
  --color-success-border: var(--primitive-green-200);
  --color-warning-border: #efd29c;
  --color-danger-border: #edb8b5;
}
```

黑色 `--color-cta` 只承担当前视图的主操作；蓝色 `--color-primary` 只承担链接、选中、焦点附近反馈和流程强调。不要让一个 Token 同时承担按钮、焦点、选中状态和大面积背景四种职责。

### 4.4 Spacing Tokens

使用 4px 基础网格，允许 2px 与 6px 用于微调：

```css
--space-0: 0;
--space-0-5: 2px;
--space-1: 4px;
--space-1-5: 6px;
--space-2: 8px;
--space-2-5: 10px;
--space-3: 12px;
--space-4: 16px;
--space-5: 20px;
--space-6: 24px;
--space-8: 32px;
--space-10: 40px;
--space-12: 48px;
--space-16: 64px;
--space-20: 80px;
```

使用规则：

- 控件内部：8–16px。
- 紧凑卡片：16px。
- 普通卡片：20–24px。
- 大型 Agent / Pipeline 面板：24–32px。
- 页面横向边距：桌面 24px，平板 20px，移动端 12px。
- 页面章节之间：28–48px，报告正文优先使用分隔线而不是无限增加空白。

### 4.5 Radius Tokens

```css
--radius-xs: 4px;
--radius-control: 6px;
--radius-card: 10px;
--radius-panel: 14px;
--radius-feature: 20px;
--radius-pill: 9999px;
```

- 输入框、工具按钮、数据单元：6px。
- 普通内容卡片：10px。
- Agent Console、组合面板：14px。
- 大型进度场域或视觉主面板：20px。
- 主 CTA、状态筛选和短标签可以使用 pill。
- 禁止所有元素都变成 pill，也禁止卡片使用 24px 以上的消费应用式圆角。

### 4.6 Shadow Tokens

```css
--shadow-none: none;
--shadow-card: 0 1px 2px rgb(15 23 42 / 0.05);
--shadow-elevated: 0 12px 32px rgb(7 24 41 / 0.10);
--shadow-overlay: 0 20px 60px rgb(7 24 41 / 0.18);
```

- 默认卡片靠边框和表面差异分层。
- `shadow-elevated` 只用于浮层、粘性操作栏或被明确抬升的主面板。
- 深色 Agent 面板不使用外发光。

### 4.7 Motion Tokens

```css
--motion-fast: 160ms;
--motion-state: 200ms;
--motion-panel: 280ms;
--motion-slow: 420ms;
--motion-easing: cubic-bezier(.2, 0, 0, 1);
--motion-exit: cubic-bezier(.4, 0, 1, 1);
```

## 5. Typography

### 5.1 字体策略

默认不依赖在线字体。

```css
--font-display: "Space Grotesk", "Noto Sans SC", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
--font-body: "Noto Sans SC", "PingFang SC", "Microsoft YaHei", "Segoe UI", system-ui, sans-serif;
--font-mono: "JetBrains Mono", "Cascadia Code", SFMono-Regular, Consolas, monospace;
```

实现约束：

- 如果仓库没有本地字体文件，必须使用系统 fallback，不得偷偷引入 Google Fonts。
- `Space Grotesk` 只影响拉丁字符与数字；中文标题仍由中文字体渲染。
- Mono 只用于 ID、状态码、Agent、RAG、路径、版本和短技术标签，不用于中文正文。

### 5.2 产品级字号

| Token | 桌面 | 移动端 | 用途 |
|---|---:|---:|---|
| Display | 40px / 500 / 1.12 | 30px | 准备页主标题、报告结论大标题，极少使用 |
| Page title | 28px / 500 / 1.25 | 24px | 页面 H1 |
| Section title | 20px / 600 / 1.35 | 18px | 页面章节 H2 |
| Card title | 16px / 600 / 1.45 | 16px | 卡片标题、问题标题 |
| Lead body | 17px / 400 / 1.65 | 16px | 页面简介、报告摘要 |
| Body | 15px / 400 / 1.65 | 15px | 长文本、反馈、回答 |
| UI | 14px / 400 / 1.5 | 14px | 表单、按钮、导航 |
| Label | 12px / 600 / 1.4 | 12px | 元数据、状态、表头 |
| Micro | 11px / 500 / 1.4 | 11px | 非关键辅助信息，禁止承载核心内容 |

### 5.3 中文排版规则

- 报告正文最大行宽约 72 个中文字符或 760px。
- 对话气泡最大宽度 48rem，但移动端为 100%。
- 中文长文本行高不得低于 1.55；报告和回答建议 1.65。
- 标题使用字重 500–600，避免 700–800 的厚重 SaaS 标题。
- 不对中文使用夸张负字距。
- 英文技术标签可以使用小范围大写；中文标签不强制大写或增加宽字距。

## 6. Layout System

### 6.1 全局框架

- 顶部应用栏：桌面 64px，移动端 56px。
- 桌面内容最大宽度：1240px；报告中心允许扩展到 1360px。
- 报告详情左侧章节导航：220px。
- 准备页流程侧栏：232px。
- 面试页三栏：220–260px / minmax(540px, 1fr) / 260–300px。
- 任何主要内容区域都必须使用 `min-width: 0`，避免长 ID 或回答撑破网格。

### 6.2 响应式断点

| 断点 | 范围 | 行为 |
|---|---|---|
| Mobile | `< 768px` | 单列；隐藏全局导航链接；侧栏下移；操作按钮全宽或两列 |
| Tablet | `768–1023px` | 两列或窄三列；右侧上下文可折叠；卡片网格降为两列 |
| Desktop | `1024–1279px` | 完整工作流；缩小页面留白与侧栏宽度 |
| Wide | `>= 1280px` | 使用完整三栏面试与固定章节导航 |

当前 CSS 已实现 `<768px` 的核心移动端降级。后续增强应优先补充 768–1023px 平板态，而不是重新引入 `body { min-width: 1280px; }` 作为依赖。

### 6.3 页面节奏

- 准备页：标题 → 输入资料 → 状态/操作 → 计划与知识预热。
- 面试页：题目 → 对话 → 回答输入是主轴，左右侧栏只提供导航和上下文。
- 生成页：当前进度优先，历史事件其次，任务技术信息最后。
- 报告页：结论 → 能力 → 逐题反馈 → 改进 → 证据与运行轨迹。
- 报告中心：统计不是主角，搜索、筛选和记录列表才是主任务。

## 7. Core Components

### 7.1 App Topbar

- 高度 64px；白色背景；底部 1px 分隔线。
- 品牌标记可以使用深色方形或深绿色小型标记，不使用发光 Logo。
- 当前路由使用下划线、文字色或短条表示，不用大面积蓝色胶囊。
- 右侧状态仅显示真实可解释状态，例如“本地运行”“报告生成中”。
- 移动端保留品牌与当前页名称，隐藏非关键导航。

### 7.2 Workflow Sidebar

- 用于准备和生成阶段，说明用户处于面试闭环中的哪一步。
- 当前步骤使用操作蓝左边框、浅蓝背景和清晰的 `aria-current="step"`。
- 已完成步骤可使用成功图标或文字；不能只靠绿色。
- 说明卡使用浅矿物背景，避免再套一层高阴影卡片。

### 7.3 Buttons

#### Primary

- 近黑背景、白色文字、40px 高、pill。
- 仅用于当前页面最高优先级操作。
- Hover：背景变为 `#2b2b31`，可有 1px 向上位移，但不要缩放。
- Loading：保留按钮宽度，显示 spinner 与明确文字，例如“正在生成计划”。

#### Action / Selected

- 操作蓝用于选中、焦点、当前步骤和链接，也可用于需要保持现有兼容的提交按钮。
- 如果页面已经有近黑 Primary，则同一视图不要再出现第二个实心蓝色主按钮。

#### Secondary

- 白色背景、深色文字、1px 边框、6px 圆角。
- 用于保存草稿、上传文件、刷新、跳题、下载 PDF。

#### Destructive

- 默认白底红字红边，不使用大面积红色。
- 结束面试、重新排队等高影响操作需要明确文案和必要的确认。

#### Sizes

| Size | Height | Padding | Use |
|---|---:|---:|---|
| Small | 34px | 12px | 表格行、分页、紧凑筛选 |
| Default | 40px | 16–20px | 常规操作 |
| Large | 48px | 24px | 准备页主操作、空状态 CTA |

#### States

状态优先级：disabled → loading → active → focus → hover → default。

所有按钮必须有 `:focus-visible`，不能只设计 hover。

### 7.4 Inputs and Textareas

- 背景白色或浅矿物色；1px 边框；6px 圆角。
- 默认高度 40px；大输入 48px。
- JD、简历和回答 textarea 使用 15px、1.65 行高。
- Focus 使用 2px 操作蓝/紫蓝焦点环，并保持边框可见。
- Error 使用红色边框、图标和字段下方错误文本，不能只改变 placeholder。
- 文件上传按钮必须同时显示文件名、文件类型或“未选择文件”。
- 计数器和草稿状态属于辅助信息，不与字段标签争夺层级。

### 7.5 Cards and Open Sections

卡片分为四类：

| 类型 | 处理 | 用途 |
|---|---|---|
| App card | 白底、1px 边框、10px 圆角、低阴影 | 独立表单、统计、上下文 |
| Open section | 无外框，顶部/底部分隔线 | 报告长内容、研究列表 |
| Agent panel | 深海军蓝、14px 圆角 | 对话、Agent 状态 |
| Feature field | 深绿或浅矿物大面板、20px 圆角 | 报告生成、关键解释 |

禁止卡片套卡片再套卡片。若子内容只是段落或列表，使用分隔线和间距。

### 7.6 Tags and Badges

- Job tag：浅蓝、浅绿、浅矿物或珊瑚浅色；文字必须满足对比度。
- Status badge：必须与状态文字和图形组合。
- ID badge：使用 mono，背景低对比，不抢主内容。
- Pill 只承载 1–3 个短词；长原因、错误信息或回答摘要不能放在 pill 中。

### 7.7 Notices and Alerts

- Info：浅蓝背景 + 蓝色左边框。
- Success：浅绿背景 + 绿色图标/边框。
- Warning：浅黄背景 + 警告图标/标题。
- Danger：浅红背景 + 明确恢复建议。
- 提示出现时不清空页面已有内容。
- 异步错误必须说明用户可以做什么：重试、后台继续、返回报告中心或稍后查看。

### 7.8 Empty State

空状态包括：标题、原因、下一步；不只显示“暂无数据”。

示例：

```text
还没有面试报告
完成一次模拟面试后，报告会出现在这里。
[开始新面试]
```

不使用远程插画作为空状态依赖。可以用简单 CSS 图形、本地 SVG 或纯排版。

## 8. Interview-specific Components

### 8.1 Question Navigation

支持状态：`current`、`answered`、`skipped`、`unanswered`、`pending`。

| 状态 | 视觉 | 辅助表达 |
|---|---|---|
| current | 蓝色左边框、浅蓝背景 | `aria-current="step"`、文字“当前题” |
| answered | 成功绿编号或勾选 | 文字“已回答” |
| skipped | 警告色编号 | 文字“已跳过” |
| unanswered | 危险色或警告色 | 文字“未回答” |
| pending | 中性灰 | 文字“待进行” |

题目标题桌面端可单行省略，但悬停/聚焦后应可访问完整文本；移动端允许两行。

### 8.2 Current Question Banner

- 当前问题位于对话区上方，始终可见。
- 使用浅蓝或浅矿物背景，不要把完整问题放进小 pill。
- 显示题号、题型或考察点，但一次最多展示两个辅助标签。
- 问题正文 15–16px、600，允许换行。

### 8.3 Agent Console / Conversation Panel

推荐目标样式：

- `conversation-panel` 使用 Calm Cobalt 雾灰记录底与低对比冷灰边界。
- 面试官消息使用近白表面；候选人消息使用淡蓝操作表面。
- 文字使用深海军正文与中性辅助文字 token，正文对比度至少 4.5:1。
- Avatar 为 28–32px 的几何标记，不使用人物照片。
- 消息气泡圆角 10px，不使用聊天应用常见的大尾巴气泡。
- 每条消息保留角色标签：“AI 面试官”“你的回答”。
- 流式追问可以显示小型状态点和“正在生成追问”，但不得模拟不存在的思考内容。

禁止把对话记录重新扩展为整片深色控制台；深色只可作为极小的真实状态或品牌文字使用，不能承载大面积正文。

### 8.4 Answer Composer

- 回答区在视觉上与对话区分离，可使用白色面板。
- Textarea 最小高度 104px，桌面最大约 160px。
- 底部左侧显示草稿状态与字符数；右侧显示操作。
- “提交回答”是主操作；“跳过此题”和“结束面试”降低层级。
- 提交、跳题、结束后必须防止重复操作并展示 loading/busy 状态。
- 409 状态冲突应显示恢复说明，不能让用户以为回答丢失。

### 8.5 Focus Mode

- 专注模式隐藏题目导航和右侧上下文，主区最大宽度约 1040px。
- 切换按钮使用 `aria-pressed`。
- 切换不改变当前问题、不清除回答草稿、不滚动到错误位置。
- 状态转换 200–280ms；`prefers-reduced-motion` 下立即完成。

### 8.6 Round Review Status

- 轮次评审是辅助但真实的异步状态。
- 已评审数量、失败数量和当前处理状态可用短文本展示。
- 不展示虚构的实时分数，除非后端明确返回并允许在面试中显示。

## 9. Report Processing Components

### 9.1 Stage Model

支持以下阶段：

```text
queued
retrieving
analyzing
evaluating
aggregating
coaching
completed
failed
```

### 9.2 Processing Feature Field

- 主进度区域使用 Calm Cobalt 白色连续工作面，与中央阶段台账共享冷灰规则线，不再切换为深企业绿环境。
- 当前阶段标题与真实百分比共同构成首要层级；百分比建议 32–44px，阶段标题建议 19–26px，避免形成网页 Hero。
- 当前阶段使用淡钴蓝表面、钴蓝图标和“当前阶段”文字；不再使用珊瑚点、黑色粗线或发光强调。
- 已完成阶段使用绿色图标和“已完成”文字，失败阶段使用红色图标和“生成失败”文字，未开始阶段保持中性低对比但仍可读。
- 进度条使用禁用灰轨道和单色钴蓝进度段；更新只动画 `transform: scaleX()`，不使用渐变、光效或装饰性循环。

### 9.3 Pipeline Timeline

每个阶段至少包含：

- 阶段名称。
- 当前状态。
- 后端返回的消息。
- 若存在，时间或当前题目。

阶段完成后保留历史，不把前一步从 DOM 中移除。

- 每行左侧业务图标表达阶段职责，右侧状态图标与中文文字表达完成状态；不得用同一个通用圆点同时承担两种含义。
- 阶段标题不低于 15px，当前消息和阶段说明不低于 14px；题目 ID、时间和索引等辅助元数据可以使用 11–13px。
- 当前阶段只做一次性 160–240ms 状态进入，不使用循环呼吸；全页同时可见的循环运动只保留一个真实轮询 Spinner。
- 当前或失败阶段允许使用 2px 钴蓝/红色短锚点帮助扫视，但不得恢复贯穿列表的粗时间线；锚点只在状态变化时做一次 `scaleY` 进入。

### 9.4 Runtime Events and Metrics

- 运行事件使用规则化纵向列表，不必每条都成为卡片。
- 事件图标必须对应真实阶段职责；普通事件不得统一使用成功勾，只有完成事件才使用完成语义。
- 数字指标右对齐，使用 tabular number 或 mono。
- `report_path`、`knowledge_path`、复用数量与回退原因必须明确显示。
- `full_session_fallback` 使用 warning，而不是 error；它表示降级路径，不一定代表报告失败。

### 9.5 Polling and Retry

- 暂时性错误显示“正在重试”和下一次行为，不跳转到错误页。
- 重试时保留上一次有效进度，并在同步说明与 warning 中明确告知用户进度未被清空。
- `failed` 才进入危险状态，并提供报告中心或重新排队入口。
- 同步错误不得把后台任务误标为 `failed`；必须区分“无法获取最新状态”和“报告任务已经停止”。
- 完成后主按钮变为“查看完整报告”。
- warning 与 error 必须使用“短标题 + 原因/下一步”两层结构；提示面使用 1px 语义边界和图标，不使用粗彩色侧条、抖动或重复告警正文。

## 10. Report Components

### 10.1 Report Overview

报告第一屏只回答三个问题：

1. 总体表现如何？
2. 最强和最弱能力是什么？
3. 接下来最值得改进什么？

总分可以使用环形或大数字，但必须有文字等级和解释。禁止展示没有后端依据的百分位或排名。

### 10.2 Five Dimensions

固定维度：

| Key | 中文 |
|---|---|
| `breadth` | 知识广度 |
| `depth` | 技术深度 |
| `architecture` | 系统设计 |
| `engineering` | 工程实践 |
| `communication` | 表达沟通 |

首选原生 `<progress>` 条形图：

- 适合比较五个维度。
- 支持无障碍标签。
- 无需外部 Chart.js。
- 比雷达图更容易阅读精确差异。

颜色不能暗示 79 与 80 是完全不同的等级。分数颜色只表达宽泛区间，具体数值始终可见。

### 10.3 Question Feedback

桌面端允许表格作为摘要，但完整反馈应使用可展开的编辑型条目：

```text
Q01 · 系统设计                         82/100
问题文本

评分依据
主要不足
更好的回答
适用维度
评分证据
知识引用
```

- 表格横向滚动时必须保留可见焦点。
- 移动端优先切换为纵向条目，不强迫用户在 800px 表格中反复横滑。
- “更好的回答”使用成功语义但不整块绿色背景。
- “主要不足”使用 warning，不默认使用 danger。

### 10.4 Scoring Evidence

评分证据至少区分：

- 命中证据。
- 缺失项。
- 质量信号。
- 适用维度。

证据属于评估解释的一部分，默认可见摘要，详细列表可渐进展开。禁止把证据 ID 设计成比证据内容更醒目。

### 10.5 Knowledge Evidence

- 使用开放式两列列表或紧凑卡片，不用大型营销卡。
- 显示来源类型、标题、摘录和可用的 Evidence ID。
- 摘录行高 1.6，最长内容允许换行。
- `bound_evidence_ids` 等路径使用 mono 小标签。
- degraded 状态必须说明原因，不伪装成正常检索。

### 10.6 Strengths and Improvements

- 优势使用成功标记；改进使用 warning 标记。
- 两栏只在 >=1024px 使用；移动端单列。
- 每条建议尽量包含行为动词，例如“补充容量估算”“说明一致性取舍”。

### 10.7 Runtime Trace

- 运行轨迹属于高级解释层，视觉层级低于逐题反馈。
- Agent 执行和运行事件使用 mono ID、状态 badge 与键值对。
- 不显示未经脱敏的提示词、回答、简历、JD、密钥、绝对路径或 provider 原始错误。
- 失败记录应显示稳定错误分类与可采取动作，而不是巨量堆栈文本。

### 10.8 Sticky Report Actions

- 桌面端底部粘性操作栏可以使用轻微透明白背景与细边框。
- 移动端操作变为单列全宽。
- 粘性栏不能遮挡最后一段内容；报告主体必须预留底部空间。

## 11. Report Center Components

### 11.1 Overview Metrics

- 统计卡最多四个：全部、已完成、生成中、失败。
- 数字 24–32px；标题 12–14px。
- 失败统计可以使用危险色数字，但不把整张卡填红。
- 报告中心统计必须是浅色薄状态带；禁止复用报告生成页的深绿 Pipeline Field，也不使用四张悬浮卡。
- 图标仅承担状态辨识，数字使用 tabular numerals；失败只允许图标与数字使用危险色。

### 11.2 Filters and Search

- 状态筛选使用按压按钮或 segmented list，并保留 `aria-pressed`。
- 搜索输入与日期筛选是主要工具，位于列表上方。
- 当前筛选条件应可见，空结果应说明是“没有记录”还是“筛选无匹配”。
- DOM 与视觉顺序固定为：页面标题 → 搜索/日期工具 → 薄统计带 → 状态目录与记录列表。
- 桌面状态筛选保持开放式目录，不包进独立浮层卡；窄屏可重排为两列或四列按压按钮。

### 11.3 Report Table

- 文本左对齐、数字右对齐、状态居中或左对齐保持一致、操作右对齐。
- 默认行高 56px；内容多时允许更高。
- Hover 使用浅矿物色，不使用明显阴影。
- 状态使用文字 + 色彩。
- completed 行进入详情；processing 行进入进度；failed 行提供重新排队或错误说明。
- 小屏允许横向滚动，后续可改为卡片列表，但不能删除关键字段。
- completed 才显示数值评分与“综合评分”；processing 与 failed 必须改为等待/恢复说明，不显示 `-- + 综合评分` 这种伪数据组合。
- 每行只保留一个最明确的继续动作：completed 为查看报告，processing 为查看进度，failed 优先重新排队；PDF 下载保持低一级。
- 报告中心样式必须使用路由作用域，禁止 `.overview-strip`、`.report-row` 等通用选择器再次改变准备页或报告详情页。
- `/reports` 的台账内容容器不得挂载旧 `.report-ledger` 类；该遗留类带有出版物式 3–7px 黑色顶边。表头与内容之间只允许 `--start-color-rule` 的 1px 冷灰分隔线。
- 岗位标题桌面端不得低于 15px，摘要不得低于 13px；行内操作按钮可紧凑，但可见高度不得低于 32px，移动端不得低于 44px。
- 重新排队与 PDF 下载必须有独立忙碌态、文字变化和旋转图标，并在请求期间阻止重复提交；不能只在控制台记录进行中的操作。
- processing 可以使用慢速旋转图标或状态点呼吸动画表达持续运行，但一个模块只保留一个主要运动源，并必须服从 `prefers-reduced-motion`。
- 查看报告/进度的箭头可在 hover 时前移约 2px 作为方向反馈，禁止按钮整体缩放、弹跳或大幅位移。

### 11.4 Pagination

- 当前页使用深色或操作蓝选中状态。
- 按钮至少 34px，高对比焦点可见。
- 禁用上一页/下一页时保留按钮位置，避免布局跳动。

## 12. State Language

### 12.1 状态颜色语义

| 语义 | 色彩 | 使用 |
|---|---|---|
| Action | 操作蓝 | 焦点、当前步骤、链接、选中 |
| Agent active | 深海军蓝 | 实时对话、Agent Console |
| Pipeline active | 操作蓝 | 报告生成、当前阶段、真实进度 |
| Accent | 淡钴蓝 | 当前流水线步骤的低强度表面强调 |
| Success | 绿色 | 已完成、已回答、证据复用成功 |
| Warning | 琥珀色 | 跳过、回退、降级、部分不可用 |
| Danger | 红色 | 终止失败、不可恢复错误、破坏性操作 |
| Neutral | 矿物灰 | 待开始、未知、禁用、次要元数据 |

### 12.2 不依赖颜色

所有状态至少同时使用以下两种表达：

- 文字。
- 图标或形状。
- 色彩。
- ARIA 状态。

例如 `failed` 不能只把圆点变红；还要显示“生成失败”和恢复操作。

### 12.3 Loading

- 页面首次加载：骨架或稳定的状态文本。
- 按钮操作：按钮内 spinner + 文字，保留尺寸。
- 流式回答：消息区域内状态，不覆盖整个页面。
- 报告生成：真实进度、阶段与事件，不使用无限 spinner 作为唯一反馈。
- 报告筛选使用请求序列或取消机制，快速切换时旧请求不得覆盖新结果。
- 多行 Skeleton 应使用轻微错峰，而不是所有占位完全同步闪烁。

### 12.4 Disabled

- 使用真实 `disabled` 属性。
- 视觉透明度约 0.55，但文字和轮廓仍需可辨。
- 不通过 `pointer-events: none` 代替语义禁用。

### 12.5 Feedback and Recovery

- 页面级加载失败只能出现一个主要恢复面板；禁止同时渲染顶部危险提示和正文危险空状态造成重复告警。
- 行内操作失败使用可关闭的轻量提示；成功提示允许在约 5 秒后自动收起。
- 错误提示必须持续存在，直到用户处理、关闭或触发下一次请求。

## 13. Motion and Micro-interactions

### 13.1 时长

| Token | 时长 | 用途 |
|---|---:|---|
| Fast | 160ms | hover、颜色、边框、按钮反馈 |
| State | 200ms | 状态切换、进度更新、badge 更新 |
| Panel | 280ms | 折叠面板、专注模式、移动端侧栏 |
| Slow | 420ms | 首次页面进入或大型结果出现，谨慎使用 |

### 13.2 动效规则

- 元素进入使用 ease-out；离开使用 ease-in。
- 只动画 `transform` 和 `opacity`，进度条可动画 `width`。
- 卡片 hover 不做明显放大；最多 `translateY(-1px)`。
- 禁止弹簧、弹跳、粒子、光标追踪和持续背景动画。
- 流式文本按块更新，不对每个字做逐字打字动画。
- Pipeline 当前状态可使用 1.8–2.4s 的低幅度呼吸，但只动画 opacity。
- `/report-processing` 的 Calm Cobalt 覆盖禁用装饰性呼吸；同一区域只保留真实 Spinner、百分比/消息一次性淡入和进度段状态更新三类功能性动效。
- `/reports` 同步轨道固定预留 2px 高度，加载时只让单色进度段使用 `translateX` 循环；出现和消失不得改变命令栏或报告列表位置。
- 报告筛选确认、数字更新、提示进入、分页选中和空/错状态进入使用 160–260ms 的一次性 opacity + transform，不使用弹簧或过冲。
- 刷新、重新排队、下载、查看和新建面试的 hover 反馈只移动或旋转按钮内部图标；按钮容器不得整体缩放。
- 报告页同时可见的循环运动只允许：真实请求 Spinner、同步进度段、当前 processing 状态。静态完成、失败和普通统计不得持续运动。
- 报告行错峰最多约 24ms/行，Skeleton 最多约 70ms/行；错峰只用于建立阅读顺序，不能形成明显波浪或等待感。

### 13.3 Reduced Motion

`prefers-reduced-motion: reduce` 下：

- 页面进入动画关闭。
- 所有交互过渡降至 0.01ms。
- 呼吸动画关闭。
- 进度仍需通过文字和数值表达。
- `/reports` 的进度段、processing 旋转、状态点呼吸、数字更新、活动栏选中和按钮图标过渡均降至 0.01ms；布局、状态文字与操作能力保持不变。

## 14. Accessibility

### 14.1 标准

- 最低目标 WCAG 2.1 AA，并向 2.2 AA 靠拢。
- 正文文字对比度至少 4.5:1。
- 大文字和 UI 边界至少 3:1。
- Focus indicator 与相邻颜色至少 3:1。

### 14.2 Focus

```css
:focus-visible {
  outline: 2px solid var(--color-focus-ring);
  outline-offset: 2px;
}
```

- 深色 Agent 面板内使用更浅的蓝紫焦点环。
- 不允许 `outline: none` 后没有替代焦点样式。
- 滚动容器中的链接、按钮和表格操作必须能被键盘访问。

### 14.3 Keyboard and ARIA

- 当前页面导航使用 `aria-current="page"`。
- 当前问题使用 `aria-current="step"`。
- 报告章节使用 `aria-current="location"`。
- 筛选与专注模式使用 `aria-pressed`。
- 异步状态使用 `role="status"`、`aria-live="polite"` 或必要时 `role="alert"`。
- Loading 按钮使用 `aria-busy="true"`。
- Error 输入使用 `aria-invalid` 与 `aria-describedby`。

### 14.4 Touch and Mobile

- 主要触控目标至少 44×44px。
- 紧凑分页最低 34px，但相邻间距要补足触控区域。
- 移动端不依赖 hover 才能发现操作。
- 表格横向滚动区域使用可见提示和 `overscroll-behavior-inline: contain`。

### 14.5 Content Accessibility

- 图标不能代替文字。
- 分数必须有数字，不只用颜色条。
- 错误文案说明原因和下一步。
- 空状态区分“无数据”“加载失败”“筛选无匹配”。

## 15. Page Specifications

### 15.1 Prep Page

目标：让用户感觉正在建立一次有依据的面试，而不是填写普通后台表单。

- 主区域采用浅色 Research Canvas。
- 桌面使用输入区 + 计划预览双栏。
- JD 和简历是两个独立但视觉一致的字段面板。
- 岗位标签在生成计划后出现；生成前显示中性等待状态。
- 计划题目按顺序排列，题号、题型、考察点和证据摘要形成清晰层级。
- Knowledge Agent 预热使用浅绿或浅蓝区域，可展示 degraded/keyword 等真实路径。
- 生成计划完成后，“开始面试”成为唯一主 CTA。

### 15.2 Interview Page

目标：营造专注、稳定、可恢复的实时评估环境。

- 保留左题目导航、中主区、右上下文三栏。
- 顶栏、题目导航、中央工作区、Inspector 与状态栏统一采用 Calm Cobalt 应用语言；中央对话区为浅色记录工作面。
- 当前问题在对话区上方的近白 banner 中只展示一次，并与题号和考察点形成清晰层级。
- 回答输入保持独立白色边界，与记录底和消息表面明确区分。
- 右侧只显示进度、考察点和轮次评审，不堆放无关统计。
- 专注模式隐藏两侧栏但保留所有业务状态。

### 15.3 Report Processing Page

目标：把异步等待转化为透明、可信的执行过程。

- 页面使用与准备、面试、报告中心和帮助页一致的 Calm Cobalt 应用框架：白色品牌顶栏、四阶段任务轨道、冷雾灰底色、中央执行工作区、右侧 Inspector 和底部状态栏。
- 主进度模块属于应用工作区而不是营销 Hero；当前百分比、阶段和消息是首要层级，钴蓝为唯一主要执行色。
- 阶段历史和运行事件在同一白色工作区中以连续台账展开，只用 1px 冷灰规则线分隔；禁止卡片套卡片、粗黑线、珊瑚顶边和整块深色任务面。
- 阶段左侧使用排队、检索、分析、评审、聚合、建议、报告等业务图标，右侧单独使用完成/当前/等待/失败状态图标；运行事件沿用对应阶段图标，避免把普通过程误表达为成功。
- Inspector 固定展示任务 ID、生成路径、工作流、当前题目、评审数量、匹配片段、来源类型和真实恢复提示；长 ID 与错误文本必须安全换行。
- Inspector 内部模块标题直接使用图标 + 标题，不重复堆放“任务信息 / 生成指标 / 上下文”等同义小标签；技术 ID 采用纵向定义列表，普通指标保持左右对齐。
- 完成前“查看完整报告”保持真实 `disabled` 次级状态，并在按钮上方持续显示禁用原因；完成后才成为唯一主操作；“返回报告中心”始终为次级返回动作。
- 未完成时查看按钮使用锁图标，完成后才切换为向右箭头；解锁只做一次 200ms opacity + `translateX(-2px)`，按钮容器不得缩放。
- 回退使用 warning，失败使用 danger，完成使用 success；颜色必须与图标和中文状态文字同时出现。
- 进入动效使用 320ms opacity + `translateY(6px)`；百分比、阶段消息和状态更新使用 160–240ms opacity + `translateY(4px)`；真实轮询 Spinner 为 1000ms linear。`prefers-reduced-motion` 下全部降至 0.01ms。

### 15.4 Report Detail Page

目标：像阅读一份高质量技术评估，而不是查看杂乱的 BI 仪表盘。

- 左侧章节导航保持粘性。
- 首屏为结论、总分、五维摘要和关键亮点。
- 逐题反馈采用开放式条目与分隔线，卡片仅用于独立证据或运行对象。
- 五维使用条形图而非雷达图。
- 运行轨迹放在最后，默认视觉层级最低。
- Sticky actions 不遮挡正文。

### 15.5 Reports Page

目标：快速找到一次历史面试并继续处理。

- 搜索与筛选优先于装饰性统计。
- 记录列表保持高信息密度与清晰列对齐。
- 状态决定主要操作：查看报告、查看进度、重新排队。
- 无结果时解释当前过滤条件。
- 页面采用浅色 Calm Cobalt 应用档案台，不使用营销 Hero、深色整块统计、奶油纸张或出版物条纹背景。
- 顶部始终只保留一个页面级主按钮“开始新面试”；刷新、搜索、下载和分页均为次级或三级操作。
- `/reports` 与 `/prep` 必须共用同一种应用框架语言：白色品牌顶栏、三项主导航、冷雾灰应用底色、紧凑活动栏、中央工作区、右侧检查器和底部状态栏。
- 报告页左侧活动栏负责状态切换，中央区域负责搜索与记录台账，右侧检查器负责统计、当前筛选与操作说明；不得重新退化为传统网页标题 + 卡片列表。
- `/reports` 的控件高度、字体族、钴蓝主按钮、面板圆角、边框、阴影、焦点环和语义状态色必须直接使用准备页的 `--start-*` token，不再维护第二套近似颜色。
- 报告工作区标题、搜索工具和当前筛选属于一个连续头部：标题说明桌面端优先保持单行，报告数量必须合并为一个矩形计数控件；搜索栏与当前筛选共享同一白色表面，只用一条 1px 内部分隔线连接。
- 同步进度必须绝对定位覆盖在查询工具的内部边界上，不能作为独立横栏增加第三条分隔线或引发布局位移。
- 当前筛选必须按“状态 / 日期 / 关键词”显示字段名和值；禁止退化成图标、粗体状态、日期和关键词四段无结构的散落文本。
- 报告标题、查询工具和筛选摘要的外边界由同一个工作区头部承担；标题与查询工具之间不得再增加横向分隔线。搜索、日期与搜索动作必须组合成一个复合控件，内部只保留必要的单线分段，禁止并排堆放三个同权描边框。
- 当前筛选使用紧凑的字段名/字段值定义列表，不使用逐项竖线、胶囊或独立小卡；刷新作为查询区的辅助工具保持透明或近白表面，不与搜索动作争夺层级。

### 15.6 Help Page

目标：提供简明入口和故障恢复，不制作营销式帮助中心。

- 页面必须表现为应用内 Recovery Workbench，而不是独立帮助网站：使用与准备页、报告页一致的 `start-*` App Shell、控件高度、字体、边界、钴蓝强调和状态栏。
- 中央工作区按操作指南、恢复手册、数据边界三个 Pane 原位切换；活动栏按钮保留 `aria-pressed` 与 `aria-controls`，Pane 保留 `role="tabpanel"` 和 `aria-labelledby`。
- 操作流程和恢复场景使用开放列表、规则线与单层内容面，不使用等权卡片网格、巨型入口色块或卡片嵌套。
- 右侧检查器显示当前章节、真实继续入口和恢复判断顺序；整个页面只允许一个主操作“开始新面试”，报告中心保持次级。
- 帮助页条目标题桌面端不得低于 15px，说明正文不得低于 14px；活动栏选中图标优先使用 Phosphor `duotone`，未选中使用 `regular`，避免 20px 小图标因全实心填充形成黑块。
- 帮助页动效预算只允许三类：章节上下文使用 180ms `opacity + translateX(4px)`，列表使用 220ms `opacity + translateY(8px)` 且每行最多错峰 28ms，按钮使用 160ms 内部图标位移或旋转。禁止循环装饰动画、整按钮缩放、弹跳、发光和布局属性动画。
- 恢复场景的 success / warning / danger 颜色只作用于语义图标和短代码；行背景保持白色或统一悬停浅蓝，禁止将失败行整块铺红。桌面紧凑入口至少 40px，移动端至少 48px。
- 内容优先覆盖：如何准备、如何恢复草稿、如何处理中断、如何查看报告、如何处理失败任务。
- 不引入聊天客服、登录入口或云端支持承诺。

## 16. Do and Don't

### Do

- 使用白色研究画布与深色 Agent / Pipeline 场域形成节奏。
- 用大标题、开放空间和规则化列表增加视觉丰富度，而不是增加更多卡片。
- 让证据、状态和数据来源可追溯。
- 用 mono 标签表达 Agent、RAG、Session、版本和路径。
- 在深色面板外保留浅色应用外壳。
- 优先使用原生 HTML、CSS 和本地资源。
- 保持 DOM ID、API 字段和测试依赖稳定。

### Don't

- 不把全站改成深色模式。
- 不使用玻璃拟态、强模糊、霓虹外发光或彩虹渐变。
- 不在普通 UI 背景上铺大面积渐变；渐变只能作为非必要的媒体装饰。
- 不使用 60–96px 的营销页英雄标题塞满应用页面。
- 不使用卡片套卡片套卡片。
- 不用珊瑚色代替错误红，也不用绿色装饰普通内容。
- 不虚构实时 Agent 思考、评分、排名、百分位或后端不存在的数据。
- 不通过极小字体提高信息密度。
- 已退休的 `app/static/` 不得重新生成或恢复为产品前端。

## 17. Implementation Contract

### 17.1 Source Files

- 独立前端根目录：`frontend/`；开发端口为 `5173`，生产预览端口为 `4173`。
- React 入口：`frontend/src/main.jsx` 与 `frontend/src/App.jsx`。
- 页面组件：`frontend/src/pages/*.jsx`。
- 共享组件：`frontend/src/components/*.jsx`。
- API 客户端：`frontend/src/api/client.js`；开发时由 Vite 代理 `/api` 到 FastAPI。
- 设计系统 CSS：`frontend/src/styles/tokens.css`、`styles/base.css`、`styles/components/` 与按路由懒加载的 `styles/pages/`。
- 构建输出：`frontend/dist/`；构建产物带 hash，不直接编辑。
- FastAPI 是 API-only 服务，不再提供 `/prep`、`/interview`、`/reports` 等页面路由。
- 旧 `app/test*.html` 与 `app/static/` 已删除并退休，不属于当前运行契约，也不得作为新组件模板恢复。

### 17.2 Build

```powershell
npm run build:frontend
```

独立开发服务使用：

```powershell
npm run dev:frontend
```

不得恢复 Tailwind CDN、在线字体或由 FastAPI 拼接 HTML 的旧模式。

### 17.3 Stable Contracts

- 不随意更改公开路由、查询参数、API 字段和浏览器存储键。
- React 组件必须保留语义地标、标签关系、ARIA 状态和键盘路径。
- 不改变路由与 `session_id` 查询参数契约。
- 不把真实接口字段替换为静态演示数据。
- 不修改五维字段定义。
- 组件可以重构，但 `AppShell`、页面级状态属性、API 客户端错误语义和测试依赖的可访问名称必须保持稳定。

### 17.4 Runtime Data Contracts

设计不得以静态演示值替代以下真实数据契约：

| 页面 | 真实数据来源 | 必须保留的状态或字段 |
|---|---|---|
| 准备页 | `POST /api/prep`、草稿接口 | `job_tags`、计划题目、Knowledge Agent 路径、生成/保存/恢复错误 |
| 面试页 | 会话快照、回答/跳题/结束接口、SSE | 当前题目、题目状态、`session_id`、断线恢复、待提交回答与终止事件 |
| 报告生成页 | `report` 与 `report/progress` | `queued/retrieving/analyzing/evaluating/aggregating/coaching/completed/failed`、百分比、`last_updated_at`、公开安全消息、RAG 摘要；不包含虚构事件时间线 |
| 报告详情页 | 完整报告、题目评审、Agent runs、PDF | 总分、五维分数、逐题反馈、证据、`full_session_fallback`、下载可用性 |
| 报告中心 | `GET /api/reports` | `items`、`total`、`limit`、`offset`、`status_totals`、查询/日期/状态筛选 |

`GET /api/reports` 的 `status_totals` 必须由与当前搜索和日期条件相同的数据集合聚合，并同时返回 `all`、`processing`、`completed`、`failed`。状态卡不能通过额外四次列表请求推算，也不能显示脱离筛选上下文的假统计。

每个异步页面都必须暴露稳定的页面状态属性，供 CSS、无障碍语义和自动化测试共同使用：

- 准备页：`data-prep-state`。
- 面试页：`data-interview-state`、`data-review-state`、`data-interview-phase`。
- 报告生成页：`data-report-state`、阶段节点的 `data-state`。
- 报告详情页：`data-report-state="completed|fallback|error"`。
- 报告中心：`data-reports-state="loading|ready|empty|error"`。

`hidden` 属性是 DOM 显隐契约；共享样式必须保留 `[hidden] { display: none !important; }`，避免布局样式意外覆盖隐藏状态。

### 17.5 Token Migration Order

1. 在 `:root` 增加 Primitive Token。
2. 把现有扁平 Token 映射到 Semantic Token。
3. 增加按钮、卡片、Agent、Pipeline、表格等 Component Token。
4. 一次迁移一个页面，避免全局视觉和测试同时失控。
5. 构建 CSS，运行静态 UI 测试和浏览器测试。

### 17.6 Verification

至少执行：

```powershell
npm run build:frontend
& 'F:\python3.11\python.exe' -m pytest tests/test_static_report_ui.py -q
```

高风险视觉修改还应执行：

```powershell
$env:STAGE41_PYTHON='F:\python3.11\python.exe'
npm run test:browser
```

## 18. Agent Workflow

任何编码 Agent 在修改 UI 前必须：

1. 阅读本 `DESIGN.md`。
2. 确认修改影响的路由与业务状态。
3. 检查现有 DOM、ARIA、JS 和测试契约。
4. 从 Token 中选值，不在组件中创造新的硬编码视觉值。
5. 保留无数据、加载、失败、重试、回退和完成状态。
6. 检查 375px、768px、1024px 与 1280px 视口。
7. 检查键盘焦点与 reduced motion。
8. 构建生成 CSS 并运行相关测试。

## 19. Definition of Done

一个符合本设计系统的 UI 修改必须同时满足：

- 与 Interview Agent 的真实任务和数据匹配。
- 视觉上能识别 Research Canvas、Agent Workspace 或 Pipeline Field 的环境角色。
- 使用三层 Token 或现有兼容 Token。
- 没有新增远程运行依赖。
- 中文长文本可读，核心字号不低于 14px。
- 所有交互具有 hover、focus、active、disabled 和必要的 loading 状态。
- 状态不只依赖颜色。
- 移动端不丢失核心功能。
- 不破坏 API、DOM、ARIA 和测试契约。
- 相关构建与测试通过。

## 20. Known Gaps and Future Extensions

- 当前设计规范不包含知识库管理 UI；未来应单独定义上传、切分、索引、检索预览和删除流程。
- 当前不定义完整全站暗色模式；深色只用于 Agent 与 Pipeline 场域。
- 当前报告图表以原生进度条和数值为主；如未来增加复杂图表，依赖必须本地化并补充无障碍表格替代。
- 当前字体以系统 fallback 为基础；若引入 Space Grotesk、Noto Sans SC 或 JetBrains Mono，应将授权允许的字体文件纳入本地静态资源。
- 平板断点的细节仍需在实际浏览器验收中迭代。
- 六个运行路由已迁移到独立 Vite/React 服务，并统一到 Calm Cobalt 应用工作台。后续修改必须在 React 组件、`frontend/src/styles/base.css`、`styles/components/` 和对应 `styles/pages/` 文件中完成，不得恢复 FastAPI HTML 页面或扩展旧静态脚本。

## 21. Product Experience Invariants — Gate 0B

本节覆盖前文中仍带有“运行工作台”倾向的旧描述。出现冲突时，以本节和 `docs/frontend-product-experience-contract.md` 为准。

### 21.1 Truth and authority

- 服务端快照是 PrepPlan、面试会话、报告进度和报告内容的唯一权威来源；浏览器存储只保存恢复引用、待提交草稿和幂等命令，不得被当作业务事实。
- 用户在 `/prep` 查看并确认的权威计划必须就是启动会话使用的计划。启动接口不得再次调用计划生成模型。
- 所有题目、证据、分数、阶段、百分比、错误、重试能力和可靠性字段均来自真实接口；缺失时显示诚实空状态，不使用示例值补齐。
- SSE 中断、版本冲突、重复 command、RAG 降级、报告失败和 bootstrap 未就绪不得伪装成成功。
- 产品不承诺当前不存在的持久化报告事件历史。报告进度只展示 `stage`、`percent`、`last_updated_at` 和公开安全消息。

### 21.2 Product and diagnostic information

- 默认产品模式只显示用户完成任务所需的状态、结果、证据和下一步。
- Worker、workflow engine、job ID、attempt、heartbeat、stalled/orphaned、Agent runs 和 runtime events 属于诊断信息，只能在显式构建能力 `VITE_SHOW_RUNTIME_DIAGNOSTICS=true` 时请求和渲染。
- 普通产品请求不得为了隐藏后再展示而预取诊断资源。诊断资源失败不能阻断报告正文。
- Provider 原始错误、提示词、堆栈、绝对路径、数据库信息、JD/简历正文、完整证据片段和恢复凭证不得进入产品错误、日志、事件或埋点。

### 21.3 Single application shell

- `frontend/src/components/AppShell.jsx` 是六个正式 React 路由唯一的应用壳层所有者；不得创建第二套 AppShell、平行顶栏或页面级全局导航。
- AppShell 负责桌面导航、移动导航、跳到主要内容、页面状态槽位和恢复入口；页面只提供当前路由的主内容、局部导航和局部状态。
- 移动端隐藏桌面导航时必须提供功能等价入口；360–900px 不得出现跨页导航断点。
- 六页迁移期间允许 AppShell 暂时输出现有 `.start-app-*` / `.start-nav` DOM 和类名。迁移完成后必须删除无人使用的旧 `.app-*` 壳层规则。

### 21.4 Safety and accessibility

- 跳题和结束面试在确认前不得发送写请求。退出并稍后继续不得结束会话或生成报告。
- 确认对话框必须使用语义 dialog、焦点圈定、Escape 关闭、取消后的焦点恢复和清晰的主次操作；结束面试不能依靠普通浏览器 `confirm()`。
- 整个对话历史不得挂载 live region。只允许短状态消息使用专用 `aria-live`；流式 token 不逐 token 播报。
- 键盘焦点始终可见但不使用突兀的默认蓝色矩形；焦点样式必须与 Calm Cobalt token 一致，并同时满足对比度。
- 所有核心触控目标最小 44px；200% 缩放、reduced-motion 和跳过链接必须保持可用。

### 21.5 Stable product state

- 页面不得依据数组长度、英文文案或空对象推断业务状态。PrepPlan、Launch、Report reliability 和错误码使用冻结的稳定枚举。
- `answered_question_count` 只表示权威会话快照中 `answer_state == "answered"` 的题数，不表达回答质量。
- `last_updated_at` 是报告进度现有公开时间字段；不得在没有版本迁移的情况下平行引入 `updated_at`。
- 前端不得使用 `Date.now()`、自增值、截短 UUID 或 `Math.random()` 生成公开恢复/命令 ID。
- 任何视觉优化不得改变幂等键、版本比较、题目身份、会话映射、错误码、恢复凭证清理顺序或诊断能力边界。

### 21.6 Phase 4 report product mode override

- `/report-processing` 默认只显示当前阶段、已完成阶段、真实百分比、公开安全消息、`last_updated_at`、已等待时长、“可以离开，报告会继续生成”的真实说明，以及失败、重试或返回报告中心等可用恢复动作。当前没有持久化事件历史时，不得渲染空事件面板或承诺未来事件。
- `report_job_id`、attempt、heartbeat、workflow engine、knowledge path 和 report path 只在 `VITE_SHOW_RUNTIME_DIAGNOSTICS=true` 时渲染；报告中心默认也不得把 report path 作为列表标签展示。
- `/report-detail` 默认不得请求或渲染 `agent-runs`、`runtime-events`、job ID、attempt、heartbeat、workflow engine 等诊断资源。只有 `VITE_SHOW_RUNTIME_DIAGNOSTICS=true` 时才允许按需请求；诊断失败不得阻断报告正文、PDF 或练习入口。
- `/report-detail` 必须使用服务端 `reliability` 展示覆盖度、生成路径和降级影响。旧报告缺少该字段时显示兼容态并降低总分视觉权重，且固定声明“这是本轮模拟表现分，不代表录用概率。”
- `/reports` 在没有真实选中报告或独立上下文时移除重复 Inspector 与静态底栏。筛选摘要、权威有效回答数和恢复动作进入主工作区；不得从 feedback 数组长度推导有效回答数。
- `/help` 是单栏恢复手册，不显示“手册就绪”“可用”等伪运行状态，不保留重复 Inspector 或静态底栏。草稿与数据说明必须匹配当前 runtime 能力。
- 上述四页仍必须复用 `frontend/src/components/AppShell.jsx`、Calm Cobalt token、主导航、移动导航与跳到主要内容能力；本覆盖不授权创建第二套应用壳层。
