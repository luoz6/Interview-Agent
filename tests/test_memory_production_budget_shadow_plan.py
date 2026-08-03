from pathlib import Path
import re


PLAN = Path(
    "docs/superpowers/plans/"
    "2026-08-03-memory-production-budget-shadow-execution-and-evidence.md"
)


def plan_text() -> str:
    return PLAN.read_text(encoding="utf-8")


def section(text: str, start: str, end: str) -> str:
    return text[text.index(start) : text.index(end)]


def test_plan_has_a_complete_contiguous_task_sequence():
    tasks = [
        int(value)
        for value in re.findall(r"^## Task (\d+)[：:]", plan_text(), re.MULTILINE)
    ]

    assert tasks == list(range(14))


def test_plan_distinguishes_published_initial_and_dynamic_baselines():
    text = plan_text()
    baseline = section(text, "### 2.1", "### 2.2")

    assert "b1559dadde4195449d1841322c4fe931984197dc" in baseline
    assert "87f25689d798c6e531dbdc5eea5bcc86ad7c049a" in baseline
    assert "ahead=1" in baseline
    assert "behind=0" in baseline
    assert "EXECUTION_START_HEAD" in baseline
    assert "不得硬编码或期待 `HEAD=b1559da`" in baseline


def test_plan_pins_external_approval_and_single_axis_boundaries():
    text = plan_text()

    for role in (
        "change_owner",
        "operations",
        "privacy",
        "security",
        "fairness",
    ):
        assert role in text
    for contract in (
        "BUDGET_SHADOW_ONLY",
        "MEMORY_BUDGET_MODE=shadow",
        "MEMORY_BUDGET_SHADOW_ENABLED=true",
        "PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED",
        "PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED",
        "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
        "PRODUCTION_OBSERVATION=NOT_RUN",
    ):
        assert contract in text


def test_status_at_authoring_keeps_external_approval_absent():
    status_line = next(
        line
        for line in plan_text().splitlines()
        if line.startswith("**Status at authoring:**")
    )

    assert "APPROVAL_STATUS=PENDING" in status_line
    assert "EXTERNAL_APPROVAL_RECORD=ABSENT" in status_line
    assert "PRODUCTION_OBSERVATION=NOT_RUN" in status_line
    assert "EXTERNAL_APPROVAL_RECORD=VERIFIED" not in status_line


def test_plan_preserves_every_explicit_exclusion():
    exclusions = section(plan_text(), "### 3.2", "## 4.")

    required = (
        "Production migration",
        "Budget enforcement",
        "Context Compression consumption",
        "Question Memory production consumption",
        "Principal Memory Write production Shadow",
        "Principal Memory Read production Shadow",
        "Principal Memory consumption",
        "把 personal facts 注入 Prompt、问题、追问、评分、报告或推荐",
        "自动创建、确认或激活 Principal facts",
        "使用真实候选人内容作为测试 fixture",
        "在仓库、CI artifact 或常规日志中保存正式 external approval record",
        "在仓库中保存 approver、ticket、deployment 或 record digest",
        "在聚合 evidence 中保存 session/principal/fact/question/message/artifact locator",
        "把 `b1559da` 的 PENDING metadata 当作审批记录",
        "Production Budget Shadow PASS 后自动进入 Write Shadow",
        "在已批准窗口内热修 production code、切换 revision 或复用旧审批",
        "本计划内实现 Consumption Spec",
    )

    assert len(required) == 17
    for exclusion in required:
        assert exclusion in exclusions


def test_plan_keeps_all_twelve_fixed_decisions():
    text = plan_text()
    decisions = [
        (int(number), title)
        for number, title in re.findall(
            r"^### Decision (\d+)[：:]([^\n]+)", text, re.MULTILINE
        )
    ]

    assert [number for number, _ in decisions] == list(range(1, 13))
    assert [title for _, title in decisions] == [
        "先补齐生产结果工具，再申请审批",
        "Production evaluator 必须离线工作",
        "历史 PENDING evidence 不改写",
        "一次只改变 Budget Shadow",
        "先 0.1% warm-up，再到批准上限",
        "窗口结束先关闭，再判定",
        "不足不是 PASS",
        "低样本 bucket 不阻止总体安全结论，但禁止外推",
        "Hard stop 不等待统计显著性",
        "Production PASS 不授权 Write Shadow",
        "生产窗口内禁止代码修补",
        "外部 record 和受信 digest 永不进入 Git",
    ]


def test_plan_pins_all_twelve_safe_configuration_keys():
    matrix = section(plan_text(), "## 5.", "## 6.")

    rows = (
        "| `MEMORY_BUDGET_MODE` | `disabled` | `shadow` | `enforce` |",
        "| `MEMORY_BUDGET_SHADOW_ENABLED` | `false` | `true` | 持续超出批准窗口 |",
        "| `MEMORY_BUDGET_ENFORCEMENT_PREP` | `false` | `false` | `true` |",
        "| `MEMORY_BUDGET_ENFORCEMENT_INTERVIEW` | `false` | `false` | `true` |",
        "| `MEMORY_BUDGET_ENFORCEMENT_REVIEW` | `false` | `false` | `true` |",
        "| `MEMORY_BUDGET_ENFORCEMENT_REPORT` | `false` | `false` | `true` |",
        "| `MEMORY_COMPRESSION_MODE` | `disabled` | `disabled` | `consume` |",
        "| `MEMORY_COMPRESSION_SHADOW_ENABLED` | `false` | `false` | `true` |",
        "| `MEMORY_LONG_TERM_MODE` | `disabled` | `disabled` | `write_shadow`、`read_shadow`、`consume` |",
        "| `MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED` | `false` | `false` | `true` |",
        "| `MEMORY_LONG_TERM_READ_SHADOW_ENABLED` | `false` | `false` | `true` |",
        "| `MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED` | `false` | `false` | `true` |",
    )

    assert len(rows) == 12
    for row in rows:
        assert row in matrix


def test_plan_defines_all_immediate_hard_stop_gates():
    gates = section(plan_text(), "### 7.1", "### 7.2")

    required = (
        "MANDATORY_CURRENT_CONTENT_LOSS",
        "PROVIDER_INPUT_CHANGED",
        "KNOWN_OVER_BUDGET_PROVIDER_CALL",
        "PRIVACY_AUDIT_HIT",
        "TRAFFIC_CAP_EXCEEDED",
        "APPROVAL_NOT_CURRENT",
        "APPROVED_REVISION_MISMATCH",
        "DEPLOYMENT_SCOPE_MISMATCH",
        "BUDGET_CONFIG_CONFLICT",
        "OTHER_MEMORY_AXIS_ENABLED",
        "DURABLE_METRICS_INCOMPLETE",
        "SHADOW_EXECUTION_ERROR",
        "DETERMINISTIC_INTERVIEW_REGRESSION",
        "CONFIGURATION_DRIFT",
    )

    assert len(required) == 14
    for gate in required:
        assert gate in gates


def test_plan_requires_three_state_closure_and_a_new_window_for_more_data():
    text = plan_text()

    for status in (
        "PRODUCTION_BUDGET_SHADOW=PASS",
        "PRODUCTION_BUDGET_SHADOW=BLOCKED",
        "PRODUCTION_BUDGET_SHADOW=CONTINUE_OBSERVATION",
        "NEW_APPROVAL_WINDOW_REQUIRED=true",
        "CONFIGURATION_RESTORED=disabled",
    ):
        assert status in text


def test_plan_records_remote_clone_and_observation_thresholds():
    text = plan_text()

    for requirement in (
        "minimum_clone_depth=2",
        "0.1% warm-up",
        "follow-up sample count >= 200",
        "window duration >= 24 hours",
        "observed_error_rate - baseline_error_rate > 0.005",
        "observed_p95_latency_ms > baseline_p95_latency_ms * 1.20",
    ):
        assert requirement in text


def test_plan_preserves_rollback_scenarios_and_complete_definition_of_done():
    text = plan_text()
    rollback = section(text, "## 11.", "## 12.")
    for scenario in (
        "Approval absent/pending",
        "Approval expired/revoked",
        "Revision/scope mismatch",
        "Traffic > approved cap",
        "Mandatory content loss",
        "Provider input mutation",
        "Known over-limit call",
        "Privacy artifact hit",
        "Metrics incomplete",
        "Error/latency regression",
        "Other memory axis enabled",
        "Production-code defect",
        "Warm-up 样本不足",
        "Final 样本不足",
        "Rollback verification failure",
    ):
        assert scenario in rollback

    definition = section(text, "## 13. Definition of Done", "## 14.")
    items = [
        int(number)
        for number in re.findall(r"^(\d+)\. ", definition, re.MULTILINE)
    ]
    assert items == list(range(1, 26))


def test_plan_does_not_invent_normative_requirement_ids():
    text = plan_text()

    assert not re.search(
        r"\bMEM(?:[-_][A-Z0-9]+)+[-_]\d+\b",
        text,
        re.IGNORECASE,
    )
    assert "不得创建未经 Spec 定义的新 `MEM-*` requirement ID" in text
