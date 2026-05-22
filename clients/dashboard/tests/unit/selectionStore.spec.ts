import { describe, expect, it, beforeEach } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { useSelectionStore } from "../../src/stores/selection";

// Smoke coverage for the selection store that replaces the
// TeamView-local prop-drilled state (#748). Exercises the handful
// of mutations consumers touch — agent-only select, agent+backend
// select, backend-only update, and clear.

describe("useSelectionStore", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("starts empty", () => {
    const store = useSelectionStore();
    expect(store.selectedName).toBeNull();
    expect(store.activeBackendId).toBeNull();
  });

  it("selectAgent sets name and clears backend", () => {
    const store = useSelectionStore();
    store.activeBackendId = "stale";
    store.selectAgent("iris");
    expect(store.selectedName).toBe("iris");
    expect(store.activeBackendId).toBeNull();
  });

  it("selectBackend sets both", () => {
    const store = useSelectionStore();
    store.selectBackend("iris", "iris-claude");
    expect(store.selectedName).toBe("iris");
    expect(store.activeBackendId).toBe("iris-claude");
  });

  it("setActiveBackend only touches the backend id", () => {
    const store = useSelectionStore();
    store.selectAgent("iris");
    store.setActiveBackend("iris-openai");
    expect(store.selectedName).toBe("iris");
    expect(store.activeBackendId).toBe("iris-openai");
  });

  it("clear resets both fields", () => {
    const store = useSelectionStore();
    store.selectBackend("iris", "iris-claude");
    store.clear();
    expect(store.selectedName).toBeNull();
    expect(store.activeBackendId).toBeNull();
  });
});
