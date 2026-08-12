import { describe, expect, it } from "vitest";
import { memoryErrorMessage } from "./memoryErrors";

describe("memoryErrorMessage", () => {
  it.each([
    [{ status: 403 }, "当前环境不允许执行这项长期记忆操作。"],
    [{ status: 409 }, "这项长期记忆设置刚刚发生了变化，已加载最新状态，请重试。"],
    [{ status: 404 }, "长期记忆当前不可用。该功能只在受支持的本地运行模式下开放。"],
    [{ status: 503 }, "本地记忆服务暂时无法完成该操作，请稍后重试。"],
    [{ code: "REQUEST_TIMEOUT" }, "本地记忆服务响应超时，请重新检测。"],
    [{ code: "CONNECTION_FAILED" }, "无法连接本地记忆服务。面试和报告功能不受影响。"],
  ])("maps transport failures without exposing technical details", (error, message) => {
    expect(memoryErrorMessage(error)).toBe(message);
  });

  it("prefers a stable domain code over a generic HTTP status", () => {
    expect(memoryErrorMessage({
      code: "principal_memory_version_conflict",
      status: 403,
    })).toBe("这条记忆刚刚发生了变化，已加载最新状态，请重新确认。");
  });
});
