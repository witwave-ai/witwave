import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises } from "@vue/test-utils";
import { effectScope, nextTick, ref } from "vue";

// Unit tests for useRouting (#824): the composable powers the chat
// selector's "default backend for this agent" resolution. A regression
// in the routing-entry → agent fallback chain silently defaults the
// chat to the wrong backend, sending traffic to a pod the operator
// didn't route to this kind (a2a/job/...). Previously no tests
// covered any composable.

// apiGet is the seam we mock; it lives in ../../src/api/client.ts.
vi.mock("../../src/api/client", () => ({
  apiGet: vi.fn(),
  ApiError: class ApiError extends Error {},
}));

import { apiGet } from "../../src/api/client";
import { useRouting } from "../../src/composables/useRouting";

const mockedApiGet = vi.mocked(apiGet);

function fullRouting(overrides: Record<string, { agent: string; model: string | null } | null> = {}) {
  return {
    default: overrides.default_name ?? "iris-claude",
    default_routing: null,
    routing: {
      a2a: null,
      heartbeat: null,
      job: null,
      task: null,
      trigger: null,
      continuation: null,
      ...overrides,
    },
  } as unknown as Awaited<ReturnType<typeof apiGet>>;
}

describe("useRouting", () => {
  beforeEach(() => {
    mockedApiGet.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns null when no agent name is supplied", async () => {
    const agentName = ref("");
    const scope = effectScope();
    scope.run(() => {
      const { routing, defaultBackendFor } = useRouting(() => agentName.value);
      expect(routing.value).toBeNull();
      expect(defaultBackendFor("a2a")).toBeNull();
    });
    expect(mockedApiGet).not.toHaveBeenCalled();
    scope.stop();
  });

  it("prefers routing.<kind>.agent over the top-level default", async () => {
    mockedApiGet.mockResolvedValueOnce(
      fullRouting({
        a2a: { agent: "iris-openai", model: "gpt-5" },
        default_name: "iris-claude",
      } as never),
    );
    const scope = effectScope();
    let resolver: ReturnType<typeof useRouting> | undefined;
    scope.run(() => {
      resolver = useRouting(() => "iris");
    });
    await flushPromises();
    await nextTick();
    expect(resolver!.defaultBackendFor("a2a")).toBe("iris-openai");
    scope.stop();
  });

  it("falls through to routing.default when kind has no override", async () => {
    mockedApiGet.mockResolvedValueOnce(fullRouting({ default_name: "iris-claude" } as never));
    const scope = effectScope();
    let resolver: ReturnType<typeof useRouting> | undefined;
    scope.run(() => {
      resolver = useRouting(() => "iris");
    });
    await flushPromises();
    await nextTick();
    expect(resolver!.defaultBackendFor("a2a")).toBe("iris-claude");
    scope.stop();
  });

  it("returns null when the API call rejects (non-Abort)", async () => {
    mockedApiGet.mockRejectedValueOnce(new Error("boom"));
    const scope = effectScope();
    let resolver: ReturnType<typeof useRouting> | undefined;
    scope.run(() => {
      resolver = useRouting(() => "iris");
    });
    await flushPromises();
    await nextTick();
    expect(resolver!.routing.value).toBeNull();
    expect(resolver!.defaultBackendFor("a2a")).toBeNull();
    expect(resolver!.error.value).toContain("boom");
    scope.stop();
  });

  it("swallows AbortError without setting error", async () => {
    const abort = Object.assign(new Error("aborted"), { name: "AbortError" });
    mockedApiGet.mockRejectedValueOnce(abort);
    const scope = effectScope();
    let resolver: ReturnType<typeof useRouting> | undefined;
    scope.run(() => {
      resolver = useRouting(() => "iris");
    });
    await flushPromises();
    await nextTick();
    expect(resolver!.error.value).toBe("");
    scope.stop();
  });
});
