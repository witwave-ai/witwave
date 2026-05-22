import { describe, expect, it, afterEach, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import ChatPanel from "../../src/components/ChatPanel.vue";
import type { Agent } from "../../src/types/team";

// ChatPanel Tier 1 — covers the two request paths (history load + send) and
// the thread rendering for user / agent / error rows.

const backends: Agent[] = [
  { id: "iris-claude", role: "backend", url: "http://iris-claude:8000" },
  { id: "iris-openai", role: "backend", url: "http://iris-openai:8000" },
];

function okJson(data: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: async () => data,
  } as unknown as Response;
}

function mountPanel() {
  return mount(ChatPanel, {
    props: {
      agentName: "iris",
      backends,
      activeBackendId: "iris-claude",
    },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ChatPanel", () => {
  it("backfills conversation history on mount", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : (input as URL).toString();
        if (url.includes("/conversations")) {
          return Promise.resolve(
            okJson([
              { ts: "t1", agent: "iris-claude", role: "user", text: "hello" },
              { ts: "t2", agent: "iris-claude", role: "agent", text: "hi there" },
            ]),
          );
        }
        return Promise.resolve(okJson({}));
      }),
    );

    const wrapper = mountPanel();
    await flushPromises();

    expect(wrapper.findAll("[data-testid='chat-msg-user']").length).toBe(1);
    expect(wrapper.findAll("[data-testid='chat-msg-agent']").length).toBe(1);
    expect(wrapper.text()).toContain("hello");
    expect(wrapper.text()).toContain("hi there");
  });

  it("sends a message and renders the agent reply", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
      const url = typeof input === "string" ? input : (input as URL).toString();
      if (url.includes("/conversations")) return Promise.resolve(okJson([]));
      if (url.includes("/agents/") && !url.includes("/conversations")) {
        return Promise.resolve(
          okJson({
            jsonrpc: "2.0",
            id: 1,
            result: {
              parts: [{ kind: "text", text: "pong from claude" }],
            },
          }),
        );
      }
      return Promise.resolve(okJson({}));
    });
    vi.stubGlobal("fetch", fetchMock);

    const wrapper = mountPanel();
    await flushPromises();

    await wrapper.find("[data-testid='chat-input']").setValue("ping");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(wrapper.text()).toContain("ping");
    expect(wrapper.text()).toContain("pong from claude");
    expect(wrapper.findAll("[data-testid='chat-msg-user']").length).toBe(1);
    expect(wrapper.findAll("[data-testid='chat-msg-agent']").length).toBe(1);

    // Confirms the request routed directly to the agent's service and
    // carried the selected backend as A2A message metadata (#470).
    const sendCall = fetchMock.mock.calls.find(
      ([u, init]) => String(u).includes("/agents/iris/") && (init as RequestInit | undefined)?.method === "POST",
    );
    expect(sendCall).toBeDefined();
    expect(String(sendCall?.[0])).toContain("/agents/iris/");
    const sentBody = JSON.parse(String((sendCall?.[1] as RequestInit | undefined)?.body ?? "{}"));
    expect(sentBody.params.message.metadata.backend_id).toBe("iris-claude");
  });

  it("surfaces an error row when the proxy call fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : (input as URL).toString();
        if (url.includes("/conversations")) return Promise.resolve(okJson([]));
        if (url.includes("/agents/") && !url.includes("/conversations")) {
          return Promise.resolve({ ok: false, status: 500 } as unknown as Response);
        }
        return Promise.resolve(okJson({}));
      }),
    );

    const wrapper = mountPanel();
    await flushPromises();

    await wrapper.find("[data-testid='chat-input']").setValue("boom");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(wrapper.findAll("[data-testid='chat-msg-error']").length).toBe(1);
    expect(wrapper.text()).toContain("HTTP 500");
  });
});
