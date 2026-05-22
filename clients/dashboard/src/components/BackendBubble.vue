<script setup lang="ts">
import { computed } from "vue";
import type { Agent } from "../types/team";
import { backendType } from "../types/team";

// Pill rendered for each backend under an agent card. Dot is green/red by
// reachability (card present = up). Type class drives brand color — see
// tokens.css for the claude/openai/gemini/echo palette.

const props = defineProps<{
  backend: Agent;
  active?: boolean;
}>();

const emit = defineEmits<{
  (e: "select", backendId: string): void;
}>();

// Pass the full Agent so backendType() can prefer an explicit `family` on the
// agent-card (structured match) and only fall back to id-based inference when
// the field is absent.
const type = computed(() => backendType(props.backend));
const reachable = computed(() => !!props.backend.card);
const agentName = computed(() => props.backend.card?.name ?? props.backend.id);

function onClick(e: MouseEvent) {
  e.stopPropagation();
  emit("select", props.backend.id);
}
</script>

<template>
  <button
    type="button"
    class="backend-bubble"
    :class="[type, { 'active-backend': active }]"
    :title="backend.url ?? ''"
    :aria-pressed="active ? 'true' : 'false'"
    @click="onClick"
  >
    <div class="bb-dot" :class="{ up: reachable, down: !reachable }" />
    <span class="bb-label">{{ backend.id }}</span>
    <span class="bb-id">/ {{ agentName }}</span>
  </button>
</template>

<style scoped>
.backend-bubble {
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 10px;
  border-radius: 20px;
  font-size: 11px;
  border: 1px solid transparent;
  /* Reset user-agent button styles. */
  appearance: none;
  background: none;
  font: inherit;
  color: inherit;
  text-align: inherit;
  line-height: inherit;
}

.backend-bubble:focus {
  outline: none;
}

.backend-bubble:focus-visible {
  outline: 2px solid var(--witwave-accent);
  outline-offset: 2px;
}

.backend-bubble.active-backend {
  outline: 2px solid var(--witwave-accent);
  outline-offset: 1px;
}

.bb-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
  background: var(--witwave-muted);
}

.bb-dot.up {
  background: var(--witwave-green) !important;
}

.bb-dot.down {
  background: var(--witwave-red) !important;
}

.bb-label {
  color: var(--witwave-bright);
  white-space: nowrap;
}

.bb-id {
  opacity: 0.55;
  font-size: 10px;
}

.backend-bubble.claude {
  background: color-mix(in srgb, var(--witwave-brand-claude) 12%, transparent);
  border-color: color-mix(in srgb, var(--witwave-brand-claude) 35%, transparent);
}

.backend-bubble.openai,
.backend-bubble.codex {
  background: color-mix(in srgb, var(--witwave-brand-openai) 12%, transparent);
  border-color: color-mix(in srgb, var(--witwave-brand-openai) 35%, transparent);
}

.backend-bubble.gemini {
  background: color-mix(in srgb, var(--witwave-brand-gemini) 12%, transparent);
  border-color: color-mix(in srgb, var(--witwave-brand-gemini) 35%, transparent);
}

.backend-bubble.echo {
  background: color-mix(in srgb, var(--witwave-brand-echo) 12%, transparent);
  border-color: color-mix(in srgb, var(--witwave-brand-echo) 35%, transparent);
}

.backend-bubble.unknown {
  background: color-mix(in srgb, var(--witwave-muted) 12%, transparent);
  border-color: color-mix(in srgb, var(--witwave-muted) 35%, transparent);
}
</style>
