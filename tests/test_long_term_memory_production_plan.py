from pathlib import Path
import re


PLAN = Path(
    "docs/superpowers/plans/"
    "2026-08-03-long-term-memory-production-shadows-consumption-and-promotion.md"
)


def plan_text() -> str:
    return PLAN.read_text(encoding="utf-8")


def section(text: str, start: str, end: str) -> str:
    return text[text.index(start) : text.index(end)]


def task_sections(text: str) -> dict[int, str]:
    matches = list(
        re.finditer(r"^## Task (\d+)[：:]", text, re.MULTILINE)
    )
    sections: dict[int, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else text.index(
            "## 10. 晋级门禁总表"
        )
        sections[int(match.group(1))] = text[match.start() : end]
    return sections


def test_plan_is_intentionally_long_and_has_complete_task_sequence():
    text = plan_text()
    tasks = [
        int(value)
        for value in re.findall(r"^## Task (\d+)[：:]", text, re.MULTILINE)
    ]

    assert len(text.splitlines()) >= 1_900
    assert tasks == list(range(40))


def test_every_task_has_goal_and_explicit_exit_or_terminal_boundary():
    sections = task_sections(plan_text())

    assert set(sections) == set(range(40))
    for number, value in sections.items():
        assert "**Goal:**" in value, f"Task {number} has no goal"
        assert (
            "**Exit gate:**" in value
            or "**Pass output:**" in value
            or "**Success output:**" in value
            or "**Terminal output:**" in value
        ), f"Task {number} has no exit boundary"


def test_plan_keeps_all_twenty_fixed_decisions():
    text = plan_text()
    decisions = [
        (int(number), title)
        for number, title in re.findall(
            r"^### Decision (\d+)[：:]([^\n]+)", text, re.MULTILINE
        )
    ]

    assert [number for number, _ in decisions] == list(range(1, 21))
    assert [title for _, title in decisions] == [
        "原始 Session 数据始终是权威来源",
        "Production Shadow 严格串行",
        "正式身份采用 OIDC issuer/subject 绑定",
        "Consent purpose 分离且 default off",
        "模型只能创建 proposed facts",
        "候选人拥有完整控制面",
        "Write Shadow 不读取，Read Shadow 不注入",
        "C1 allowlist 是封闭集合",
        "C1 只允许 follow-up context assembly",
        "C1 bounds 固定且 fail-closed",
        "Prompt block 必须可见且有固定 marker",
        "实时 disable 使用 context-assembly barrier",
        "评分、报告和公共知识使用结构隔离加相等性测试",
        "常规观察只保存低基数聚合",
        "删除真相由 online state 与 operator tombstone 共同维持",
        "每个生产阶段一次只改变一个 memory axis",
        "Hard stop 不等待统计显著性",
        "窗口结束先关闭再判定",
        "Consumption 实现批准与生产 Canary 批准分离",
        "C1 1% 是本计划生产上限",
    ]


def test_plan_distinguishes_historical_authoring_and_execution_baselines():
    baseline = section(plan_text(), "### 2.1", "### 2.2")

    for value in (
        "f5dce4206751775c1650a4fccbd5060625af523a",
        "d857e0a091d55db76f4405669a9e699e3e3f44b6",
        "962eab5990e21d6a34821c400483be798ec5a1ab",
        "6969efa119de0da33698f0de74f4fdeee502b375",
        "EXECUTION_START_HEAD",
        "不得 reset、restore、clean、覆盖或错误提交用户变化",
    ):
        assert value in baseline


def test_plan_preserves_the_full_explicit_exclusion_boundary():
    exclusions = section(plan_text(), "### 3.2", "## 4.")
    required = (
        "Production Budget Shadow PASS 前启动任何 Principal production Shadow",
        "trusted-local identity 作为 production consumption identity",
        "email、姓名、电话、IP、User-Agent、设备指纹、简历、embedding 相似度或模型输出合并 Principal",
        "默认开启 Consent",
        "自动确认或自动激活模型 proposal",
        "unconfirmed、revoked、expired、deleted、conflicting 或 stale fact 注入 Prompt",
        "候选人回答、简历、报告、项目名、公司名或自由文本保存为 Principal fact",
        "在 C1 使用 `confirmed_skill`",
        "使用历史事实计算或改变 score、difficulty、evidence、report、rank、recommendation 或 hiring decision",
        "Principal facts 写入公共 Knowledge、corpus、embedding 或共享向量检索",
        "cross-Principal similarity、nearest-neighbor、collaborative filtering 或自动 identity merge",
        "candidate 不可见的情况下进行隐式 personalization",
        "`Disable memory now` 后继续新的 proposal、selection 或 injection",
        "production window 内热修代码、切换 revision、改变 schema 或复用旧批准",
        "external approval record、approver reference、ticket digest、deployment digest、DSN、secret 或 candidate locator 提交进 Git",
        "PENDING evidence 改写为 production PASS",
        "从 C1 1% 自动扩到 5%、25%、50% 或 100%",
        "本计划视为 Production Budget Shadow、Write Shadow、Read Shadow 或 Consumption Canary 的外部批准",
    )

    for value in required:
        assert value in exclusions


def test_plan_pins_authenticated_identity_consent_and_user_rights():
    text = plan_text()

    for requirement in (
        "OIDC `issuer + subject`",
        "deployment-scoped、versioned HMAC",
        "proposal_write",
        "fact_storage",
        "read_shadow",
        "consumption_c1",
        "view、confirm、correct、revoke、delete、export",
        "Ignore memory for this interview",
        "Disable memory now",
        "next-assembly + 60s SLO",
        "不自动继承旧 memory",
    ):
        assert requirement in text


def test_plan_closes_c1_allowlist_and_pins_runtime_bounds():
    decisions = section(plan_text(), "### Decision 8", "### Decision 13")

    for category in (
        "interview_language",
        "accessibility_preference",
        "learning_goal",
        "target_role_family",
    ):
        assert category in decisions
    for requirement in (
        "`confirmed_skill` 明确排除",
        "`interview.followup.context_assembly`",
        "最多 3 个 facts、最多 120 tokens",
        "Non-authoritative historical preference",
        "current candidate message 之前",
    ):
        assert requirement in decisions


def test_plan_pins_all_phase_specific_configuration_controls():
    matrix = section(plan_text(), "## 6.", "## 7.")

    for key in (
        "MEMORY_BUDGET_MODE",
        "MEMORY_COMPRESSION_MODE",
        "MEMORY_LONG_TERM_MODE",
        "MEMORY_LONG_TERM_WRITE_SHADOW_ENABLED",
        "MEMORY_LONG_TERM_READ_SHADOW_ENABLED",
        "MEMORY_LONG_TERM_CONSUMPTION_C1_ENABLED",
        "MEMORY_LONG_TERM_CONSUMPTION_TRAFFIC_PERCENT",
        "MEMORY_LONG_TERM_MAX_CONSUMED_FACTS",
        "MEMORY_LONG_TERM_MAX_CONSUMED_TOKENS",
        "MEMORY_TRUSTED_LOCAL_PRINCIPAL_MEMORY_API_ENABLED",
        "MEMORY_AUTHENTICATED_SELF_SERVICE_ENABLED",
        "MEMORY_CONSUMPTION_KILL_SWITCH",
    ):
        assert key in matrix
    assert "添加 `consume_c1`，继续拒绝泛化 `consume`" in plan_text()


def test_plan_defines_every_write_read_and_consumption_hard_stop():
    gates = section(plan_text(), "## 9.", "## Task 0")
    required = (
        "WRITE_CROSS_PRINCIPAL",
        "WRITE_WITHOUT_CONSENT",
        "WRITE_AUTOMATIC_ACTIVE",
        "WRITE_PUBLIC_KNOWLEDGE_MUTATION",
        "WRITE_METRICS_INCOMPLETE",
        "READ_CROSS_PRINCIPAL",
        "READ_WITHOUT_CONSENT",
        "READ_UNCONFIRMED_FACT",
        "READ_PROMPT_CHANGED",
        "READ_QUESTION_SCORE_REPORT_CHANGED",
        "READ_METRICS_INCOMPLETE",
        "CONSUME_CROSS_PRINCIPAL",
        "CONSUME_WITHOUT_CONSENT",
        "CONSUME_AFTER_IGNORE_OR_DISABLE",
        "CONSUME_NON_C1_FACT",
        "CONSUME_OUTSIDE_FOLLOWUP_CONTEXT",
        "CONSUME_MARKER_MISSING",
        "CONSUME_HIDDEN_PERSONALIZATION",
        "CONSUME_CURRENT_EVIDENCE_OVERRIDDEN",
        "CONSUME_SCORE_OR_REPORT_DIFFERENCE",
        "CONSUME_PUBLIC_KNOWLEDGE_MUTATION",
        "CONSUME_DISABLE_DELETE_SLA_BREACH",
        "CONSUME_BACKUP_REPLAY_RESIDUE",
        "CONSUME_METRICS_INCOMPLETE",
    )

    for gate in required:
        assert gate in gates
    assert "observed_error_rate - baseline_error_rate > 0.005" in gates
    assert "observed_p95_latency_ms > baseline_p95_latency_ms * 1.20" in gates


def test_plan_requires_independent_approvals_for_each_production_phase():
    text = plan_text()

    for role in (
        "change_owner",
        "operations",
        "privacy",
        "security",
        "fairness",
    ):
        assert role in text.lower()
    for phase in (
        "BUDGET_SHADOW_ONLY",
        "PRINCIPAL_WRITE_SHADOW_ONLY",
        "PRINCIPAL_READ_SHADOW_ZERO_INJECTION_ONLY",
        "PRINCIPAL_MEMORY_CONSUMPTION_C1_ONLY",
    ):
        assert phase in text
    assert "任何 Shadow approval 都不能复用" in text


def test_plan_pins_serial_promotion_and_three_state_closure():
    text = plan_text()

    for contract in (
        "Budget → Write → Read",
        "PASS",
        "BLOCKED",
        "CONTINUE_OBSERVATION",
        "窗口结束先关闭再判定",
        "EXPANSION_ABOVE_1_PERCENT=NOT_AUTHORIZED",
        "GENERAL_AVAILABILITY=NOT_AUTHORIZED",
    ):
        assert contract in text


def test_plan_preserves_critical_rollback_scenarios():
    rollback = section(plan_text(), "## 11.", "## 12.")
    scenarios = (
        "Identity ambiguous",
        "Account takeover/recovery risk",
        "Cross-Principal access",
        "Consent missing/revoked",
        "Disable SLA breach",
        "Automatic active fact",
        "Backup resurrection",
        "Read Prompt mutation",
        "Read score/report difference",
        "Non-C1 fact consumed",
        "Marker/disclosure missing",
        "Current evidence overridden",
        "Knowledge/embedding mutation",
        "Traffic cap exceeded",
        "Production code defect",
        "Rollback not verified",
    )

    for scenario in scenarios:
        assert scenario in rollback


def test_plan_has_complete_fifty_item_definition_of_done():
    definition = section(
        plan_text(), "## 14. Definition of Done", "## 15. 稳定状态输出"
    )
    items = [
        int(number)
        for number in re.findall(r"^(\d+)\. ", definition, re.MULTILINE)
    ]

    assert items == list(range(1, 51))


def test_plan_traces_existing_pmc_requirements_without_inventing_mem_ids():
    text = plan_text()
    traceability = section(text, "## 13. Traceability", "## 14.")

    for number in range(1, 11):
        assert f"PMC-{number:03d}" in traceability
    assert "本计划不创建新的 `MEM-*` requirement ID" in traceability
    assert not re.search(
        r"\bMEM(?:[-_][A-Z0-9]+)+[-_]\d+\b",
        text,
        re.IGNORECASE,
    )


def test_plan_never_treats_shadow_or_staging_as_consumption_authority():
    text = plan_text()

    for boundary in (
        "Write Shadow PASS ≠ Read Shadow 自动授权",
        "Read Shadow PASS ≠ Consumption 实现授权",
        "Consumption Staging PASS ≠ Production Canary 授权",
        "C1 1% PASS ≠ 5% 或 General Availability 自动授权",
        "IMPLEMENTATION=NOT_AUTHORIZED",
        "PRODUCTION_CANARY=NOT_AUTHORIZED",
        "LONG_TERM_MEMORY_CONSUMPTION=BLOCKED",
    ):
        assert boundary in text
