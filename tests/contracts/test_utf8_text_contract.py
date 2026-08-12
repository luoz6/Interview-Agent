from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

RUNTIME_TEXT_FILES = (
    "frontend/src/components/AppShell.jsx",
    "frontend/src/components/PrimaryNav.jsx",
    "frontend/src/components/navigation.js",
    "frontend/src/pages/StartPage.jsx",
    "frontend/src/pages/InterviewPage.jsx",
    "frontend/src/pages/ReportProcessingPage.jsx",
    "frontend/src/pages/ReportDetailPage.jsx",
    "frontend/src/pages/ReportsPage.jsx",
    "frontend/src/pages/HelpPage.jsx",
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
    "frontend/src/components/AppShell.jsx": (
        "面试智能体",
    ),
    "frontend/src/components/PrimaryNav.jsx": (
        "主导航",
    ),
    "frontend/src/components/navigation.js": (
        "准备",
        "报告",
        "帮助",
    ),
    "frontend/src/pages/StartPage.jsx": (
        "编辑两份源文档，生成有证据约束的技术面试计划。",
        "岗位 JD",
        "候选人经历",
        "生成面试计划",
    ),
    "frontend/src/pages/InterviewPage.jsx": (
        "围绕当前问题完整说明判断、方案、取舍与验证",
        "你的回答",
        "结束面试",
        "专注模式",
    ),
    "frontend/src/pages/ReportProcessingPage.jsx": (
        "正在整理本轮报告",
        "生成阶段",
        "运行诊断",
    ),
    "frontend/src/pages/ReportDetailPage.jsx": (
        "结构化面评报告",
        "逐题反馈",
        "证据引用",
        "下载 PDF",
    ),
    "frontend/src/pages/ReportsPage.jsx": (
        "报告中心",
        "搜索岗位、摘要或标签",
        "重新排队",
    ),
    "frontend/src/pages/HelpPage.jsx": (
        "按实际任务查找操作，不需要理解内部运行架构",
        "恢复手册",
        "报告失败",
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
