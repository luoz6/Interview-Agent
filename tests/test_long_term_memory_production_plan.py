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


def task_numbers(text: str) -> list[int]:
    return [
        int(value)
        for value in re.findall(r"^## Task (\d+)[：:]", text, re.MULTILINE)
    ]


def decision_numbers(text: str) -> list[int]:
    return [
        int(value)
        for value in re.findall(r"^### Decision (\d+)[：:]", text, re.MULTILINE)
    ]


def test_plan_is_the_revised_master_roadmap() -> None:
    text = plan_text()

    assert "**Plan revision:** v0.2-revised" in text
    assert "Master Roadmap + Phase Execution Contract" in text
    assert len(text.splitlines()) >= 1_250
    assert task_numbers(text) == list(range(35))
    assert decision_numbers(text) == list(range(1, 21))


def test_task_dependencies_only_point_to_earlier_tasks() -> None:
    text = plan_text()
    matches = list(re.finditer(r"^## Task (\d+)[：:]", text, re.MULTILINE))

    for index, match in enumerate(matches):
        number = int(match.group(1))
        end = matches[index + 1].start() if index + 1 < len(matches) else text.index(
            "## 11."
        )
        body = text[match.end() : end]
        dependency = re.search(r"^\*\*Dependencies:\*\* ([^\n]+)", body, re.MULTILINE)
        if dependency is None:
            assert number == 0
            continue
        referenced = [
            int(value)
            for value in re.findall(r"(?:Task|Tasks)\s+(\d+)", dependency.group(1))
        ]
        referenced.extend(
            int(value)
            for value in re.findall(r"[、,]\s*(\d+)", dependency.group(1))
        )
        assert referenced, f"Task {number} has no machine-readable dependency"
        assert all(value < number for value in referenced), (
            f"Task {number} depends on a non-earlier task: {referenced}"
        )


def test_productization_and_data_use_are_hard_prerequisites() -> None:
    baseline = section(plan_text(), "### 2.1", "### 2.2")
    dependency_graph = section(plan_text(), "### 8.1", "### 8.2")

    assert "HOSTED_PRODUCTIZATION_DECISION != APPROVED" in baseline
    assert "Tasks 4-34 不得实施" in baseline
    assert 'T0["T0 Baseline"] --> T1["T1 Productization ADR"]' in dependency_graph
    assert 'T1 --> T2["T2 Data-use Spec"]' in dependency_graph
    assert 'T2 --> T4["T4-T10 Control Foundation"]' in dependency_graph


def test_principal_identity_is_stable_random_and_hmac_is_only_an_alias() -> None:
    decision = section(plan_text(), "### Decision 4", "### Decision 5")

    assert "versioned subject HMAC alias" in decision
    assert "stable random internal principal_id" in decision
    assert "HMAC key rotation 只更新或新增 alias，不改变内部 Principal ID" in decision


def test_write_read_and_assist_are_strict_single_axis_modes() -> None:
    decision = section(plan_text(), "### Decision 9", "### Decision 10")
    matrix = section(plan_text(), "## 7.", "## 8.")

    assert "Write Shadow: write=true, read=false, assist=false" in decision
    assert "Read Shadow:  write=false, read=true, assist=false" in decision
    assert "C1-A:         write=false, read=false, assist=true" in decision
    assert "`read_shadow` 时 Write gate 必须为 `false`" in matrix
    assert "production mode + Null/test component" in matrix


def test_c1a_never_sends_principal_facts_to_the_provider() -> None:
    decision = section(plan_text(), "### Decision 13", "### Decision 14")
    exclusions = section(plan_text(), "### 3.2", "## 4.")

    assert "C1-A 不把历史事实直接发送给 Provider" in decision
    assert "accessibility_preference" in decision
    assert "永不发送给 LLM" in decision
    assert "learning_goal" in decision
    assert "target_role_family" in decision
    assert "confirmed_skill" in decision
    assert "C1-A 将任何 Principal fact block" in exclusions


def test_production_windows_use_absolute_caps_and_three_state_closure() -> None:
    protocol = section(plan_text(), "## 9.", "## 10.")

    for field in (
        "max_principals",
        "max_sessions",
        "minimum_evidence_n",
        "minimum_duration",
        "maximum_duration",
    ):
        assert field in protocol
    assert "PASS\nBLOCKED\nCONTINUE_OBSERVATION" in protocol
    assert "disable phase gate" in protocol
    assert "verify zero new operations" in protocol


def test_all_phase_specific_hard_stop_sets_exist() -> None:
    gates = section(plan_text(), "## 10.", "## Task 0")

    write = set(re.findall(r"^WRITE_[A-Z0-9_]+$", gates, re.MULTILINE))
    read = set(re.findall(r"^READ_[A-Z0-9_]+$", gates, re.MULTILINE))
    assist = set(re.findall(r"^C1A_[A-Z0-9_]+$", gates, re.MULTILINE))
    assert len(write) == 17
    assert len(read) == 13
    assert len(assist) == 16
    assert "WRITE_ABSOLUTE_CAP_EXCEEDED" in write
    assert "READ_PROPOSAL_OR_OUTBOX_CREATED" in read
    assert "C1A_MEMORY_FACT_IN_PROVIDER_PAYLOAD" in assist


def test_phase_approvals_cannot_be_reused() -> None:
    text = plan_text()

    assert "phase approval 不可复用" in section(text, "### 8.3", "## 9.")
    assert "新批准不可复用 Write" in text
    assert "不把其批准复用于 Principal Memory" in text
    assert "实现批准、生产批准和扩容批准相互独立" in text


def test_markdown_contract_defers_runtime_invariants_to_code_tests() -> None:
    strategy = section(plan_text(), "### 8.3", "## 9.")
    runtime_contracts = (
        "Read 不创建 proposal/outbox",
        "production mode 禁止 Null identity/extractor",
        "Consent 各 purpose 独立 grant/revoke",
        "request-scoped Principal 与 async owner isolation",
        "disable/delete 后 zero new operation",
        "Correct 原子事务和 exclusive active unique constraint",
        "same-session-input score/report parity",
        "C1-A Provider payload 不含 Principal fact",
    )

    for contract in runtime_contracts:
        assert contract in strategy


def test_definition_of_done_is_contiguous_and_keeps_future_boundaries_closed() -> None:
    text = plan_text()
    definition = section(text, "## 14. Definition of Done", "## 15.")
    items = [
        int(number)
        for number in re.findall(r"^(\d+)\. ", definition, re.MULTILINE)
    ]

    assert items == list(range(1, 36))
    assert "C1-B、扩容和 GA 未授权" in definition
    assert "EXPANSION=NOT_AUTHORIZED" in text
    assert "GENERAL_AVAILABILITY=NOT_AUTHORIZED" in text


def test_revision_status_remains_truthful_and_not_authorized() -> None:
    status = section(plan_text(), "**Status at revision:**", "**授权边界：**")

    assert "HOSTED_PRODUCTIZATION_DECISION=NOT_APPROVED" in status
    assert "PRODUCTION_DATA_USE_SPEC=NOT_APPROVED" in status
    assert "PRODUCTION_BUDGET_SHADOW=NOT_RUN" in status
    assert "PRINCIPAL_WRITE_SHADOW_PRODUCTION=NOT_AUTHORIZED" in status
    assert "PRINCIPAL_READ_SHADOW_PRODUCTION=NOT_AUTHORIZED" in status
    assert "IMPLEMENTATION=NOT_AUTHORIZED" in status
    assert "PRODUCTION_CANARY=NOT_AUTHORIZED" in status


def test_plan_does_not_invent_normative_memory_requirement_ids() -> None:
    assert not re.search(
        r"\bMEM(?:[-_][A-Z0-9]+)+[-_]\d+\b",
        plan_text(),
        re.IGNORECASE,
    )
