# Interview Quality V1 — T56 自动审查

## 结论

T56 Engineering 为 **PASS**，自动审查为 **PASS**，Quality 为 **NOT_REQUIRED**，真实外部 Provider 调用数为 **0**。

本阶段在既有 Plan Revision、`InterviewPlanEditor`、`ProviderPlanRegenerator` 和唯一 revision store 之上，实现难度、目标时长、面试重点和题型配比的安全预设。配置会冻结到 revision snapshot，并参与 plan hash；它只影响出题计划，不改变评分 rubric。配置改变后，当前计划会显式进入 stale 状态，用户必须确认重新生成，成功获得服务端新 revision 后才能开始面试。

## 配置契约

配置项与有限值域如下：

- 难度：`foundation`、`intermediate`、`advanced`；
- 目标时长：15、30、45、60 分钟；
- 重点：`technical_depth`、`system_design`、`project_review`、`balanced`；
- 题型预设：`balanced`、`technical`、`architecture`、`project`；
- 目标时长对应主问题数：15 → 3、30 → 5、45 → 7、60 → 9；
- `max_followups_per_question` 固定为 2，UI 不提供任意数值或百分比输入；
- `expected_followup_budget` 默认等于主问题数；
- `question_mix_preset` 仅为 UI 元数据，不发送到后端；后端接收的是完整、安全的 `question_type_budget`。

前端配置 payload 固定包含：

- `difficulty`；
- `target_duration_minutes`；
- `focus_preset`；
- `question_type_budget`；
- `expected_followup_budget`；
- `max_followups_per_question=2`；
- `generator_version`；
- `followup_policy_version`。

后端使用 Pydantic 的有限枚举、字面量和额外字段禁止规则验证配置。组合矩阵覆盖 3 种难度 × 4 种时长 × 4 种重点 × 4 种题型预设，共 **192** 个合法组合；非法时长 25、追问上限 3、难度 `expert` 和重点 `freeform` 均被拒绝。

## UI 与 stale 行为

开始页新增配置工作区，以 radio group 和安全预设呈现所有可选项，不开放数字输入、range 控件或自由百分比。页面显示：

- 预计主问题数；
- 目标分钟数；
- 预计追问数；
- 单题最多 2 次追问；
- “实际时长取决于回答长度、追问和操作节奏”的非承诺性说明；
- “配置只影响出题，不改变评分 rubric”的边界说明；
- 当前配置与 revision 同步或待重新生成状态；
- 各题型的安全数量摘要。

存在计划时修改配置会保留当前 revision 的冻结 snapshot，把页面标记为 stale，禁用开始按钮，并要求用户明确执行“应用配置并重新生成”。确认界面会列出手工调整数量、未保存草稿数量以及发生变化的配置字段。重新生成成功后只采用服务端返回的新 plan、revision、hash 和 configuration；失败时保留本地配置。

如果用户把设置改回当前 revision snapshot，stale 会立即解除，不要求无意义的重新生成。页面刷新后会自动恢复匿名 draft、其绑定的 active revision 和 revision snapshot 配置；`localStorage` 只保存经过 allowlist 和安全预设重建的数据。

历史 revision 可能含合法但不属于当前四种 UI 预设的精确题型计数。自动审查发现后，恢复逻辑改为保留该 `question_type_budget` 和 `expected_followup_budget`，并显示“当前 revision”，不会把它误推断为 `balanced` 或制造假 stale。只有用户主动选择新时长或新预设时才切换到当前安全预设。

## 服务端 revision 与权限边界

全量重新生成请求新增可选 `configuration`，同时要求：

- `confirmed=true`；
- `expected_revision`；
- `request_id`；
- 禁止未声明的额外字段。

未传配置时仍复用当前 frozen snapshot，保持已有调用兼容。显式配置变更仍复用 `ProviderPlanRegenerator`、`InterviewPlanEditor`、`InterviewPlanRevisionStore` 和 `create_next_revision`，没有新增第二套 revision 创建链路。新配置写入 `InterviewPlanV2.configuration_snapshot`；revision snapshot 与 plan snapshot 一致；plan hash 覆盖配置；审计只记录配置字段的前后 hash；相同 request ID/hash 的幂等重放返回相同 revision，不重复调用 Provider。

自动审查发现，若简单放开 editor 的配置一致性检查，领域服务调用者可能默认注入配置已变化的 regenerate plan。现已改为服务端显式能力：`InterviewPlanEditor.apply(..., allow_configuration_change=False)` 默认仍拒绝 `configuration_mismatch`，只有已经过确认的服务端配置重新生成路由才传入 `allow_configuration_change=True`。公开 PATCH 仍不允许客户端提交 `regenerate_question`、`regenerate_all` 或 Provider 输出。

## 唯一启动入口的 latest gate

自动审查还发现 `/api/interviews` 原先只验证请求中 revision 自身的编号和 hash，没有再次确认它仍是 plan family 的 latest。直接 API 调用可能绕过前端 stale/conflict 门禁并用历史 revision 创建新 session。

唯一启动入口 `app.api.routes.start_interview` 现在会读取传入 revision，再读取 family latest。二者不一致时返回 HTTP 409 和 `plan_revision_conflict`，响应携带当前 revision ID、revision number 与 plan hash，而且不创建 session。前端已有的 start-time 409 路径会进入可见 conflict 状态。已经启动的 session 仍保持自己的 immutable snapshot，不受后续 revision 影响。

## 评分隔离

配置 snapshot 只进入计划生成和题目预算，不进入评分 rubric。冻结策略测试明确验证：

```text
configuration_may_change_rubric = False
scoring_rubric_changed = False
```

评分隔离策略回归为 **17 passed**。

## T56 验收

- 基础/中级/高级难度：PASS；
- 15/30/45/60 分钟安全时长：PASS；
- 技术深度/系统设计/项目复盘/综合重点：PASS；
- 有限题型安全预设且无任意百分比：PASS；
- 192 个配置组合后端验证：PASS；
- 配置变更使当前计划 stale 并要求显式重新生成：PASS；
- 改回当前 snapshot 可解除 stale：PASS；
- 显示预计题数、目标时长、预计追问和非承诺性说明：PASS；
- 用户不能把单题追问上限调到 2 以上：PASS；
- 配置冻结到 revision snapshot 并参与 hash：PASS；
- 刷新后恢复配置、draft 和 revision：PASS；
- 历史非 UI 预设 snapshot 精确恢复：PASS；
- 配置不改变评分 rubric：PASS；
- 历史 revision 不能绕过 latest gate 启动新 session：PASS。

## 测试与证据

T56 前端纯配置测试：

```text
6 passed, 0 failed, 0 skipped
```

T56 新增的开始页配置场景与纯配置测试共：

```text
9 passed, 0 failed, 0 skipped
```

全部前端 Vitest：

```text
57 passed, 0 failed, 0 skipped
```

后端配置组合矩阵：

```text
192 passed
```

完整 configured generation 测试文件：

```text
207 passed
```

扩大邻接回归覆盖 plan API、editor、revision、configured generation、E2E、预算、评分隔离、audit/binding、PostgreSQL revision store、session serialization、drafts 和 prep/context：

```text
388 passed, 0 failed, 6 skipped, 1 warning
```

正式全仓回归在设置 `POSTGRES_DSN=postgresql://postgres@127.0.0.1:55432/interview` 后为：

```text
2857 passed, 9 failed, 3 skipped, 1 warning
```

相较 T55 基线 `2705 passed, 9 failed, 3 skipped`，T56 新增 152 个 Python passing cases：配置矩阵扩展 144 个、显式配置 regenerator 1 个、配置重新生成 API 1 个、非法配置参数化 4 个、editor 服务端能力 1 个、历史 revision latest gate 1 个。没有新增 T56 相关失败。

九个既有失败如实保留：

1. agent runtime hardening 的旧 `perf_counter` mock ticks 耗尽；
2. context artifact failed cleanup 跨时钟；
3. context artifact concurrent cleanup 跨时钟；
4. completed generation chunk cleanup 跨时钟；
5. interview graph 旧期望为 `next_question`，当前最终态为 `finish`；
6. applied workflow command payload cleanup 跨时钟；
7. historical publication allowlist 与 quality branch 差异；
8. PostgreSQL session deletion 的旧 latest migration 期望 `followup_decision_v1`；
9. 既有 dependency lock hash 漂移。

本轮收尾测得 PostgreSQL `clock_timestamp()` 比 Python UTC 快 **3.319487 秒**，仅作为跨时钟失败的环境证据，不作为 T56 acceptance gate。此前未设置 `POSTGRES_DSN` 的一次诊断运行覆盖不足，不用于正式结论。

其他验证：

- `compileall app tests`：PASS；
- `git diff --check`：PASS，仅有 Windows LF/CRLF 提示；
- 风险模式与任意数值控件扫描：PASS；
- Vite 生产构建：PASS；
- CSS 291.61 kB，gzip 38.42 kB；
- JavaScript 535.57 kB，gzip 145.27 kB；
- Rollup 如实报告 JavaScript chunk 超过 500 kB 的非阻塞警告；
- `npm run check`：TOOLING_MISSING，项目未安装可执行 ESLint；
- 真实 Provider 调用：0。

机器证据见 `docs/interview-quality-v1-t56-evidence.json`。

## Provider 与后续边界

- T56 不声明真实 Provider 问题质量、成本、token 使用或 Provider Quality PASS；
- Provider Quality 仍为 `BLOCKED_MODEL_VERSION_DRIFT`，不阻塞无关 Engineering；
- 统一授权 `interview-quality-v1-20260805-unlimited-01` 仍有效，不需要在阶段、Gate、checkpoint 或提交点重新授权；
- T57 与 T72 尚未完成；T56 提交后直接进入 T57；
- Goal 在 T72 真正完成前保持 `active`。

## 回滚

回滚 T56 提交会移除配置工作区、安全预设、配置 stale/重新生成流程、服务端显式配置能力、启动入口 latest gate 及相关测试和证据。回滚不得恢复客户端 Provider 输出注入、任意题型百分比、超过 2 的单题追问上限、历史 revision 启动绕过，或使配置改变评分 rubric。
