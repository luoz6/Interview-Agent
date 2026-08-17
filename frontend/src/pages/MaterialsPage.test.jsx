import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MaterialsPage } from "./MaterialsPage";

const READY_ID = "11111111-1111-4111-8111-111111111111";
const FAILED_ID = "22222222-2222-4222-8222-222222222222";

function material(overrides = {}) {
  return {
    document_id: READY_ID,
    display_name: "系统设计复盘",
    media_type: "text/markdown",
    size_bytes: 2450,
    status: "ready",
    enabled: true,
    allowed_usage: ["question", "follow_up", "feedback"],
    created_at: "2026-08-15T08:00:00Z",
    updated_at: "2026-08-15T08:00:00Z",
    error_code: null,
    ...overrides,
  };
}

function response(payload, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => null },
    json: async () => payload,
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

beforeEach(() => {
  window.history.replaceState({}, "", "/materials");
});

describe("MaterialsPage", () => {
  it("renders the safe list contract, every item state, usage labels and search", async () => {
    const items = [
      material({
        owner_principal_id: "private-owner-value",
        revision_id: "private-revision-value",
        content_sha256: "private-hash-value",
        embedding: [0.1, 0.2],
        manifest: "private-manifest-value",
        corpus: "private-corpus-value",
        original_path: "/private/raw/path",
        extracted_content: "private raw content",
        internal_stage: "private-stage-value",
      }),
      material({ document_id: "33333333-3333-4333-8333-333333333333", display_name: "项目追问清单", status: "processing" }),
      material({ document_id: FAILED_ID, display_name: "缓存事故记录", status: "failed", enabled: false, error_code: "processing_failed" }),
      material({ document_id: "44444444-4444-4444-8444-444444444444", display_name: "岗位能力清单", status: "disabled", enabled: false }),
      material({ document_id: "55555555-5555-4555-8555-555555555555", display_name: "待删除笔记", status: "deleting", enabled: false }),
    ];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(await response({ items })));
    const user = userEvent.setup();

    render(<MaterialsPage />);

    expect(await screen.findByRole("heading", { level: 1, name: "我的资料" })).toBeInTheDocument();
    expect(screen.getByText("你主动上传的文件，可在准备面试时选择使用")).toBeInTheDocument();
    expect(screen.getByText("只有在准备页选中的资料，才会用于对应面试。")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "5 份资料" })).toBeInTheDocument();
    expect(screen.getByLabelText("资料概览")).toHaveTextContent("已就绪1份可选择处理中1份正在建立索引需处理1份需要你确认");
    for (const label of ["已就绪", "处理中", "处理失败", "已停用", "删除中"]) {
      expect(screen.getByRole("status", { name: `状态：${label}` })).toBeInTheDocument();
    }
    for (const usage of ["定制问题", "生成追问", "辅助反馈"]) {
      expect(screen.getAllByText(usage).length).toBeGreaterThan(0);
    }
    expect(screen.getByText("资料处理暂时失败，请稍后重试。")).toBeInTheDocument();

    await user.type(screen.getByRole("searchbox", { name: "搜索资料" }), "缓存");
    expect(screen.getByText("缓存事故记录")).toBeInTheDocument();
    expect(screen.queryByText("系统设计复盘")).not.toBeInTheDocument();
    expect(screen.getByText("当前显示 1 份匹配结果")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "清除搜索" }));
    expect(screen.getByText("系统设计复盘")).toBeInTheDocument();

    const dom = document.body.textContent.toLowerCase();
    for (const forbidden of [
      "owner_principal_id",
      "principal",
      "revision_id",
      "content_sha256",
      "embedding",
      "manifest",
      "corpus",
      "/private/raw/path",
      "private raw content",
      "private-stage-value",
    ]) {
      expect(dom).not.toContain(forbidden);
    }
  });

  it("validates and uploads only file plus editable display_name through FormData", async () => {
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => response({ items: [] }))
      .mockImplementationOnce((_url, options) => response(material({
        document_id: "66666666-6666-4666-8666-666666666666",
        display_name: options.body.get("display_name"),
        status: "processing",
      }), 201));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup({ applyAccept: false });
    render(<MaterialsPage />);

    await screen.findByRole("heading", { name: "0 份资料" });
    const uploadTrigger = screen.getByRole("button", { name: /^上传资料$/ });
    expect(uploadTrigger).toHaveAttribute("aria-expanded", "false");
    await user.click(uploadTrigger);
    expect(uploadTrigger).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("heading", { name: "上传一份新的参考资料" })).toBeInTheDocument();
    const uploadSteps = screen.getByRole("list", { name: "资料处理步骤" });
    for (const step of ["选择文件", "确认名称", "自动处理"]) {
      expect(within(uploadSteps).getByText(step)).toBeInTheDocument();
    }
    const fileInput = screen.getByLabelText(/点击选择，或将文件拖到这里/);

    await user.upload(fileInput, new File(["pdf"], "notes.pdf", { type: "application/pdf" }));
    expect(screen.getByRole("alert")).toHaveTextContent("仅支持 Markdown 或 TXT 文件");

    const markdown = new File(["# system design"], "system-design.md", { type: "text/markdown" });
    await user.upload(fileInput, markdown);
    const nameInput = screen.getByRole("textbox", { name: "资料名称" });
    await user.clear(nameInput);
    await user.type(nameInput, "  分布式系统复盘  ");
    await user.click(screen.getByRole("button", { name: "上传并处理" }));

    expect(await screen.findByText("分布式系统复盘")).toBeInTheDocument();
    const [url, options] = fetchMock.mock.calls[1];
    expect(url).toBe("/api/materials");
    expect(options.method).toBe("POST");
    expect(options.body).toBeInstanceOf(FormData);
    expect([...options.body.keys()]).toEqual(["file", "display_name"]);
    expect(options.body.get("file")).toBe(markdown);
    expect(options.body.get("display_name")).toBe("分布式系统复盘");
    expect(options.headers).toBeUndefined();
  });

  it("supports rename, usage, disable, enable, retry and confirmed permanent delete", async () => {
    let ready = material();
    let failed = material({
      document_id: FAILED_ID,
      display_name: "失败资料",
      status: "failed",
      enabled: false,
      error_code: "index_write_failed",
    });
    const fetchMock = vi.fn(async (url, options = {}) => {
      if (!options.method) return response({ items: [ready, failed] });
      if (options.method === "PATCH" && url.endsWith(READY_ID)) {
        const payload = JSON.parse(options.body);
        ready = {
          ...ready,
          ...(payload.display_name ? { display_name: payload.display_name } : {}),
          ...(payload.allowed_usage ? { allowed_usage: payload.allowed_usage } : {}),
          ...(payload.enabled === false ? { enabled: false, status: "disabled" } : {}),
          ...(payload.enabled === true ? { enabled: true, status: "ready" } : {}),
        };
        return response(ready);
      }
      if (options.method === "POST" && url.endsWith(`${FAILED_ID}/retry`)) {
        failed = { ...failed, status: "processing", error_code: null };
        return response(failed);
      }
      if (options.method === "DELETE" && url.endsWith(READY_ID)) {
        return response({ document_id: READY_ID, deleted: true });
      }
      throw new Error(`Unexpected request ${options.method} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<MaterialsPage />);

    await screen.findByText("系统设计复盘");
    const readyRow = screen.getByText("系统设计复盘").closest("li");
    await user.click(within(readyRow).getByRole("button", { name: /改名/ }));
    const rename = within(readyRow).getByRole("textbox", { name: "新的资料名称" });
    await user.clear(rename);
    await user.type(rename, "系统设计精要");
    await user.click(within(readyRow).getByRole("button", { name: "保存" }));
    expect(await screen.findByText("系统设计精要")).toBeInTheDocument();

    await user.click(screen.getByRole("checkbox", { name: "系统设计精要：辅助反馈" }));
    await waitFor(() => {
      const patchCalls = fetchMock.mock.calls.filter(([, options]) => options?.method === "PATCH");
      expect(JSON.parse(patchCalls.at(-1)[1].body)).toEqual({ allowed_usage: ["question", "follow_up"] });
    });

    const renamedRow = screen.getByText("系统设计精要").closest("li");
    await user.click(within(renamedRow).getByRole("button", { name: /停用/ }));
    expect(await within(renamedRow).findByRole("button", { name: /启用/ })).toBeInTheDocument();
    await user.click(within(renamedRow).getByRole("button", { name: /启用/ }));
    expect(await within(renamedRow).findByRole("button", { name: /停用/ })).toBeInTheDocument();

    const failedRow = screen.getByText("失败资料").closest("li");
    await user.click(within(failedRow).getByRole("button", { name: /重试/ }));
    expect(await within(failedRow).findByRole("status", { name: "状态：处理中" })).toBeInTheDocument();

    await user.click(within(renamedRow).getByRole("button", { name: /永久删除/ }));
    const dialog = screen.getByRole("alertdialog");
    expect(dialog).toHaveTextContent("将永久删除该资料的原始文件和索引，且不可恢复");
    await user.click(within(dialog).getByRole("button", { name: "确认永久删除" }));
    await waitFor(() => expect(screen.queryByText("系统设计精要")).not.toBeInTheDocument());

    expect(fetchMock.mock.calls.some(([url, options]) => (
      url === `/api/materials/${READY_ID}` && options.method === "DELETE"
    ))).toBe(true);
  });

  it("shows capability-disabled copy for a hidden list without exposing the raw error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => response({
      detail: { code: "not_found", message: "private route detail" },
    }, 404)));
    render(<MaterialsPage />);

    expect(await screen.findByRole("heading", { name: "资料功能当前未启用" })).toBeInTheDocument();
    expect(screen.queryByText("private route detail")).not.toBeInTheDocument();
  });

  it("keeps permanent deletion available when upload capability is hidden", async () => {
    const existing = material();
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => response({ items: [existing] }))
      .mockImplementationOnce(() => response({ detail: { code: "not_found", message: "private" } }, 404))
      .mockImplementationOnce(() => response({ document_id: READY_ID, deleted: true }));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<MaterialsPage />);

    await screen.findByText("系统设计复盘");
    await user.click(screen.getByRole("button", { name: /^上传资料$/ }));
    const fileInput = screen.getByLabelText(/点击选择，或将文件拖到这里/);
    await user.upload(fileInput, new File(["notes"], "notes.txt", { type: "text/plain" }));
    await user.click(screen.getByRole("button", { name: "上传并处理" }));

    expect(await screen.findByText("已有资料仍可管理或永久删除。", { exact: false })).toBeInTheDocument();
    const deleteButton = screen.getByRole("button", { name: /永久删除/ });
    expect(deleteButton).toBeEnabled();
    await user.click(deleteButton);
    await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "确认永久删除" }));
    await waitFor(() => expect(screen.queryByText("系统设计复盘")).not.toBeInTheDocument());
    expect(fetchMock.mock.calls.at(-1)[1].method).toBe("DELETE");
  });
});
