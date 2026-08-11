# 阶段一：核心大脑替换 SDD

## 目标

阶段一的目标是把当前 MVP 中的硬编码出题和固定追问逻辑替换为 LLM 驱动逻辑。

具体目标：

- 引入 `langchain-openai` 作为默认 LLM 接入层。
- 默认模型使用 `deepseekv4-pro`。
- 使用 Pydantic schema + `with_structured_output` 让 LLM 生成结构化面试大纲。
- 使用 LLM 根据当前问题、候选人回答和最近上下文动态生成追问。
- 保持现有 FastAPI、静态页面和测试闭环可运行。

## 当前问题

当前实现主要位于：

- `app/services/prep.py`
- `app/services/session.py`

目前的问题是：

- `prep.py` 依赖 `TECH_KEYWORDS` 和 `_infer_role_title` 生成题目。
- `session.py` 使用固定模板生成追问。
- 现有测试依赖硬编码标题和无参 `InterviewSessionStore()`，如果直接替换默认 LLM，会在没有 API Key 的环境中失败。
- 追问接口只接收当前问题和当前回答，缺少最近对话上下文。
- LLM 调用失败时缺少降级策略，容易把 API 变成 500。

## 设计原则

- 自动化测试不调用真实 LLM，全部通过 fake LLM 完成。
- 真实 LLM 接入集中在 `app/services/llm.py`，业务模块不直接创建 `ChatOpenAI`。
- LLM 生成的面试大纲必须经过 Pydantic 结构校验。
- API Key 从环境变量读取，不写入代码。
- 默认模型为 `deepseekv4-pro`，并允许通过 `OPENAI_MODEL` 覆盖。
- `ChatOpenAI` 默认 `temperature=0.2`，降低结构化输出波动。
- LLM 失败时必须降级到本地 fallback，而不是直接导致接口 500。
- 阶段一只替换“出题”和“追问”，不引入数据库、Redis、Celery、RAG 和完整 LangGraph。

## 技术依据

LangChain 官方文档说明：

- 使用 OpenAI 兼容模型需要安装 `langchain-openai`，并配置 API Key。
- `ChatOpenAI` 支持 structured output。
- 单次模型调用可以使用 `llm.with_structured_output(PydanticModel, method="json_schema")` 返回结构化 Pydantic 对象。

参考：

- https://docs.langchain.com/oss/python/integrations/chat/openai
- https://docs.langchain.com/oss/python/langchain/structured-output

## 模块设计

### 1. LLM 基础设施模块

新增文件：

- `app/services/llm.py`

职责：

- 读取 `OPENAI_API_KEY`、`OPENAI_MODEL`、`OPENAI_BASE_URL`。
- 创建 `ChatOpenAI` 实例。
- 定义业务层依赖的 `InterviewLLM` 协议。
- 提供 `OpenAIInterviewLLM` 实现。
- 支持测试注入 fake LLM。

协议接口：

```python
class InterviewLLM(Protocol):
    def generate_plan(self, job_description: str, resume_text: str) -> InterviewPlan:
        ...

    def generate_followup(self, context: list[dict[str, str]]) -> str:
        ...
```

追问接口使用 `context`，而不是 `question_prompt + answer` 两个字符串。这样可以把最近 1-2 轮 Q&A 一起传给模型。

### 2. 结构化输出 schema

当前 `InterviewPlan` 和 `InterviewQuestion` 是 dataclass。阶段一迁移到 Pydantic：

```python
class InterviewQuestion(BaseModel):
    id: str
    kind: Literal["project", "technical", "system-design", "behavioral"]
    prompt: str
    focus: str


class InterviewPlan(BaseModel):
    title: str
    questions: list[InterviewQuestion]
```

原因：

- Pydantic 可以直接作为 `with_structured_output` 的 schema。
- FastAPI 返回结构仍然清晰。
- 后续 ReviewGraph 和数据库 JSONB 可以复用同一结构。

### 3. 动态生成面试大纲

修改文件：

- `app/services/prep.py`

目标：

- 移除 `TECH_KEYWORDS` 匹配。
- 移除 `_infer_role_title` 这类规则式判断。
- `prepare_interview` 接收可注入的 `InterviewLLM`。
- 默认使用真实 `OpenAIInterviewLLM`。
- 测试中所有调用必须传入 fake LLM，避免读取真实 API Key。
- 如果 LLM 失败，降级到本地 fallback plan。

建议函数签名：

```python
def prepare_interview(
    job_description: str,
    resume_text: str,
    llm: InterviewLLM | None = None,
) -> InterviewPlan:
    ...
```

### 4. 动态上下文追问

修改文件：

- `app/services/session.py`

目标：

- 移除固定追问模板。
- `InterviewSessionStore` 接收 `InterviewLLM`。
- 第一次提交某题回答时，构造最近上下文并调用 LLM 生成追问。
- 第二次提交该题回答时，推进到下一题。
- 如果 LLM 失败，降级到本地 fallback follow-up。

上下文格式：

```python
[
    {"role": "interviewer", "content": "当前问题"},
    {"role": "candidate", "content": "候选人回答"},
]
```

后续可以扩展为最近 1-2 轮 Q&A。

## 运行时配置

环境变量：

- `OPENAI_API_KEY`：API Key。
- `OPENAI_MODEL`：模型名，默认 `deepseekv4-pro`。
- `OPENAI_BASE_URL`：可选，用于兼容 OpenAI API 的代理或国产模型服务。

## 测试策略

测试分三层：

- 单元测试：使用 fake LLM，验证业务行为。
- API 测试：通过依赖注入使用 fake LLM，避免真实 API Key。
- 手动验证：配置真实 API Key 后，运行服务并观察真实题目和追问。

必须同步修改现有测试：

- `test_prep_service.py` 不能再断言硬编码 `_infer_role_title` 产生的标题，必须改成 fake LLM 返回的标题。
- `test_session_service.py` 不能再使用无参 `InterviewSessionStore()`，必须传入 fake LLM。
- `tests/acceptance/test_api.py` 不能通过多重继承拼 fake，应使用一个单独 fake 类实现 `generate_plan` 和 `generate_followup`。

自动测试不调用真实 LLM，避免网络、费用和不稳定输出影响 TDD。

## 非目标

阶段一不做：

- LangGraph 子图落地。
- 数据库持久化。
- RAG 检索。
- 评分报告。
- WebSocket。
- 多 Agent 真正拆分。

这些放到后续阶段。
