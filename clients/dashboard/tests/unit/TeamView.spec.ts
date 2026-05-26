import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import PrimeVue from "primevue/config";
import { createPinia, setActivePinia } from "pinia";
import TeamView from "../../src/views/TeamView.vue";
import { __resetForTesting as resetUseTeam } from "../../src/composables/useTeam";

// Dashboard TeamView smoke tests. Shape matches harness /team contract
// (src/types/team.ts) — array of team members each with an `agents` array of
// witwave + backend cards. Covers list render, selection, chat-panel mount, and
// the loading/error/empty placeholders. Live chat send/receive is exercised
// separately in ChatPanel.spec.ts.

// Track the mounted wrapper so afterEach can unmount it. useTeam
// keeps module-level singleton state (shared members / error /
// loading refs + the poll subscriber map) that survives beyond a
// single wrapper's lifetime. Without an explicit unmount between
// tests, test 1's `sharedMembers` leaks into test 2's TeamView
// render, which then sees members.length > 0 and skips the
// loading/error/empty placeholder branches the assertion targets.
let currentWrapper: ReturnType<typeof mount> | null = null;

function mountView() {
  currentWrapper = mount(TeamView, {
    global: {
      plugins: [PrimeVue, createPinia()],
    },
  });
  return currentWrapper;
}

function okJson(data: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: async () => data,
  } as unknown as Response;
}

// Path-aware fetch mock — matches the new direct-routing dashboard API:
//   GET  /api/team                      → directory of members
//   GET  /api/agents/<name>/agents      → that member's witwave + backend cards
//   GET  /api/agents/<name>/conversations → empty list (ChatPanel will
//                                          fetch this on mount once the
//                                          right-pane is shown)
// `team` is an array of TeamMember-shaped objects; the mock derives the
// directory and per-agent responses from it.
interface MockTeamMember {
  name: string;
  url: string;
  agents?: unknown[];
  error?: string;
}

function mockEndpoints(team: MockTeamMember[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : (input as URL).toString();
      if (url.includes("/conversations")) return Promise.resolve(okJson([]));
      const agentMatch = /\/agents\/([^/]+)\/agents$/.exec(url);
      if (agentMatch) {
        const name = decodeURIComponent(agentMatch[1]);
        const member = team.find((m) => m.name === name);
        if (!member) {
          return Promise.resolve({ ok: false, status: 404 } as unknown as Response);
        }
        if (member.error) {
          return Promise.resolve({ ok: false, status: 502 } as unknown as Response);
        }
        return Promise.resolve(okJson(member.agents ?? []));
      }
      if (url.endsWith("/api/team") || url.endsWith("/team")) {
        return Promise.resolve(okJson(team.map((m) => ({ name: m.name, url: m.url }))));
      }
      return Promise.resolve(okJson(null));
    }),
  );
}

describe("TeamView", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    // Reset Pinia between tests so the selection store starts clean.
    setActivePinia(createPinia());
  });

  afterEach(() => {
    // Unmount first so useTeam's onUnmounted hook stops the poll
    // timer and clears the subscribers map, then explicitly reset
    // the shared refs (sharedMembers / sharedError / sharedLoading).
    // onUnmounted deliberately does NOT reset those in production —
    // cached data across mount/unmount is a feature for nav-away-and-back
    // UX — but tests need a clean slate between `it` blocks or test 1's
    // fetched members leak into test 2's placeholder-render assertions.
    currentWrapper?.unmount();
    currentWrapper = null;
    resetUseTeam();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("renders loading state then agent cards on fetch success", async () => {
    mockEndpoints([
      {
        name: "iris",
        url: "http://iris:8000",
        agents: [
          {
            id: "iris-witwave",
            role: "witwave",
            url: "http://iris:8000",
            card: { name: "iris", description: "Primary agent" },
          },
          {
            id: "iris-claude",
            role: "backend",
            url: "http://iris-claude:8000",
            card: { name: "iris-claude", family: "claude" },
          },
        ],
      },
      {
        name: "nova",
        url: "http://nova:8001",
        agents: [{ id: "nova-witwave", role: "witwave", url: "http://nova:8001", card: { name: "nova" } }],
      },
    ]);

    const wrapper = mountView();
    expect(wrapper.find("[data-testid='team-loading']").exists()).toBe(true);

    await flushPromises();

    const cards = wrapper.findAll("[data-testid='agent-card']");
    expect(cards.length).toBe(2);
    expect(wrapper.text()).toContain("iris");
    expect(wrapper.text()).toContain("nova");
    expect(wrapper.text()).toContain("iris-claude");
  });

  it("renders error placeholder on fetch failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 503 } as unknown as Response));

    const wrapper = mountView();
    await flushPromises();

    const err = wrapper.find("[data-testid='team-error']");
    expect(err.exists()).toBe(true);
    expect(err.text()).toContain("HTTP 503");
  });

  it("renders empty-state when team list is empty", async () => {
    mockEndpoints([]);

    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.find("[data-testid='team-empty']").exists()).toBe(true);
  });

  it("renders an unreachable card when a member reports an error", async () => {
    mockEndpoints([
      {
        name: "ghost",
        url: "http://ghost:8000",
        agents: [],
        error: "connection refused",
      },
    ]);

    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.find("[data-testid='agent-card-unreachable']").exists()).toBe(true);
  });

  it("mounts the chat panel once an agent is selected", async () => {
    mockEndpoints([
      {
        name: "iris",
        url: "http://iris:8000",
        agents: [
          { id: "iris-witwave", role: "witwave", url: "http://iris:8000", card: { name: "iris" } },
          {
            id: "iris-claude",
            role: "backend",
            url: "http://iris-claude:8000",
            card: { name: "iris-claude", family: "claude" },
          },
        ],
      },
    ]);

    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.find("[data-testid='detail-placeholder']").exists()).toBe(true);

    await wrapper.find("[data-testid='agent-card']").trigger("click");
    await flushPromises();

    expect(wrapper.find("[data-testid='detail-body']").exists()).toBe(true);
    expect(wrapper.find("[data-testid='chat-panel']").exists()).toBe(true);
    expect(wrapper.find("[data-testid='chat-backend-select']").exists()).toBe(true);
  });
});
