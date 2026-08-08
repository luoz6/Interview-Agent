# Interview Quality V1 — Gate 5 自动审查

## 结论

```text
engineering_status=PASS
quality_status=BLOCKED
quality_reason=BLOCKED_MODEL_VERSION_DRIFT
overall_status=BLOCKED
automatic_review=PASS
provider_calls=0
engineering_may_continue=true
next_task=T60
```

Gate 5 Engineering 已通过。T50–T56、T58–T59 均已完成；15/30/45/60 分钟配置分别映射到 3/5/7/9 个主问题，60 分钟不再受历史 3–5 题硬编码限制；preview、edit、start 和 session snapshot 使用同一个不可变 Plan Revision 与 plan hash；启动接口不调用 Provider；单题编辑、换题、删除、排序、草稿/版本恢复和冲突均具有 unit 与浏览器端到端合同；T59 没有发现阻塞级无障碍或移动端回归。

Gate 5 Quality 未通过。T57 的统一授权有效且无限额，但授权模型 `deepseek-chat` 不在当前 Provider 官方模型列表和价格表中，可见候选为 `deepseek-v4-flash` 与 `deepseek-v4-pro`。授权禁止自动替换模型，因此在发送任何 JD、简历或问题生成数据前按 `MODEL_VERSION_DRIFT` 硬停止。没有 Provider 调用，没有首个数据请求，没有 fallback，也没有真实 Provider 初始问题质量结论。

Plan 明确规定 T57 未运行或 Provider 不可用时，Gate 5 Quality 必须为 NOT_RUN/BLOCKED，但不阻塞 T60 工程组合回归。因此保留 `engineering_status=PASS`，总体状态诚实记录为 `BLOCKED`，并自动进入 Phase 6 的 T60。

## Engineering 验收映射

| Gate 5 条件 | 证据 | 结果 |
|---|---|---|
| T50–T56、T58–T59 完成 | 各任务 review/evidence 与对应实现提交 | PASS |
| 15/30/45/60 与预算模型一致 | 3/5/7/9 主问题、估算时长与 warning 合同 | PASS |
| 60 分钟允许超过 5 题 | 60 分钟生成 9 个主问题；历史范围仅保留 legacy 兼容边界 | PASS |
| preview/edit/start/session hash 100% 一致 | revision/configuration snapshot/hash 校验、start latest revision gate | PASS |
| 启动 Provider 调用 0 | start 路由与 E2E Provider spy | PASS |
| 编辑、换题、删除、排序、恢复、冲突 unit + E2E | T54–T59 前端、API 和 Playwright 测试 | PASS |
| 无阻塞 a11y/mobile 回归 | T59 41 项浏览器矩阵、66 项前端测试与响应式合同 | PASS |

## 自动审查

自动审查复核了当前 T59 代码差异、T50–T58 的既有证据、T59 完整浏览器矩阵、PDF 语义、邻接 Python、前端全量测试、生产构建和全仓 pytest。T59 审查中发现的浏览器支持应用契约漂移、focus trap、移动触控高度、诊断刷新遮挡报告、旧 selector/流程以及 unscored 假分断言均已修复并复测。

Gate 5 没有以宽松断言、skip、截图或 trace 替代阻塞验收。`axe-core` 未安装，真实硬件屏幕阅读器会话未运行，ESLint 工具缺失，构建 chunk warning、全仓 5 个既有失败、218 个 skip 和 1 个 warning 都在 T59 机器证据中保留。

## Quality blocker

```text
authorization_id=interview-quality-v1-20260805-unlimited-01
provider=DeepSeek
authorized_model=deepseek-chat
budget_limit=UNLIMITED
request_limit=UNLIMITED
token_limit=UNLIMITED
fallback=prohibited
available_models=deepseek-v4-flash,deepseek-v4-pro
provider_called=false
first_data_request_sent=false
quality_status=BLOCKED_MODEL_VERSION_DRIFT
```

无限额授权消除了预算、请求数和 token 上限导致的暂停，但不会授权模型替换。若后续获得与 Provider 当前模型一致的新授权，T57 可以作为独立 Quality 路径重跑；在此之前不能声明初始问题真实 Provider 质量达到第 5.4 节阈值。

## 阶段继续

- Gate 5 Engineering：PASS；
- Gate 5 Quality：BLOCKED_MODEL_VERSION_DRIFT；
- Gate 5 overall：BLOCKED；
- 用户 checkpoint：不需要；
- 下一阶段：Phase 6；
- 下一任务：T60 全链路组合回归矩阵；
- Goal：继续保持 `active`，直到 T72 和最终适用 Gate 真正完成。
