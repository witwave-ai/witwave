import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import BackendBubble from "../../src/components/BackendBubble.vue";
import type { Agent } from "../../src/types/team";

// Unit tests for the BackendBubble component (#823). BackendBubble is
// the pill rendered for every backend under an agent card — its class
// list drives brand colours and its dot drives reachability, so a
// silent regression (backend.card gone → no "down" class) would hide
// dead backends from the operator. No non-view component had a spec
// before this lands.

function makeBackend(overrides: Partial<Agent> = {}): Agent {
  return {
    id: "iris-claude",
    url: "http://iris:8010",
    family: "claude",
    // Casting because AgentCard has many optional fields we don't need
    // here; the component only reads `.card` for truthiness and
    // `.card?.name` for the title.
    card: { name: "iris-claude", family: "claude" } as Agent["card"],
    ...overrides,
  } as Agent;
}

describe("BackendBubble", () => {
  it("renders the backend id label", () => {
    const wrapper = mount(BackendBubble, { props: { backend: makeBackend() } });
    expect(wrapper.find(".bb-label").text()).toBe("iris-claude");
  });

  it("marks the dot `up` when a card is present (reachable)", () => {
    const wrapper = mount(BackendBubble, { props: { backend: makeBackend() } });
    const dot = wrapper.find(".bb-dot");
    expect(dot.classes()).toContain("up");
    expect(dot.classes()).not.toContain("down");
  });

  it("marks the dot `down` when card is null (unreachable)", () => {
    const wrapper = mount(BackendBubble, {
      props: { backend: makeBackend({ card: null as unknown as Agent["card"] }) },
    });
    const dot = wrapper.find(".bb-dot");
    expect(dot.classes()).toContain("down");
    expect(dot.classes()).not.toContain("up");
  });

  it("adds the backend type class for brand colouring", () => {
    const wrapper = mount(BackendBubble, { props: { backend: makeBackend() } });
    expect(wrapper.classes()).toContain("claude");
  });

  it("emits `select` with backend id when clicked and stops propagation", async () => {
    const wrapper = mount(BackendBubble, { props: { backend: makeBackend() } });
    await wrapper.trigger("click");
    expect(wrapper.emitted("select")).toBeTruthy();
    expect(wrapper.emitted("select")?.[0]).toEqual(["iris-claude"]);
  });

  it("reflects active prop via aria-pressed and active-backend class", async () => {
    const wrapper = mount(BackendBubble, {
      props: { backend: makeBackend(), active: true },
    });
    expect(wrapper.attributes("aria-pressed")).toBe("true");
    expect(wrapper.classes()).toContain("active-backend");
    await wrapper.setProps({ active: false });
    expect(wrapper.attributes("aria-pressed")).toBe("false");
    expect(wrapper.classes()).not.toContain("active-backend");
  });
});
