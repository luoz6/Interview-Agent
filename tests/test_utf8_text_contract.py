from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

RUNTIME_TEXT_FILES = (
    "app/static/shared-ui.js",
    "app/static/prep.js",
    "app/static/interview.js",
    "app/static/report-processing.js",
    "app/static/report-detail.js",
    "app/test1.html",
    "app/test3.html",
    "README.md",
    "docs/local-v1-runbook.md",
)

FORBIDDEN_MOJIBAKE_FRAGMENTS = (
    "妯℃嫙",
    "闈㈣瘯",
    "鏅鸿兘",
    "鎶ュ憡",
    "缂哄皯",
    "浼氳瘽",
    "鏆傛棤",
    "绛夊緟",
    "鐢熸垚",
    "閫愰",
    "璇勪及",
    "寮犲悓瀛",
    "鍊欓",
    "涓嶅寘",
    "鐪熷疄",
)

EXPECTED_PHRASES = {
    "app/static/shared-ui.js": (
        "知识广度",
        "技术深度",
        "当前题",
        "待进行",
        "等待识别岗位标签",
    ),
    "app/static/prep.js": (
        "请先填写岗位 JD",
        "草稿已保存",
        "Knowledge Agent 已完成考点预热。",
        "面试计划已生成",
    ),
    "app/static/interview.js": (
        "缺少 session_id，请从准备页开始面试",
        "会话状态已刷新",
        "暂无对话消息。",
        "回答不能为空",
    ),
    "app/static/report-processing.js": (
        "报告生成尚未开始。",
        "暂无任务 ID",
        "暂无生成事件。",
        "报告暂不可用，请稍后重试。",
    ),
    "app/static/report-detail.js": (
        "暂无维度分。",
        "暂无逐题反馈。",
        "逐题评估链路",
        "报告仍在生成中",
        "兜底报告",
    ),
    "app/test1.html": (
        "结构化面评报告",
        "面试智能体",
        "逐题评估链路",
        "下载报告 (PDF)",
    ),
    "app/test3.html": (
        "模拟面试进行中",
        "面试智能体",
        "按 Enter 提交，Shift+Enter 换行。",
    ),
    "README.md": (
        "不包含登录",
        "不包含 Docker Compose",
    ),
    "docs/local-v1-runbook.md": (
        "## 6. 真实浏览器验收",
        "逐题评估链路",
    ),
}


def read_text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_runtime_user_visible_text_has_no_known_mojibake_fragments():
    offenders: list[str] = []
    for relative_path in RUNTIME_TEXT_FILES:
        text = read_text(relative_path)
        for fragment in FORBIDDEN_MOJIBAKE_FRAGMENTS:
            if fragment in text:
                offenders.append(f"{relative_path}: {fragment}")

    assert offenders == []


def test_runtime_user_visible_text_contains_readable_chinese_phrases():
    missing: list[str] = []
    for relative_path, phrases in EXPECTED_PHRASES.items():
        text = read_text(relative_path)
        for phrase in phrases:
            if phrase not in text:
                missing.append(f"{relative_path}: {phrase}")

    assert missing == []
