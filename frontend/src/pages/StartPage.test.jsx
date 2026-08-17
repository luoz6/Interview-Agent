import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { useMaterialsMock } = vi.hoisted(() => ({
  useMaterialsMock: vi.fn(),
}));

vi.mock("../materials/useMaterials", () => ({
  useMaterials: useMaterialsMock,
}));

import { StartPage } from "./StartPage";

const familyId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const questionOne = "11111111-1111-4111-8111-111111111111";
const questionTwo = "22222222-2222-4222-8222-222222222222";
const readyMaterialId = "66666666-6666-4666-8666-666666666666";
const processingMaterialId = "77777777-7777-4777-8777-777777777777";
const failedMaterialId = "88888888-8888-4888-8888-888888888888";
const disabledMaterialId = "99999999-9999-4999-8999-999999999999";
const materialItems = [
  {
    documentId: readyMaterialId,
    displayName: "Interview-Agent 项目设计.md",
    status: "ready",
    enabled: true,
  },
  {
    documentId: processingMaterialId,
    displayName: "Redis 学习笔记.txt",
    status: "processing",
    enabled: true,
  },
  {
    documentId: failedMaterialId,
    displayName: "Java 并发总结.md",
    status: "failed",
    enabled: true,
  },
  {
    documentId: disabledMaterialId,
    displayName: "旧版架构说明.md",
    status: "disabled",
    enabled: false,
  },
];
const defaultConfiguration = {
  difficulty: "intermediate",
  target_duration_minutes: 30,
  focus_preset: "balanced",
  question_type_budget: {
    project: 1,
    technical: 2,
    "system-design": 1,
    behavioral: 1,
  },
  expected_followup_budget: 5,
  max_followups_per_question: 2,
  generator_version: "plan-generator-v2",
  followup_policy_version: "fixed_v1",
};

function revisionResponse(revision = 1, overrides = {}) {
  const questions = overrides.questions || [
    {
      question_id: questionOne,
      position: 1,
      question_text:
        revision === 1
          ? "How do you prevent a cache stampede?"
          : "How do you safely prevent a cache stampede?",
      focus: "cache resilience",
      question_type: "technical",
      difficulty: "intermediate",
      expected_minutes: 6,
      expected_followups: 1,
      origin: revision === 1 ? "generated" : "edited",
      replaces_question_id: null,
      knowledge_binding: {
        schema_version: "plan-question-knowledge-binding-v1",
        status: "unbound",
        evidence_ids: [],
        reason_code: "no_grounded_evidence",
      },
    },
    {
      question_id: questionTwo,
      position: 2,
      question_text: "Design an idempotent payment workflow.",
      focus: "idempotency",
      question_type: "system-design",
      difficulty: "intermediate",
      expected_minutes: 8,
      expected_followups: 1,
      origin: "generated",
      replaces_question_id: null,
      knowledge_binding: {
        schema_version: "plan-question-knowledge-binding-v1",
        status: "unbound",
        evidence_ids: [],
        reason_code: "no_grounded_evidence",
      },
    },
  ];
  const legacyQuestions = questions.map((question) => ({
    id: question.question_id,
    prompt: question.question_text,
    focus: question.focus,
    kind: question.question_type,
  }));
  const legacy = {
    title: "Backend interview plan",
    questions: legacyQuestions,
    prep_context: {
      knowledge_status: "empty",
      topics: [],
      evidence_refs: [],
    },
  };
  return {
    ...legacy,
    legacy_plan: legacy,
    job_tags: ["Redis"],
    plan_family_id: familyId,
    plan_revision_id:
      revision === 1
        ? "33333333-3333-4333-8333-333333333333"
        : revision === 2
          ? "44444444-4444-4444-8444-444444444444"
          : "55555555-5555-4555-8555-555555555555",
    revision,
    plan_sha256: String(revision).repeat(64),
    plan: {
      schema_version: "interview-plan-v2",
      title: "Backend interview plan",
      configuration_snapshot: defaultConfiguration,
      knowledge_scope: {
        schema_version: "interview-knowledge-scope-v1",
        include_system_knowledge: true,
        selected_documents: [],
      },
      questions,
    },
    ...overrides,
  };
}

function response(payload, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? "OK" : "Error",
    json: async () => payload,
  });
}

function deferredResponse() {
  let resolve;
  const promise = new Promise((next) => {
    resolve = next;
  });
  return { promise, resolve };
}

function sourceFileInput(label) {
  const labeled = screen.getByLabelText(label);
  return labeled.matches("input")
    ? labeled
    : labeled.querySelector('input[type="file"]');
}

function sourceImportPayload(target, filename, text, overrides = {}) {
  return {
    target,
    filename,
    media_type: filename.endsWith(".pdf") ? "application/pdf" : "text/plain",
    text,
    character_count: text.length,
    truncated: false,
    warning_codes: [],
    ...overrides,
  };
}

async function resolveDeferred(pending, payload, status = 200) {
  const nextResponse = await response(payload, status);
  await act(async () => {
    pending.resolve(nextResponse);
    await Promise.resolve();
  });
}

async function generatePlan(user, fetchMock) {
  fetchMock.mockImplementationOnce(() => response(revisionResponse(1)));
  await user.type(screen.getByRole("textbox", { name: "岗位 JD" }), "Backend role");
  await user.click(screen.getByRole("tab", { name: /候选人经历/ }));
  await user.type(screen.getByRole("textbox", { name: "简历内容" }), "Backend resume");
  await user.click(screen.getByRole("button", { name: "生成面试计划" }));
  await screen.findByText("R1");
}

describe("StartPage editable plan workflow", () => {
  let fetchMock;

  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    useMaterialsMock.mockReturnValue({
      items: [],
      availability: "ready",
      refresh: vi.fn(),
    });
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("imports a PDF job description through the shared multipart client", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation((path) => {
      if (path === "/api/prep/source-imports") {
        return response({
          target: "job_description",
          filename: "backend-role.pdf",
          media_type: "application/pdf",
          text: "Backend engineer\nDistributed systems",
          character_count: 36,
          truncated: false,
          warning_codes: [],
        });
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    render(<StartPage />);

    const input = sourceFileInput("导入当前岗位文档");
    expect(input).toHaveAttribute(
      "accept",
      ".pdf,.docx,.md,.txt,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/markdown,text/plain",
    );
    expect(screen.getByText("导入文件")).toBeInTheDocument();

    const file = new File(["%PDF-test"], "backend-role.pdf", {
      type: "application/pdf",
    });
    await user.upload(input, file);

    await waitFor(() => expect(
      screen.getByRole("textbox", { name: "岗位 JD" }),
    ).toHaveValue("Backend engineer\nDistributed systems"));
    const importCall = fetchMock.mock.calls.find(
      ([path]) => path === "/api/prep/source-imports",
    );
    expect(importCall).toBeDefined();
    expect(importCall[1].method).toBe("POST");
    expect(importCall[1].body).toBeInstanceOf(FormData);
    expect(importCall[1].body.get("target")).toBe("job_description");
    expect(importCall[1].body.get("file")).toBe(file);
    expect(importCall[1].headers).toBeUndefined();
    expect(screen.getByText("backend-role.pdf · 已导入")).toBeInTheDocument();
    expect(screen.getByText(/生成计划前仍可继续编辑/)).toBeInTheDocument();

    await user.type(
      screen.getByRole("textbox", { name: "岗位 JD" }),
      "\nUser edit",
    );
    expect(screen.getByRole("textbox", { name: "岗位 JD" })).toHaveValue(
      "Backend engineer\nDistributed systems\nUser edit",
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("imports a truncated DOCX resume with the exact resume target", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation((path) => {
      if (path === "/api/prep/source-imports") {
        return response({
          target: "resume_text",
          filename: "candidate.docx",
          media_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
          text: "Platform engineer",
          character_count: 17,
          truncated: true,
          warning_codes: ["text_truncated"],
        });
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    render(<StartPage />);
    await user.click(screen.getByRole("tab", { name: /候选人经历/ }));

    const file = new File(["PK-test"], "candidate.docx", {
      type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    });
    await user.upload(sourceFileInput("导入当前经历文档"), file);

    await waitFor(() => expect(
      screen.getByRole("textbox", { name: "简历内容" }),
    ).toHaveValue("Platform engineer"));
    const importCall = fetchMock.mock.calls.find(
      ([path]) => path === "/api/prep/source-imports",
    );
    expect(importCall[1].body.get("target")).toBe("resume_text");
    expect(importCall[1].body.get("file")).toBe(file);
    expect(screen.getByText("candidate.docx · 已截断")).toBeInTheDocument();
    expect(screen.getByText(/后续内容未导入/)).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("text_truncated");
  });

  it("keeps both SourceEditors editable while only the current one shows extraction", async () => {
    const user = userEvent.setup();
    const pending = deferredResponse();
    fetchMock.mockImplementation((path) => {
      if (path === "/api/prep/source-imports") return pending.promise;
      throw new Error(`Unexpected request: ${path}`);
    });
    render(<StartPage />);
    await user.click(screen.getByRole("tab", { name: /并排查看/ }));

    const jdFile = sourceFileInput("导入当前岗位文档");
    const resumeFile = sourceFileInput("导入当前经历文档");
    await user.upload(
      jdFile,
      new File(["# Role"], "role.md", { type: "text/markdown" }),
    );

    await waitFor(() => expect(
      screen.getByText("未导入文件 · 正在提取"),
    ).toBeInTheDocument());
    expect(jdFile).toBeEnabled();
    expect(screen.getByRole("textbox", { name: "岗位 JD" })).toBeEnabled();
    expect(resumeFile).toBeEnabled();
    expect(screen.getByRole("textbox", { name: "简历内容" })).toBeEnabled();

    await resolveDeferred(pending, {
      target: "job_description",
      filename: "role.md",
      media_type: "text/markdown",
      text: "# Role",
      character_count: 6,
      truncated: false,
      warning_codes: [],
    });
    await waitFor(() => expect(jdFile).toBeEnabled());
  });

  it("keeps only the latest same-source response and aborts the older request silently", async () => {
    const user = userEvent.setup();
    const requests = [];
    fetchMock.mockImplementation((path, options) => {
      if (path !== "/api/prep/source-imports") {
        throw new Error(`Unexpected request: ${path}`);
      }
      const pending = deferredResponse();
      requests.push({ ...pending, signal: options.signal });
      return pending.promise;
    });
    render(<StartPage />);

    await user.upload(
      sourceFileInput("导入当前岗位文档"),
      new File(["old"], "old.txt", { type: "text/plain" }),
    );
    await waitFor(() => expect(requests).toHaveLength(1));
    await user.upload(
      sourceFileInput("导入当前岗位文档"),
      new File(["new"], "new.txt", { type: "text/plain" }),
    );
    await waitFor(() => expect(requests).toHaveLength(2));

    expect(requests[0].signal.aborted).toBe(true);
    expect(requests[1].signal.aborted).toBe(false);
    await resolveDeferred(
      requests[1],
      sourceImportPayload("job_description", "new.txt", "New JD"),
    );
    await waitFor(() => expect(
      screen.getByRole("textbox", { name: "岗位 JD" }),
    ).toHaveValue("New JD"));

    await resolveDeferred(
      requests[0],
      sourceImportPayload("job_description", "old.txt", "Old JD"),
    );
    expect(screen.getByRole("textbox", { name: "岗位 JD" })).toHaveValue("New JD");
    expect(screen.getByText("new.txt · 已导入")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(/old\.txt|请求已取消|导入失败/);
  });

  it("lets manual source edits abort an import and win without an error notice", async () => {
    const user = userEvent.setup();
    let importSignal;
    fetchMock.mockImplementation((path, options) => {
      if (path !== "/api/prep/source-imports") {
        throw new Error(`Unexpected request: ${path}`);
      }
      importSignal = options.signal;
      return new Promise((_resolve, reject) => {
        options.signal.addEventListener("abort", () => {
          reject(new DOMException("Aborted", "AbortError"));
        }, { once: true });
      });
    });
    render(<StartPage />);
    const jdInput = screen.getByRole("textbox", { name: "岗位 JD" });
    await user.type(jdInput, "Current JD");
    await user.upload(
      sourceFileInput("导入当前岗位文档"),
      new File(["pending"], "pending.txt", { type: "text/plain" }),
    );
    await waitFor(() => expect(importSignal).toBeInstanceOf(AbortSignal));

    await user.type(jdInput, " wins");

    await waitFor(() => expect(importSignal.aborted).toBe(true));
    expect(jdInput).toHaveValue("Current JD wins");
    expect(screen.getByText("未导入文件")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(/请求已取消|导入失败|REQUEST_ABORTED/);
  });

  it("runs job and resume imports in parallel without cross-cancellation", async () => {
    const user = userEvent.setup();
    const requests = new Map();
    fetchMock.mockImplementation((path, options) => {
      if (path !== "/api/prep/source-imports") {
        throw new Error(`Unexpected request: ${path}`);
      }
      const pending = deferredResponse();
      requests.set(options.body.get("target"), {
        ...pending,
        signal: options.signal,
      });
      return pending.promise;
    });
    render(<StartPage />);
    await user.click(screen.getByRole("tab", { name: /并排查看/ }));

    await user.upload(
      sourceFileInput("导入当前岗位文档"),
      new File(["job"], "job.txt", { type: "text/plain" }),
    );
    await user.upload(
      sourceFileInput("导入当前经历文档"),
      new File(["resume"], "resume.txt", { type: "text/plain" }),
    );
    await waitFor(() => expect(requests.size).toBe(2));
    expect(requests.get("job_description").signal.aborted).toBe(false);
    expect(requests.get("resume_text").signal.aborted).toBe(false);

    await resolveDeferred(
      requests.get("resume_text"),
      sourceImportPayload("resume_text", "resume.txt", "Resume text"),
    );
    await resolveDeferred(
      requests.get("job_description"),
      sourceImportPayload("job_description", "job.txt", "Job text"),
    );
    expect(screen.getByRole("textbox", { name: "岗位 JD" })).toHaveValue("Job text");
    expect(screen.getByRole("textbox", { name: "简历内容" })).toHaveValue("Resume text");
    expect(screen.getByText("job.txt · 已导入")).toBeInTheDocument();
    expect(screen.getByText("resume.txt · 已导入")).toBeInTheDocument();
  });

  it("invalidates a pending import when the workspace is cleared", async () => {
    const user = userEvent.setup();
    const pending = deferredResponse();
    let importSignal;
    fetchMock.mockImplementation((path, options) => {
      if (path !== "/api/prep/source-imports") {
        throw new Error(`Unexpected request: ${path}`);
      }
      importSignal = options.signal;
      return pending.promise;
    });
    render(<StartPage />);
    await user.type(screen.getByRole("textbox", { name: "岗位 JD" }), "Current JD");
    await user.upload(
      sourceFileInput("导入当前岗位文档"),
      new File(["old"], "old.txt", { type: "text/plain" }),
    );
    await waitFor(() => expect(importSignal).toBeInstanceOf(AbortSignal));

    await user.click(screen.getByRole("button", { name: "清空当前画布" }));
    await user.click(screen.getByRole("button", { name: "确认清空画布" }));
    expect(importSignal.aborted).toBe(true);
    await resolveDeferred(
      pending,
      sourceImportPayload("job_description", "old.txt", "Old response"),
    );

    expect(screen.getByRole("textbox", { name: "岗位 JD" })).toHaveValue("");
    expect(screen.getByText("未导入文件")).toBeInTheDocument();
    expect(screen.getByText(/当前画布已清空/)).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(/old\.txt|Old response/);
  });

  it("invalidates a pending import after a successful draft restore", async () => {
    const user = userEvent.setup();
    const pending = deferredResponse();
    let importSignal;
    fetchMock.mockImplementation((path, options) => {
      if (path === "/api/prep/source-imports") {
        importSignal = options.signal;
        return pending.promise;
      }
      if (path === "/api/interview-drafts/draft-race") {
        return response({
          draft_id: "draft-race",
          durability: "memory",
          job_description: "Restored JD",
          resume_text: "Restored resume",
          job_tags: [],
          plan_status: "stale",
          plan_family_id: null,
          latest_plan_revision_id: null,
        });
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    render(<StartPage />);
    await user.type(screen.getByRole("textbox", { name: "岗位 JD" }), "Current JD");
    window.localStorage.setItem("interview-agent:draft-id", "draft-race");
    await user.upload(
      sourceFileInput("导入当前岗位文档"),
      new File(["old"], "old.txt", { type: "text/plain" }),
    );
    await waitFor(() => expect(importSignal).toBeInstanceOf(AbortSignal));

    await user.click(screen.getByRole("button", { name: "恢复草稿" }));
    await user.click(screen.getByRole("button", { name: "确认恢复草稿" }));
    await waitFor(() => expect(
      screen.getByRole("textbox", { name: "岗位 JD" }),
    ).toHaveValue("Restored JD"));
    expect(importSignal.aborted).toBe(true);
    await resolveDeferred(
      pending,
      sourceImportPayload("job_description", "old.txt", "Old response"),
    );

    expect(screen.getByRole("textbox", { name: "岗位 JD" })).toHaveValue("Restored JD");
    expect(screen.getByText("来自匿名草稿")).toBeInTheDocument();
    expect(screen.getByText(/草稿已恢复/)).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(/old\.txt|Old response/);
  });

  it("aborts both source requests on unmount without publishing late state", async () => {
    const user = userEvent.setup();
    const requests = [];
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    fetchMock.mockImplementation((path, options) => {
      if (path !== "/api/prep/source-imports") {
        throw new Error(`Unexpected request: ${path}`);
      }
      const pending = deferredResponse();
      requests.push({ ...pending, signal: options.signal });
      return pending.promise;
    });
    const { unmount } = render(<StartPage />);
    await user.click(screen.getByRole("tab", { name: /并排查看/ }));
    await user.upload(
      sourceFileInput("导入当前岗位文档"),
      new File(["job"], "job.txt", { type: "text/plain" }),
    );
    await user.upload(
      sourceFileInput("导入当前经历文档"),
      new File(["resume"], "resume.txt", { type: "text/plain" }),
    );
    await waitFor(() => expect(requests).toHaveLength(2));

    unmount();
    expect(requests.every(({ signal }) => signal.aborted)).toBe(true);
    await resolveDeferred(
      requests[0],
      sourceImportPayload("job_description", "job.txt", "Late job"),
    );
    await resolveDeferred(
      requests[1],
      sourceImportPayload("resume_text", "resume.txt", "Late resume"),
    );
    expect(consoleError).not.toHaveBeenCalled();
    consoleError.mockRestore();
  });

  it("invalidates an existing plan after import without saving or regenerating", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation((path) => {
      if (path === "/api/prep") return response(revisionResponse(1));
      if (path === "/api/prep/source-imports") {
        return response({
          target: "job_description",
          filename: "replacement.txt",
          media_type: "text/plain",
          text: "Replacement JD",
          character_count: 14,
          truncated: false,
          warning_codes: [],
        });
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    render(<StartPage />);
    await user.type(screen.getByRole("textbox", { name: "岗位 JD" }), "Old JD");
    await user.click(screen.getByRole("tab", { name: /候选人经历/ }));
    await user.type(screen.getByRole("textbox", { name: "简历内容" }), "Resume");
    await user.click(screen.getByRole("button", { name: "生成面试计划" }));
    await screen.findByText("R1");
    expect(screen.getByRole("button", { name: "开始本次面试" })).toBeEnabled();
    await user.click(screen.getByRole("tab", { name: /岗位 JD/ }));

    await user.upload(
      sourceFileInput("导入当前岗位文档"),
      new File(["Replacement JD"], "replacement.txt", { type: "text/plain" }),
    );

    await waitFor(() => expect(
      screen.getByRole("textbox", { name: "岗位 JD" }),
    ).toHaveValue("Replacement JD"));
    expect(screen.queryByText("R1")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "生成面试计划" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "开始本次面试" })).toBeDisabled();
    expect(screen.getByText(/原面试计划已失效，请重新生成/)).toBeInTheDocument();
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      "/api/prep",
      "/api/prep/source-imports",
    ]);
  });

  it("preserves text, file state, and plan when a later import fails safely", async () => {
    const user = userEvent.setup();
    let imports = 0;
    fetchMock.mockImplementation((path) => {
      if (path === "/api/prep/source-imports") {
        imports += 1;
        if (imports === 1) {
          return response({
            target: "job_description",
            filename: "current.txt",
            media_type: "text/plain",
            text: "Current JD",
            character_count: 10,
            truncated: false,
            warning_codes: [],
          });
        }
        return response({
          detail: {
            code: "invalid_file_signature",
            message: "文件格式不一致，请重新选择。",
            parser: "private parser traceback",
            content_sha256: "secret-hash",
          },
        }, 422);
      }
      if (path === "/api/prep") return response(revisionResponse(1));
      throw new Error(`Unexpected request: ${path}`);
    });
    render(<StartPage />);

    await user.upload(
      sourceFileInput("导入当前岗位文档"),
      new File(["Current JD"], "current.txt", { type: "text/plain" }),
    );
    await waitFor(() => expect(
      screen.getByRole("textbox", { name: "岗位 JD" }),
    ).toHaveValue("Current JD"));
    await user.click(screen.getByRole("tab", { name: /候选人经历/ }));
    await user.type(screen.getByRole("textbox", { name: "简历内容" }), "Resume");
    await user.click(screen.getByRole("button", { name: "生成面试计划" }));
    await screen.findByText("R1");
    await user.click(screen.getByRole("tab", { name: /岗位 JD/ }));

    await user.upload(
      sourceFileInput("导入当前岗位文档"),
      new File(["%PDF-bad"], "bad.pdf", { type: "application/pdf" }),
    );

    expect(await screen.findByText(/文件格式不一致，请重新选择/)).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "岗位 JD" })).toHaveValue("Current JD");
    expect(screen.getByText("current.txt · 已导入")).toBeInTheDocument();
    expect(screen.getByText("R1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始本次面试" })).toBeEnabled();
    expect(document.body).not.toHaveTextContent(
      /invalid_file_signature|secret-hash|private parser traceback/,
    );
    expect(fetchMock.mock.calls.filter(([path]) => path === "/api/prep")).toHaveLength(1);
  });

  it("shows every material state but only allows Ready and Enabled items", async () => {
    const user = userEvent.setup();
    useMaterialsMock.mockReturnValue({
      items: materialItems,
      availability: "ready",
      refresh: vi.fn(),
    });
    render(<StartPage />);

    await user.click(screen.getByRole("tab", { name: "就绪" }));

    expect(screen.getByRole("checkbox", { name: /Interview-Agent 项目设计/ })).toBeEnabled();
    expect(screen.getByRole("checkbox", { name: /Redis 学习笔记/ })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: /Java 并发总结/ })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: /旧版架构说明/ })).toBeDisabled();
    expect(screen.getByText("正在提取内容并建立索引")).toBeInTheDocument();
    expect(screen.getByText("可检查文件后重新处理")).toBeInTheDocument();
    expect(screen.getByText("已停用，请先到“我的资料”启用")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /同时使用系统知识/ })).toBeChecked();
  });

  it("supports system-only and explicit-empty generation when Materials are unavailable", async () => {
    const user = userEvent.setup();
    let prepRevision = 0;
    useMaterialsMock.mockReturnValue({
      items: [],
      availability: "unavailable",
      refresh: vi.fn(),
    });
    fetchMock.mockImplementation((path, options) => {
      if (path === "/api/runtime/principal-memory/status") {
        return response({ mode: "disabled", global_enabled: false });
      }
      if (path === "/api/prep") {
        prepRevision += 1;
        const requestScope = JSON.parse(options.body).knowledge_scope;
        const base = revisionResponse(prepRevision);
        return response(revisionResponse(prepRevision, {
          plan: {
            ...base.plan,
            knowledge_scope: {
              schema_version: "interview-knowledge-scope-v1",
              include_system_knowledge: requestScope.include_system_knowledge,
              selected_documents: [],
            },
          },
        }));
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    render(<StartPage />);

    await user.click(screen.getByRole("tab", { name: "就绪" }));
    expect(screen.getByText(/个人资料功能当前未启用/)).toBeInTheDocument();
    await user.type(screen.getByRole("textbox", { name: "岗位 JD" }), "Backend role");
    await user.click(screen.getByRole("tab", { name: /候选人经历/ }));
    await user.type(screen.getByRole("textbox", { name: "简历内容" }), "Backend resume");
    await user.click(screen.getByRole("button", { name: "生成面试计划" }));
    await screen.findByText("R1");

    let prepCalls = fetchMock.mock.calls.filter(([path]) => path === "/api/prep");
    expect(JSON.parse(prepCalls[0][1].body).knowledge_scope).toEqual({
      include_system_knowledge: true,
      selected_document_ids: [],
    });

    await user.click(screen.getByRole("tab", { name: "就绪" }));
    await user.click(screen.getByRole("checkbox", { name: /同时使用系统知识/ }));
    expect(screen.getByRole("button", { name: "开始本次面试" })).toBeDisabled();
    await user.click(screen.getByRole("button", {
      name: "应用资料范围并重新生成",
    }));
    await screen.findByText("R2");

    prepCalls = fetchMock.mock.calls.filter(([path]) => path === "/api/prep");
    expect(JSON.parse(prepCalls[1][1].body).knowledge_scope).toEqual({
      include_system_knowledge: false,
      selected_document_ids: [],
    });
    await user.click(screen.getByRole("tab", { name: "就绪" }));
    expect(screen.getByText("未启用知识来源")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始本次面试" })).toBeEnabled();
  });

  it.each(["loading", "error", "unavailable"])(
    "keeps a restored selected Scope fail closed while the Materials list is %s",
    async (availability) => {
      const base = revisionResponse(1);
      const restored = revisionResponse(1, {
        plan: {
          ...base.plan,
          knowledge_scope: {
            schema_version: "interview-knowledge-scope-v1",
            include_system_knowledge: true,
            selected_documents: [{ document_id: readyMaterialId }],
          },
        },
      });
      window.localStorage.setItem("interview-agent:draft-id", "draft-material-scope");
      useMaterialsMock.mockReturnValue({
        items: materialItems,
        availability,
        refresh: vi.fn(),
      });
      fetchMock.mockImplementation((path) => {
        if (path === "/api/interview-drafts/draft-material-scope") {
          return response({
            draft_id: "draft-material-scope",
            durability: "memory",
            job_description: "Restored backend role",
            resume_text: "Restored backend resume",
            job_tags: ["Backend"],
            plan_status: "active",
            plan_family_id: familyId,
            latest_plan_revision_id: base.plan_revision_id,
          });
        }
        if (path === `/api/interview-plans/${familyId}/revisions/${base.plan_revision_id}`) {
          return response(restored);
        }
        if (path === "/api/runtime/principal-memory/status") {
          return response({ mode: "disabled", global_enabled: false });
        }
        throw new Error(`Unexpected request: ${path}`);
      });

      render(<StartPage />);

      expect(await screen.findByText(/草稿和对应的同一份计划修订已恢复/)).toBeInTheDocument();
      const selectedMaterial = screen.getByRole("checkbox", {
        name: /Interview-Agent 项目设计/,
      });
      await waitFor(() => expect(selectedMaterial).toBeChecked());
      expect(selectedMaterial).toBeDisabled();
      expect(screen.getByText(/已选范围中有资料当前不可用/)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "开始本次面试" })).toBeDisabled();
      expect(fetchMock.mock.calls.some(([path]) => path === "/api/interviews")).toBe(false);
    },
  );

  it("submits only the public Scope request and shows the server-confirmed summary", async () => {
    const user = userEvent.setup();
    const base = revisionResponse(1);
    useMaterialsMock.mockReturnValue({
      items: materialItems,
      availability: "ready",
      refresh: vi.fn(),
    });
    fetchMock.mockImplementation((path) => {
      if (path === "/api/runtime/principal-memory/status") {
        return response({ mode: "disabled", global_enabled: false });
      }
      if (path === "/api/prep") {
        return response(revisionResponse(1, {
          plan: {
            ...base.plan,
            knowledge_scope: {
              schema_version: "interview-knowledge-scope-v1",
              include_system_knowledge: false,
              selected_documents: [{ document_id: readyMaterialId }],
            },
          },
        }));
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    render(<StartPage />);

    await user.click(screen.getByRole("tab", { name: "就绪" }));
    await user.click(screen.getByRole("checkbox", { name: /Interview-Agent 项目设计/ }));
    await user.click(screen.getByRole("checkbox", { name: /同时使用系统知识/ }));
    await user.type(screen.getByRole("textbox", { name: "岗位 JD" }), "Backend role");
    await user.click(screen.getByRole("tab", { name: /候选人经历/ }));
    await user.type(screen.getByRole("textbox", { name: "简历内容" }), "Backend resume");
    await user.click(screen.getByRole("button", { name: "生成面试计划" }));
    await screen.findByText("R1");

    const prepCall = fetchMock.mock.calls.find(([path]) => path === "/api/prep");
    const requestBody = JSON.parse(prepCall[1].body);
    expect(requestBody.knowledge_scope).toEqual({
      include_system_knowledge: false,
      selected_document_ids: [readyMaterialId],
    });
    expect(JSON.stringify(requestBody.knowledge_scope)).not.toMatch(
      /revision|hash|owner|title|internal|embedding|path/i,
    );

    await user.click(screen.getByRole("tab", { name: "就绪" }));
    expect(screen.getByText("服务端已确认")).toBeInTheDocument();
    expect(screen.getByText("1 份个人资料，不使用系统知识")).toBeInTheDocument();
    expect(screen.getAllByText("Interview-Agent 项目设计.md")).toHaveLength(2);

    await user.click(screen.getByRole("checkbox", { name: /同时使用系统知识/ }));
    expect(screen.getByText("资料范围已变更")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始本次面试" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "应用资料范围并重新生成" })).toBeEnabled();
  });

  it("keeps server Scope internals out of the rendered DOM", async () => {
    const user = userEvent.setup();
    const base = revisionResponse(1);
    useMaterialsMock.mockReturnValue({
      items: materialItems,
      availability: "ready",
      refresh: vi.fn(),
    });
    fetchMock.mockImplementationOnce(() => response(revisionResponse(1, {
      plan: {
        ...base.plan,
        knowledge_scope: {
          schema_version: "interview-knowledge-scope-v1",
          include_system_knowledge: true,
          selected_documents: [{
            document_id: readyMaterialId,
            owner_principal_id: "principal-secret",
            document_revision_id: "revision-secret",
            content_sha256: "content-hash-secret",
            selection_sha256: "selection-hash-secret",
            allowed_usages: ["question"],
            created_at: "2026-08-15T08:30:00Z",
          }],
        },
      },
    })));
    const { container } = render(<StartPage />);

    await user.type(screen.getByRole("textbox", { name: "岗位 JD" }), "Backend role");
    await user.click(screen.getByRole("tab", { name: /候选人经历/ }));
    await user.type(screen.getByRole("textbox", { name: "简历内容" }), "Backend resume");
    await user.click(screen.getByRole("button", { name: "生成面试计划" }));
    await screen.findByText("R1");
    await user.click(screen.getByRole("tab", { name: "就绪" }));

    expect(container).toHaveTextContent("系统知识 + 1 份个人资料");
    expect(container.textContent).not.toMatch(
      /principal-secret|revision-secret|content-hash-secret|selection-hash-secret|allowed_usages|created_at/,
    );
  });

  it("requires an explicit user action to remove a missing restored material", async () => {
    const user = userEvent.setup();
    const base = revisionResponse(1);
    const restored = revisionResponse(1, {
      plan: {
        ...base.plan,
        knowledge_scope: {
          schema_version: "interview-knowledge-scope-v1",
          include_system_knowledge: true,
          selected_documents: [{ document_id: readyMaterialId }],
        },
      },
    });
    window.localStorage.setItem("interview-agent:draft-id", "draft-missing-material");
    useMaterialsMock.mockReturnValue({
      items: [],
      availability: "ready",
      refresh: vi.fn(),
    });
    fetchMock.mockImplementation((path) => {
      if (path === "/api/interview-drafts/draft-missing-material") {
        return response({
          draft_id: "draft-missing-material",
          durability: "memory",
          job_description: "Restored backend role",
          resume_text: "Restored backend resume",
          job_tags: ["Backend"],
          plan_status: "active",
          plan_family_id: familyId,
          latest_plan_revision_id: base.plan_revision_id,
        });
      }
      if (path === `/api/interview-plans/${familyId}/revisions/${base.plan_revision_id}`) {
        return response(restored);
      }
      if (path === "/api/runtime/principal-memory/status") {
        return response({ mode: "disabled", global_enabled: false });
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    render(<StartPage />);

    expect(await screen.findByText(/草稿和对应的同一份计划修订已恢复/)).toBeInTheDocument();
    expect(await screen.findByText(/已选范围中有资料当前不可用/)).toBeInTheDocument();
    const start = screen.getByRole("button", { name: "开始本次面试" });
    expect(start).toBeDisabled();
    expect(screen.getByText("系统知识 + 1 份个人资料")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "只保留可用资料" }));
    expect(screen.queryByText(/已选范围中有资料当前不可用/)).not.toBeInTheDocument();
    expect(screen.getByText("资料范围已变更")).toBeInTheDocument();
    expect(start).toBeDisabled();
  });

  it("keeps an unavailable confirmed item unselectable and can remove it safely", async () => {
    const user = userEvent.setup();
    const base = revisionResponse(1);
    useMaterialsMock.mockReturnValue({
      items: materialItems,
      availability: "ready",
      refresh: vi.fn(),
    });
    fetchMock.mockImplementationOnce(() => response(revisionResponse(1, {
      plan: {
        ...base.plan,
        knowledge_scope: {
          schema_version: "interview-knowledge-scope-v1",
          include_system_knowledge: true,
          selected_documents: [{ document_id: disabledMaterialId }],
        },
      },
    })));
    render(<StartPage />);
    await user.type(screen.getByRole("textbox", { name: "岗位 JD" }), "Backend role");
    await user.click(screen.getByRole("tab", { name: /候选人经历/ }));
    await user.type(screen.getByRole("textbox", { name: "简历内容" }), "Backend resume");
    await user.click(screen.getByRole("button", { name: "生成面试计划" }));
    await screen.findByText("R1");
    await user.click(screen.getByRole("tab", { name: "就绪" }));

    const disabledChoice = screen.getByRole("checkbox", { name: /旧版架构说明/ });
    expect(disabledChoice).toBeChecked();
    expect(disabledChoice).toBeDisabled();
    expect(screen.getByRole("button", { name: "开始本次面试" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "只保留可用资料" }));
    expect(disabledChoice).not.toBeChecked();
    expect(screen.getByText("资料范围已变更")).toBeInTheDocument();
  });

  it.each([
    [404, "knowledge_scope_document_not_found"],
    [409, "knowledge_scope_document_unavailable"],
  ])("projects Scope Start errors safely for HTTP %s", async (statusCode, code) => {
    const user = userEvent.setup();
    render(<StartPage />);
    await generatePlan(user, fetchMock);
    fetchMock.mockImplementationOnce(() => response({
      detail: {
        code,
        message: "secret principal-b ownership detail",
      },
    }, statusCode));

    await user.click(screen.getByRole("button", { name: "开始本次面试" }));

    expect(await screen.findByText(
      "本次参考资料已失效或暂不可用。请重新确认资料范围并生成新计划。",
    )).toBeInTheDocument();
    expect(screen.queryByText(/principal-b ownership/)).not.toBeInTheDocument();
    expect(screen.queryByText("计划已不是服务端最新版本。请查看服务端版本后再开始。")).not.toBeInTheDocument();
  });

  it("keeps the live generate action retryable after a request failure", async () => {
    const user = userEvent.setup();
    render(<StartPage />);
    await user.type(screen.getByRole("textbox", { name: "岗位 JD" }), "Backend role");
    await user.click(screen.getByRole("tab", { name: /候选人经历/ }));
    await user.type(screen.getByRole("textbox", { name: "简历内容" }), "Backend resume");

    fetchMock.mockImplementationOnce(() =>
      response({ detail: { code: "provider_timeout", message: "Timed out" } }, 503),
    );
    const generate = screen.getByRole("button", { name: "生成面试计划" });
    await user.click(generate);

    expect(await screen.findByText("Timed out")).toBeInTheDocument();
    expect(generate).toBeEnabled();

    fetchMock.mockImplementationOnce(() => response(revisionResponse(1)));
    await user.click(generate);

    await screen.findByText("R1");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("keeps start disabled for a local draft and adopts the successful server revision", async () => {
    const user = userEvent.setup();
    render(<StartPage />);
    await generatePlan(user, fetchMock);
    const revisionState = screen.getByText("已保存").closest('[role="status"]');
    expect(revisionState).toHaveAttribute("aria-live", "polite");
    expect(revisionState).toHaveTextContent("R1");

    const start = screen.getByRole("button", { name: "开始本次面试" });
    expect(start).toBeEnabled();
    const questionInputs = screen.getAllByRole("textbox", { name: "问题内容" });
    await user.clear(questionInputs[0]);
    await user.type(questionInputs[0], "My safer cache stampede question");
    expect(start).toBeDisabled();
    expect(screen.getByText(/本地修改尚未全部保存/)).toBeInTheDocument();

    fetchMock.mockImplementationOnce(() => response(revisionResponse(2)));
    await user.click(screen.getAllByRole("button", { name: "保存修改" })[0]);

    await screen.findByText("R2");
    expect(start).toBeEnabled();
    const patch = fetchMock.mock.calls.at(-1);
    expect(patch[0]).toBe("/api/interview-plans/" + familyId);
    expect(patch[1].method).toBe("PATCH");
    const body = JSON.parse(patch[1].body);
    expect(body.expected_revision).toBe(1);
    expect(body.operations).toEqual([
      {
        op: "edit_question_text",
        question_id: questionOne,
        question_text: "My safer cache stampede question",
      },
    ]);
  });

  it("binds the readiness memory choice into the single start request", async () => {
    const user = userEvent.setup();
    render(<StartPage />);
    await generatePlan(user, fetchMock);
    fetchMock.mockImplementationOnce(() => response({
      schema_version: "principal-memory-local-status-v1",
      mode: "local_consume",
      global_enabled: true,
      local_consumption_enabled: true,
      deletion_fence_active: false,
      consent: { granted: true, allowed_purposes: ["fact_storage", "local_consume"] },
    }));

    await user.click(screen.getByRole("tab", { name: "就绪" }));
    const memoryChoice = await screen.findByRole("checkbox", {
      name: "在本次面试中使用我的长期记忆",
    });
    expect(memoryChoice).toBeChecked();
    expect(screen.getByRole("link", { name: "查看和管理我的记忆" })).toHaveAttribute(
      "href",
      "/memory-center",
    );
    await user.click(memoryChoice);
    fetchMock.mockImplementationOnce(() =>
      response({ detail: { code: "provider_timeout", message: "start failed" } }, 503),
    );
    await user.click(screen.getByRole("button", { name: "开始本次面试" }));

    await screen.findByText("start failed");
    const [path, options] = fetchMock.mock.calls.at(-1);
    expect(path).toBe("/api/interviews");
    expect(JSON.parse(options.body).principal_memory_mode).toBe("ignore");
  });

  it("inherits the memory setting when readiness was not opened", async () => {
    const user = userEvent.setup();
    render(<StartPage />);
    await generatePlan(user, fetchMock);
    fetchMock.mockImplementationOnce(() =>
      response({ detail: { code: "provider_timeout", message: "start failed" } }, 503),
    );

    await user.click(screen.getByRole("button", { name: "开始本次面试" }));

    await screen.findByText("start failed");
    const [path, options] = fetchMock.mock.calls.at(-1);
    expect(path).toBe("/api/interviews");
    expect(JSON.parse(options.body).principal_memory_mode).toBe("inherit");
  });

  it("preserves local input when save fails", async () => {
    const user = userEvent.setup();
    render(<StartPage />);
    await generatePlan(user, fetchMock);
    const input = screen.getAllByRole("textbox", { name: "问题内容" })[0];
    await user.clear(input);
    await user.type(input, "Keep this local wording");

    fetchMock.mockImplementationOnce(() =>
      response({ detail: { code: "provider_timeout", message: "Timed out" } }, 503),
    );
    await user.click(screen.getAllByRole("button", { name: "保存修改" })[0]);

    await screen.findByText("计划操作失败");
    expect(input).toHaveValue("Keep this local wording");
    expect(screen.getByText(/本地输入没有丢失/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始本次面试" })).toBeDisabled();
  });

  it("shows conflict actions without overwriting local input", async () => {
    const user = userEvent.setup();
    const clipboardWrite = vi
      .spyOn(navigator.clipboard, "writeText")
      .mockResolvedValue(undefined);
    render(<StartPage />);
    await generatePlan(user, fetchMock);
    const input = screen.getAllByRole("textbox", { name: "问题内容" })[0];
    await user.clear(input);
    await user.type(input, "My conflicting question");

    fetchMock.mockImplementationOnce(() =>
      response(
        {
          code: "plan_revision_conflict",
          current_revision: {
            plan_revision_id: "44444444-4444-4444-8444-444444444444",
            revision: 2,
            plan_sha256: "2".repeat(64),
          },
        },
        409,
      ),
    );
    await user.click(screen.getAllByRole("button", { name: "保存修改" })[0]);

    const conflictAlert = await screen.findByRole("alert");
    expect(conflictAlert).toHaveTextContent("计划版本冲突");
    expect(screen.getByText("版本冲突").closest('[role="status"]')).toHaveAttribute("aria-live", "polite");
    expect(input).toHaveValue("My conflicting question");
    expect(screen.getByRole("button", { name: "查看服务端版本" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "复制我的内容" }));
    expect(clipboardWrite).toHaveBeenCalledWith(
      expect.stringContaining("My conflicting question"),
    );
  });

  it("uses keyboard-operable move controls and sends the requested position", async () => {
    const user = userEvent.setup();
    render(<StartPage />);
    await generatePlan(user, fetchMock);
    fetchMock.mockImplementationOnce(() =>
      response(
        revisionResponse(2, {
          questions: [
            revisionResponse(1).plan.questions[1],
            revisionResponse(1).plan.questions[0],
          ].map((question, index) => ({ ...question, position: index + 1 })),
        }),
      ),
    );

    const moveDown = screen.getByRole("button", { name: "将第 1 题下移" });
    moveDown.focus();
    await user.keyboard("{Enter}");

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const body = JSON.parse(fetchMock.mock.calls[1][1].body);
    expect(body.operations[0]).toEqual({
      op: "move_question",
      question_id: questionOne,
      to_position: 2,
    });
  });

  it("requires explicit confirmation before delete", async () => {
    const user = userEvent.setup();
    render(<StartPage />);
    await generatePlan(user, fetchMock);

    await user.click(screen.getAllByRole("button", { name: "删除" })[0]);
    const dialog = screen.getByRole("dialog", { name: "删除第 1 题？" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await user.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await user.click(screen.getAllByRole("button", { name: "删除" })[0]);
    fetchMock.mockImplementationOnce(() =>
      response(
        revisionResponse(2, {
          questions: [
            { ...revisionResponse(1).plan.questions[1], position: 1 },
          ],
        }),
      ),
    );
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "删除并保存",
      }),
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(JSON.parse(fetchMock.mock.calls[1][1].body).operations[0].op).toBe(
      "delete_question",
    );
  });

  it("loads history and restores only after confirmation", async () => {
    const user = userEvent.setup();
    render(<StartPage />);
    await generatePlan(user, fetchMock);
    fetchMock.mockImplementationOnce(() =>
      response({
        plan_family_id: familyId,
        latest_revision: 2,
        revisions: [
          {
            plan_revision_id: "44444444-4444-4444-8444-444444444444",
            revision: 2,
            title: "Backend interview plan",
            question_count: 2,
            created_reason: "edit_question_text",
            is_latest: true,
          },
          {
            plan_revision_id: "33333333-3333-4333-8333-333333333333",
            revision: 1,
            title: "Backend interview plan",
            question_count: 2,
            created_reason: "initial_generation",
            is_latest: false,
          },
        ],
      }),
    );
    await user.click(screen.getByRole("button", { name: "历史版本" }));
    await screen.findByText(/initial_generation/);

    await user.click(screen.getByRole("button", { name: "恢复" }));
    expect(screen.getByRole("dialog", { name: "恢复到 R1？" })).toBeInTheDocument();
    fetchMock.mockImplementationOnce(() => response(revisionResponse(3)));
    await user.click(screen.getByRole("button", { name: "确认恢复" }));

    await screen.findByText("R3");
    const body = JSON.parse(fetchMock.mock.calls[2][1].body);
    expect(body.operations[0]).toEqual({
      op: "restore_revision",
      target_revision_id: "33333333-3333-4333-8333-333333333333",
    });
  });

  it("adds a custom question with safe ungrounded defaults", async () => {
    const user = userEvent.setup();
    render(<StartPage />);
    await generatePlan(user, fetchMock);
    await user.click(screen.getByRole("button", { name: "添加题目" }));
    const customForm = screen
      .getByRole("button", { name: "添加并保存" })
      .closest("form");
    await user.type(
      within(customForm).getByRole("textbox", { name: "问题内容" }),
      "Describe a production incident review.",
    );
    await user.type(
      within(customForm).getByRole("textbox", { name: "考察重点" }),
      "incident learning",
    );
    const custom = {
      question_id: "66666666-6666-4666-8666-666666666666",
      position: 3,
      question_text: "Describe a production incident review.",
      focus: "incident learning",
      question_type: "technical",
      difficulty: "intermediate",
      expected_minutes: 6,
      expected_followups: 0,
      origin: "custom",
      replaces_question_id: null,
      knowledge_binding: {
        schema_version: "plan-question-knowledge-binding-v1",
        status: "unbound",
        evidence_ids: [],
        reason_code: "custom_question",
      },
    };
    fetchMock.mockImplementationOnce(() =>
      response(
        revisionResponse(2, {
          questions: [...revisionResponse(1).plan.questions, custom],
        }),
      ),
    );
    await user.click(screen.getByRole("button", { name: "添加并保存" }));

    await screen.findByText("自定义题");
    const body = JSON.parse(fetchMock.mock.calls[1][1].body);
    expect(body.operations[0]).toEqual({
      op: "add_custom_question",
      question_text: "Describe a production incident review.",
      focus: "incident learning",
      question_type: "technical",
      difficulty: "intermediate",
      expected_minutes: 6,
      expected_followups: 0,
    });
    expect(body.operations[0]).not.toHaveProperty("knowledge_binding");
  });

  it("regenerates one question through the server provider endpoint", async () => {
    const user = userEvent.setup();
    render(<StartPage />);
    await generatePlan(user, fetchMock);
    const replacement = {
      ...revisionResponse(1).plan.questions[0],
      question_id: "77777777-7777-4777-8777-777777777777",
      question_text: "Explain a resilient cache refresh strategy.",
      origin: "regenerated",
      replaces_question_id: questionOne,
    };
    fetchMock.mockImplementationOnce(() =>
      response(
        revisionResponse(2, {
          questions: [replacement, revisionResponse(1).plan.questions[1]],
        }),
      ),
    );

    await user.click(screen.getAllByRole("button", { name: "换题" })[0]);

    await screen.findByDisplayValue("Explain a resilient cache refresh strategy.");
    expect(fetchMock.mock.calls[1][0]).toBe(
      "/api/interview-plans/" +
        familyId +
        "/questions/" +
        questionOne +
        "/regenerate",
    );
    expect(JSON.parse(fetchMock.mock.calls[1][1].body).expected_revision).toBe(1);
  });

  it("confirms before replacing a question that has unsaved local text", async () => {
    const user = userEvent.setup();
    render(<StartPage />);
    await generatePlan(user, fetchMock);
    const input = screen.getAllByRole("textbox", { name: "问题内容" })[0];
    await user.clear(input);
    await user.type(input, "Unsaved wording");

    await user.click(screen.getAllByRole("button", { name: "换题" })[0]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const dialog = screen.getByRole("dialog", {
      name: "放弃本地修改并替换这道题？",
    });
    expect(input).toHaveValue("Unsaved wording");
    await user.keyboard("{Escape}");
    expect(dialog).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not regenerate the whole plan until the confirmation is accepted", async () => {
    const user = userEvent.setup();
    render(<StartPage />);
    await generatePlan(user, fetchMock);
    await user.click(screen.getByRole("button", { name: "全部换题" }));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const dialog = screen.getByRole("dialog", { name: "重新生成整份计划？" });
    fetchMock.mockImplementationOnce(() => response(revisionResponse(2)));
    await user.click(
      within(dialog).getByRole("button", { name: "确认重新生成" }),
    );

    await screen.findByText("R2");
    expect(fetchMock.mock.calls[1][0]).toBe(
      "/api/interview-plans/" + familyId + "/regenerate",
    );
    expect(JSON.parse(fetchMock.mock.calls[1][1].body).confirmed).toBe(true);
  });

  it("turns a start-time 409 into a visible conflict and disables start", async () => {
    const user = userEvent.setup();
    render(<StartPage />);
    await generatePlan(user, fetchMock);
    fetchMock.mockImplementationOnce(() =>
      response({ detail: "plan revision conflict" }, 409),
    );

    await user.click(screen.getByRole("button", { name: "开始本次面试" }));

    await screen.findByText("计划版本冲突");
    expect(screen.getByRole("button", { name: "开始本次面试" })).toBeDisabled();
    expect(screen.getByText(/启动前服务端 revision 已变化/)).toBeInTheDocument();
  });

  it("preserves a local question draft while refreshing after failure", async () => {
    const user = userEvent.setup();
    render(<StartPage />);
    await generatePlan(user, fetchMock);
    const input = screen.getAllByRole("textbox", { name: "问题内容" })[0];
    await user.clear(input);
    await user.type(input, "Keep this draft across refresh");
    fetchMock.mockImplementationOnce(() =>
      response({ detail: "temporary failure" }, 503),
    );
    await user.click(screen.getAllByRole("button", { name: "保存修改" })[0]);
    await screen.findByText("计划操作失败");

    fetchMock
      .mockImplementationOnce(() =>
        response({
          plan_family_id: familyId,
          latest_revision: 2,
          revisions: [
            {
              plan_revision_id: "44444444-4444-4444-8444-444444444444",
              revision: 2,
              title: "Backend interview plan",
              question_count: 2,
              created_reason: "edit_question_text",
              is_latest: true,
            },
          ],
        }),
      )
      .mockImplementationOnce(() => response(revisionResponse(2)));

    await user.click(
      screen.getByRole("button", { name: "重新载入服务端版本" }),
    );

    await screen.findByText("R2");
    expect(screen.getAllByRole("textbox", { name: "问题内容" })[0]).toHaveValue(
      "Keep this draft across refresh",
    );
    expect(screen.getByRole("button", { name: "开始本次面试" })).toBeDisabled();
  });

  it("sends a complete safe configuration for initial generation", async () => {
    const user = userEvent.setup();
    const requestedConfiguration = {
      ...defaultConfiguration,
      difficulty: "advanced",
      target_duration_minutes: 45,
      focus_preset: "system_design",
      question_type_budget: {
        project: 1,
        technical: 2,
        "system-design": 3,
        behavioral: 1,
      },
      expected_followup_budget: 7,
    };
    const baseResponse = revisionResponse(1);
    fetchMock.mockImplementationOnce(() =>
      response(
        revisionResponse(1, {
          plan: {
            ...baseResponse.plan,
            configuration_snapshot: requestedConfiguration,
          },
        }),
      ),
    );
    render(<StartPage />);

    await user.click(screen.getByRole("radio", { name: /高级/ }));
    await user.click(screen.getByRole("radio", { name: "45 分钟" }));
    await user.click(screen.getByRole("radio", { name: /系统设计 架构/ }));
    await user.click(screen.getByRole("radio", { name: /架构优先/ }));
    expect(screen.getByText("7 道")).toBeInTheDocument();
    expect(screen.getByText(/实际进度取决于回答长度、追问和操作节奏/)).toBeInTheDocument();

    await user.type(screen.getByRole("textbox", { name: "岗位 JD" }), "Platform role");
    await user.click(screen.getByRole("tab", { name: /候选人经历/ }));
    await user.type(screen.getByRole("textbox", { name: "简历内容" }), "Platform resume");
    await user.click(screen.getByRole("button", { name: "生成面试计划" }));
    await screen.findByText("R1");

    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body.configuration).toEqual(requestedConfiguration);
    expect(body.configuration).not.toHaveProperty("question_mix_preset");
    expect(body.configuration.max_followups_per_question).toBe(2);
  });

  it("marks the current revision stale until configured regeneration succeeds", async () => {
    const user = userEvent.setup();
    render(<StartPage />);
    await generatePlan(user, fetchMock);

    await user.click(screen.getByRole("radio", { name: /高级/ }));
    expect(screen.getByText("待重新生成")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始本次面试" })).toBeDisabled();

    await user.click(screen.getByRole("radio", { name: /中级/ }));
    expect(screen.getByText("配置已同步")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始本次面试" })).toBeEnabled();
    await user.click(screen.getByRole("radio", { name: /高级/ }));

    await user.click(screen.getByRole("button", { name: "应用配置并重新生成" }));
    const dialog = screen.getByRole("dialog", {
      name: "使用新配置重新生成计划？",
    });
    expect(within(dialog).getByText(/新 revision 将采用：难度/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    const configured = { ...defaultConfiguration, difficulty: "advanced" };
    const baseResponse = revisionResponse(2);
    fetchMock.mockImplementationOnce(() =>
      response(
        revisionResponse(2, {
          plan: {
            ...baseResponse.plan,
            configuration_snapshot: configured,
          },
        }),
      ),
    );
    await user.click(
      within(dialog).getByRole("button", { name: "确认采用新配置" }),
    );

    await screen.findByText("R2");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "开始本次面试" })).toBeEnabled(),
    );
    const requestBody = JSON.parse(fetchMock.mock.calls[1][1].body);
    expect(requestBody.confirmed).toBe(true);
    expect(requestBody.configuration).toEqual(configured);
  });

  it("automatically restores the saved revision and its configuration after refresh", async () => {
    const user = userEvent.setup();
    const configured = {
      ...defaultConfiguration,
      difficulty: "advanced",
      target_duration_minutes: 60,
      focus_preset: "project_review",
      question_type_budget: {
        project: 4,
        technical: 2,
        "system-design": 1,
        behavioral: 2,
      },
      expected_followup_budget: 9,
    };
    const baseResponse = revisionResponse(1);
    window.localStorage.setItem("interview-agent:draft-id", "draft_refresh");
    window.localStorage.setItem(
      "interview-agent:plan-configuration-v1",
      JSON.stringify(configured),
    );
    fetchMock
      .mockImplementationOnce(() =>
        response({
          draft_id: "draft_refresh",
          job_description: "Restored platform role",
          resume_text: "Restored platform resume",
          job_tags: ["Platform"],
          plan_status: "active",
          plan_family_id: familyId,
          latest_plan_revision_id: baseResponse.plan_revision_id,
        }),
      )
      .mockImplementationOnce(() =>
        response(
          revisionResponse(1, {
            plan: {
              ...baseResponse.plan,
              configuration_snapshot: configured,
            },
          }),
        ),
      );

    render(<StartPage />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    await user.click(screen.getByRole("tab", { name: "计划" }));
    await screen.findByText("R1");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/interview-drafts/draft_refresh");
    expect(screen.getByRole("radio", { name: /高级/ })).toBeChecked();
    expect(screen.getByRole("radio", { name: "60 分钟" })).toBeChecked();
    expect(screen.getByRole("button", { name: "开始本次面试" })).toBeEnabled();
    expect(screen.getByText("配置已同步")).toBeInTheDocument();
  });

  it("confirms before a manual draft restore can replace the current canvas", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementationOnce(() => response({
      target: "job_description",
      filename: "current.pdf",
      media_type: "application/pdf",
      text: "New canvas content",
      character_count: 18,
      truncated: true,
      warning_codes: ["text_truncated"],
    }));
    render(<StartPage />);
    const jdInput = screen.getByRole("textbox", { name: "岗位 JD" });
    await user.upload(
      sourceFileInput("导入当前岗位文档"),
      new File(["%PDF-current"], "current.pdf", { type: "application/pdf" }),
    );
    await screen.findByText("current.pdf · 已截断");
    window.localStorage.setItem("interview-agent:draft-id", "draft-t58");
    const restoreButton = screen.getByRole("button", { name: "恢复草稿" });
    await user.click(restoreButton);

    const dialog = screen.getByRole("dialog", { name: "用已保存草稿替换当前画布？" });
    expect(dialog).toHaveTextContent("当前画布中的岗位 JD 和候选人经历会被替换");
    expect(dialog).toHaveTextContent("已保存的匿名草稿不会被删除");
    expect(fetchMock.mock.calls.some(
      ([path]) => path === "/api/interview-drafts/draft-t58",
    )).toBe(false);

    await user.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(jdInput).toHaveValue("New canvas content");
    expect(screen.getByText("current.pdf · 已截断")).toBeInTheDocument();
    await waitFor(() => expect(restoreButton).toHaveFocus());

    fetchMock.mockImplementationOnce(() => response({
      draft_id: "draft-t58",
      job_description: "Restored saved JD",
      resume_text: "Restored saved resume",
      job_tags: [],
      plan_status: "stale",
      plan_family_id: null,
      latest_plan_revision_id: null,
    }));
    await user.click(restoreButton);
    await user.click(screen.getByRole("button", { name: "确认恢复草稿" }));

    await waitFor(() => expect(jdInput).toHaveValue("Restored saved JD"));
    await user.click(screen.getByRole("tab", { name: /并排查看/ }));
    expect(screen.getAllByText("来自匿名草稿")).toHaveLength(2);
    expect(screen.queryByText(/来自匿名草稿 ·/)).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(
      ([path]) => path === "/api/interview-drafts/draft-t58",
    )).toBe(true);
  });

  it("uses a dialog before clearing the canvas and keeps saved drafts recoverable", async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementationOnce(() => response({
      target: "job_description",
      filename: "current.pdf",
      media_type: "application/pdf",
      text: "Do not clear without confirmation",
      character_count: 33,
      truncated: true,
      warning_codes: ["text_truncated"],
    }));
    render(<StartPage />);
    const jdInput = screen.getByRole("textbox", { name: "岗位 JD" });
    await user.upload(
      sourceFileInput("导入当前岗位文档"),
      new File(["%PDF-current"], "current.pdf", { type: "application/pdf" }),
    );
    await screen.findByText("current.pdf · 已截断");
    const clearButton = screen.getByRole("button", { name: "清空当前画布" });

    await user.click(clearButton);
    const dialog = screen.getByRole("dialog", { name: "清空当前画布？" });
    expect(dialog).toHaveTextContent("只清空当前画布");
    expect(dialog).toHaveTextContent("已保存的匿名草稿不会被删除，之后仍可恢复");
    await user.keyboard("{Escape}");
    expect(jdInput).toHaveValue("Do not clear without confirmation");
    await waitFor(() => expect(clearButton).toHaveFocus());

    await user.click(clearButton);
    await user.click(screen.getByRole("button", { name: "确认清空画布" }));
    expect(jdInput).toHaveValue("");
    await user.click(screen.getByRole("tab", { name: /并排查看/ }));
    expect(screen.getAllByText("未导入文件")).toHaveLength(2);
    expect(screen.queryByText(/未导入文件 ·/)).not.toBeInTheDocument();
    expect(screen.getByText("当前画布已清空；此前保存的匿名草稿仍可恢复。")).toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(
      ([path]) => path === "/api/prep/source-imports",
    )).toHaveLength(1);
  });

  it("keeps the current canvas and draft link when a confirmed restore fails mid-read", async () => {
    const user = userEvent.setup();
    render(<StartPage />);
    const jdInput = screen.getByRole("textbox", { name: "岗位 JD" });
    await user.type(jdInput, "Keep this current canvas");
    window.localStorage.setItem("interview-agent:draft-id", "draft-atomic");
    fetchMock
      .mockImplementationOnce(() => response({
        draft_id: "draft-atomic",
        job_description: "Do not apply this partial draft",
        resume_text: "Do not apply this partial resume",
        job_tags: ["Platform"],
        plan_status: "active",
        plan_family_id: familyId,
        latest_plan_revision_id: "33333333-3333-4333-8333-333333333333",
      }))
      .mockImplementationOnce(() => response({ detail: "revision temporarily unavailable" }, 500));

    await user.click(screen.getByRole("button", { name: "恢复草稿" }));
    await user.click(screen.getByRole("button", { name: "确认恢复草稿" }));

    expect(await screen.findByText("草稿恢复失败：revision temporarily unavailable")).toBeInTheDocument();
    expect(jdInput).toHaveValue("Keep this current canvas");
    expect(window.localStorage.getItem("interview-agent:draft-id")).toBe("draft-atomic");
  });
});
