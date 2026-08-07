# Interview Quality V1 — T64 自动审查

## 结论

```text
engineering_status=PASS
automatic_review=PASS
quality_status=NOT_REQUIRED_CROSS_PLATFORM_ENGINEERING
overall_status=PASS
requirement_mapping=21/21
tests_passed=6646
tests_failed=0
nonblocking_skips=92
blocking_skips=0
provider_calls=0
cleanup_residue=0
screenshots=0
traces=0
open_engineering_findings=0
```

T64 已在同一个干净候选提交 `820328c8b0428c31d134e9bc991759dd64611fa2`、tree
`5f420690d4c54c4c25fb67bfae94b067c270621d` 上完成 Windows 11 x64 与 Ubuntu 24.04 LTS x64
完整验收。跨平台 Gate 为 Engineering PASS；T64 本身不要求真实 Provider Quality PASS，因此
`quality_status=NOT_REQUIRED_CROSS_PLATFORM_ENGINEERING`。四类质量 replay 的 Engineering 合同通过，
但其独立评审或真实 Provider 质量结论仍准确保留为 BLOCKED，由 T65 接续，未被本任务冒充为质量通过。

## 21 项要求映射

| ID | Plan 要求 | 正式证据 | 结论 |
|---|---|---|---|
| T64-M01 | Windows 11 x64 | Windows 11 build 10.0.26200 AMD64 | PASS |
| T64-M02 | Ubuntu 24.04 LTS x64 | Ubuntu 24.04.4 LTS x86_64 | PASS |
| T64-M03 | Python 3.11，记录绝对路径 | Windows `F:\\python3.11\\python.exe` 3.11.3；Ubuntu `/usr/local/bin/python3.11` 3.11.15 | PASS |
| T64-M04 | Node 22 LTS | 两平台均为 22.21.0 | PASS |
| T64-M05 | PostgreSQL 16 | 两平台均为 16.14，pgvector 0.8.6 | PASS |
| T64-M06 | 项目锁定 Playwright Chromium | Playwright 1.61.1，Chromium 149.0.7827.55 | PASS |
| T64-C01 | Python 全量 pytest | 每平台 2955 passed / 0 failed / 3 skipped | PASS |
| T64-C02 | 可达 DSN 的 PostgreSQL 标记测试 | 每平台 203 passed / 0 failed / 0 skipped；missing-DSN skip=0 | PASS |
| T64-C03 | migration 和 backup restore | 每平台 7 passed / 0 failed / 0 skipped | PASS |
| T64-C04 | root 与 frontend `npm ci` | 两平台四次 clean install 全部 PASS，0 vulnerabilities | PASS |
| T64-C05 | ESLint | 两平台均 0 errors / 6 warnings | PASS_WITH_NONBLOCKING_WARNINGS |
| T64-C06 | Vitest | 每平台 67 passed / 0 failed / 0 skipped | PASS |
| T64-C07 | frontend production build | 两平台均 PASS | PASS_WITH_NONBLOCKING_CHUNK_WARNING |
| T64-C08 | Playwright preflight 与浏览器测试 | 每平台 91 passed / 0 failed / 43 skipped；preflight PASS | PASS |
| T64-C09 | 四类冻结质量评测 replay | 两平台四类均 Engineering PASS、Provider calls=0 | PASS_ENGINEERING_ONLY |
| T64-C10 | 清理端口、进程、trace、截图和临时数据库对象 | 两平台六个 residue 字段全部为 0 | PASS |
| T64-R01 | 必需测试 0 failure / 0 blocking skip | 6646 passed / 0 failed / 0 blocking skip | PASS |
| T64-R02 | 不接受 missing DSN 导致的 PostgreSQL skip | 两平台 `postgres_dsn_configured=true`、missing-DSN skip=0 | PASS |
| T64-R03 | 所有非阻塞 skip 都有 reason 和 owner | 92/92 已逐项登记 | PASS |
| T64-R04 | 拒绝替代 OS、Python、Node、数据库或浏览器版本 | Gate 对目标工具链执行精确校验 | PASS |
| T64-R05 | 冻结一个干净 Provider candidate SHA/tree | `820328c…` / `5f42069…`，两平台 `source_clean=true` | PASS |

权威机器映射位于 `tests/golden/interview_quality_v1/t64-cross-platform-acceptance-v1.json`，共 21 项；
canonical SHA-256 为 `3da812229919e3892af5733da8bc9efb9b544b8c12c48e35aceb0afeb8631ec7`，
文件 SHA-256 为 `0c1963bf1327e854fe1dfbf1dd2f896b420864e12970969177c1987ee2652665`。

## 正式平台矩阵

| 平台 | Python | Node | PostgreSQL / pgvector | Playwright / Chromium |
|---|---|---|---|---|
| Windows 11 10.0.26200 AMD64 | 3.11.3 (`F:\\python3.11\\python.exe`) | 22.21.0 | 16.14 / 0.8.6 | 1.61.1 / 149.0.7827.55 |
| Ubuntu 24.04.4 LTS x86_64 | 3.11.15 (`/usr/local/bin/python3.11`) | 22.21.0 | 16.14 / 0.8.6 | 1.61.1 / 149.0.7827.55 |

每个平台执行 11 个必需命令组：root/frontend `npm ci`、Python 全量 pytest、PostgreSQL 标记测试、
migration/restore、ESLint、Vitest、frontend build、Playwright preflight、Playwright browser，以及四类质量 replay。
两平台测试计数完全一致：

```text
Python full:       2955 passed / 0 failed / 3 skipped
PostgreSQL marked:  203 passed / 0 failed / 0 skipped
migration/restore:    7 passed / 0 failed / 0 skipped
Vitest:              67 passed / 0 failed / 0 skipped
Playwright:          91 passed / 0 failed / 43 skipped
per platform:      3323 passed / 0 failed / 46 skipped
cross platform:    6646 passed / 0 failed / 92 skipped
```

## Skip 清单与归属

每个平台有 46 个非阻塞 skip：3 个 Python、43 个 Playwright。所有条目均携带 `blocking=false`、
明确 reason 和 owner；跨平台合计 owner T64 为 86 项、owner T65 为 6 项。

- Windows Python：POSIX path contract（T64）、POSIX venv interpreter symlink（T64）、
  `RUN_REAL_LLM_EVAL` opt-in（T65）。
- Ubuntu Python：Windows drive path contract（T64）、Windows UNC contract（T64）、
  `RUN_REAL_LLM_EVAL` opt-in（T65）。
- Playwright：移动端项目排除均由桌面端显式 viewport/design/T59 矩阵持有（T64）；每个平台两项
  `real-model-smoke` 跳过由 T65 持有，原因为 `explicit provider opt-in required`。

不存在缺少 DSN 引起的 PostgreSQL skip，也不存在无 owner、无 reason 或 blocking skip。

## 四类质量 replay 的真实性边界

Windows 与 Ubuntu 均运行相同的四类冻结 replay，全部 `engineering_status=PASS` 且
`provider_calls=0`：

| Replay | 样本 | Engineering | Quality |
|---|---:|---|---|
| initial_question | 24 cases | PASS | BLOCKED_SYNTHETIC_FIXTURE_ONLY |
| followup_decision | 100 cases / 20 sequences | PASS | BLOCKED_PENDING_INDEPENDENT_REVIEW |
| report_score | 60 cases | PASS | BLOCKED_PENDING_INDEPENDENT_REVIEW |
| report_semantic | 24 cases / 17 scenarios | PASS | BLOCKED_INDEPENDENT_HUMAN_REVIEW_NOT_RUN |

这些状态证明 replay 合同在目标平台可执行，不证明真实模型质量。真实 DeepSeek `deepseek-chat` 完整基准、
调用/token/金额计量和必要评审属于 T65。

## 自动审查发现与修复

T64 实现与多轮正式候选审查共关闭以下工程问题：

1. 补齐 `pdfplumber`、`pypdf` 直接依赖以及平台锁文件，验证 clean hashed install。
2. Ubuntu migration/restore 使用原生 PostgreSQL 16 工具回退，避免宿主 Docker 假设。
3. 修复 PostgreSQL catalog 外键查询、临时关系清理和 queue recovery 的脆弱时序假设。
4. 历史 publication、stream timing 及三项数据库时钟域测试不再错误绑定 live HEAD 或宿主时钟。
5. 冻结 JSON 哈希改为 CRLF/LF 可移植语义；平台 runner 可解析 pretty-printed JSON 日志。
6. 补齐项目锁定 ESLint 依赖/配置，修复真实 lint error，并保留 6 个既有非阻塞 hook warning。
7. 修复 report detail focus loading race，并把 real-model smoke skip 正确归属 T65。
8. 修复跨平台 Gate 的 `platform_artifacts` key collision，确保 Windows/Ubuntu 产物独立保存。
9. 修复 Ubuntu regex invalid escape warning。
10. Chromium 布局验收允许 1/64 CSS pixel 量化，同时保持精确 computed min-height 合同。

最终自动审查重新读取两个平台产物和 Gate，确认候选 SHA/tree、平台版本、计数、skip、清理字段与哈希一致，
`open_engineering_findings=0`。

## 未被接受为最终 PASS 的诊断运行

- `7bedaf9`：Windows full Pytest 有 3 个时钟域失败，runner 不能解析 pretty cleanup JSON；已取代。
- `c907dce`：Windows Playwright 出现 report-detail focus loading race，real-model skip 被误判 blocking；已取代。
- `ec95173`：两平台矩阵通过，但自动审查发现 Gate 平台 Artifact key collision；未冻结为最终证据。
- `4cd7aea`：Windows PostgreSQL marker 有一次瞬时 queue claim 失败；隔离 10/10 和 marker 重跑 203/203 后，
  将测试改为有界 worker-style polling；已取代。
- `069b331`：Windows 通过，Ubuntu Chromium 将 40px 量化为 39.999969px；修复后已取代。
- `820328c`：Windows PASS、Ubuntu PASS、最终跨平台 Gate PASS；这是唯一冻结候选。

## 非阻塞警告

- 每个平台 full Pytest、PostgreSQL marker 和 migration/restore 报告同一个
  Starlette TestClient/httpx deprecation warning；没有转写成失败，也没有重复计作多个根因。
- ESLint 每个平台保留 6 个 `react-hooks/exhaustive-deps` warning，0 errors。
- Vite 每个平台成功构建；单一 JS chunk 为 542.31 kB，超过 500 kB warning threshold。

这些警告不违反 T64 的 21 项正式合同；已如实保留，未删除日志或伪造零 warning。

## 证据、数据和清理边界

- Windows platform Artifact：18889 bytes，SHA-256
  `a584169da8ee0aba387a6dd8739e1b5ded9ea83ba0e02a62b8732c496f6d146b`。
- Ubuntu platform Artifact：18862 bytes，SHA-256
  `932c32992bd4dc953c41f237a46326dbc65906fc70f3ac16866e79542f3622b2`。
- T64 Gate Artifact：1139 bytes，SHA-256
  `e9fa2328920ec7dc9a63d2745636cf085cf0e8c82825bcadf37f071c003ed62b`。
- 两个平台 cleanup 的 ports、processes、screenshots、traces、temporary database relations 和 unexpected
  worktree changes 全部为 0。
- T64 Provider calls=0；没有发送真实候选人数据，只使用 synthetic/public-safe fixture。
- Playwright 配置保持 screenshot off，正式调用显式 trace off；没有截图、trace 或图像操作。
- 原始运行日志保留在忽略的 `tmp/` 下；Git 只提交聚合、脱敏、可复核的指标和哈希。

T64 完成后，冻结 Provider candidate 仍是 `820328c…`；后续文档提交不会重新定义该候选。
下一任务为 T65。总体 Interview Quality V1 Goal 保持 `active`，T72 尚未完成。
