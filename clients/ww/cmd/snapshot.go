package cmd

import (
	"context"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/witwave-ai/witwave/clients/ww/internal/client"
	"github.com/witwave-ai/witwave/clients/ww/internal/output"
)

// snapshotEntry is the normalised view of a single job/task/trigger/
// continuation row. The harness snapshots use slightly different field
// names per concern; we collect everything into a flat map to preserve
// unknown fields and pull the common ones out by name.
type snapshotEntry map[string]any

// fetchSnapshot hits one of the read-only scheduler endpoints and
// returns the list of entries. The response body can be a raw list,
// a map with "items", or a map keyed by name — we accept all three.
func fetchSnapshot(ctx context.Context, c *client.Client, path string) ([]snapshotEntry, error) {
	raw, err := c.GetBytes(ctx, path)
	if err != nil {
		return nil, err
	}
	return parseSnapshot(raw)
}

// fetchSnapshotSingle hits a snapshot endpoint that returns a single
// flat JSON object rather than the list/envelope shape used by /jobs,
// /tasks, /triggers, /continuations. Currently only /heartbeat — each
// agent has at most one heartbeat configured, so the harness returns
// the heartbeat record directly (e.g. `{"enabled": true, "schedule":
// "...", "model": null, ...}`) rather than wrapping it in an envelope.
// See harness/main.py:heartbeat_handler for the response contract.
func fetchSnapshotSingle(ctx context.Context, c *client.Client, path string) (snapshotEntry, error) {
	raw, err := c.GetBytes(ctx, path)
	if err != nil {
		return nil, err
	}
	return parseSnapshotSingle(raw)
}

// parseSnapshotSingle decodes a flat JSON object body into a single
// snapshotEntry. Empty/whitespace input returns (nil, nil) — callers
// distinguish "no payload" from "disabled heartbeat" via the
// `enabled` field on the returned entry, not by nil-ness.
func parseSnapshotSingle(raw []byte) (snapshotEntry, error) {
	raw = []byte(strings.TrimSpace(string(raw)))
	if len(raw) == 0 {
		return nil, nil
	}
	var obj map[string]any
	if err := json.Unmarshal(raw, &obj); err != nil {
		return nil, fmt.Errorf("parse object: %w", err)
	}
	return snapshotEntry(obj), nil
}

func parseSnapshot(raw []byte) ([]snapshotEntry, error) {
	raw = []byte(strings.TrimSpace(string(raw)))
	if len(raw) == 0 {
		return nil, nil
	}
	// Try list first.
	if raw[0] == '[' {
		var list []snapshotEntry
		if err := json.Unmarshal(raw, &list); err != nil {
			return nil, fmt.Errorf("parse list: %w", err)
		}
		return list, nil
	}
	// Object.
	var obj map[string]any
	if err := json.Unmarshal(raw, &obj); err != nil {
		return nil, fmt.Errorf("parse object: %w", err)
	}
	// "items": [...]
	if items, ok := obj["items"].([]any); ok {
		out := make([]snapshotEntry, 0, len(items))
		for _, it := range items {
			if m, ok := it.(map[string]any); ok {
				out = append(out, snapshotEntry(m))
			}
		}
		return out, nil
	}
	// "jobs" / "tasks" / "triggers" / "continuations" / similar.
	for _, k := range []string{"jobs", "tasks", "triggers", "continuations", "heartbeat"} {
		if v, ok := obj[k]; ok {
			if items, ok := v.([]any); ok {
				out := make([]snapshotEntry, 0, len(items))
				for _, it := range items {
					if m, ok := it.(map[string]any); ok {
						out = append(out, snapshotEntry(m))
					}
				}
				return out, nil
			}
			if m, ok := v.(map[string]any); ok {
				// Single entry (heartbeat).
				return []snapshotEntry{snapshotEntry(m)}, nil
			}
		}
	}
	// Unknown shape. Previously we silently treated the raw object as a
	// single entry, which meant a harness response wrapped in a new
	// envelope (e.g. `{"data": [...]}`) rendered a meaningless
	// "one-row" table keyed by envelope metadata. Surfacing the
	// observed top-level keys plus the expected shape helps operators
	// tell "harness changed its schema" from "I pointed ww at the
	// wrong URL". (#1244)
	keys := make([]string, 0, len(obj))
	for k := range obj {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return nil, fmt.Errorf(
		"unexpected snapshot shape: top-level keys = [%s]; expected a JSON list, "+
			"an object with \"items\": [...], or an object with one of "+
			"\"jobs\"/\"tasks\"/\"triggers\"/\"continuations\"/\"heartbeat\"",
		strings.Join(keys, ", "),
	)
}

// pickField returns the first non-empty string value among the
// candidate keys.
func (e snapshotEntry) pickField(keys ...string) string {
	for _, k := range keys {
		if v, ok := e[k]; ok {
			if s, ok := v.(string); ok && s != "" {
				return s
			}
			if v == nil {
				continue
			}
			// Stringify numbers / bools.
			b, _ := json.Marshal(v)
			s := strings.Trim(string(b), `"`)
			if s != "" && s != "null" {
				return s
			}
		}
	}
	return ""
}

// printList renders a snapshot as JSON, YAML (#1707), or a text table.
func printList(out *output.Writer, entries []snapshotEntry, columns [][2]string) error {
	if out.IsJSON() {
		return out.EmitJSON(entries)
	}
	if out.IsYAML() {
		return out.EmitYAML(entries)
	}
	headers := make([]string, 0, len(columns))
	for _, c := range columns {
		headers = append(headers, c[0])
	}
	rows := make([][]string, 0, len(entries))
	for _, e := range entries {
		row := make([]string, 0, len(columns))
		for _, c := range columns {
			keys := strings.Split(c[1], ",")
			row = append(row, firstNonEmpty(e.pickField(keys...), "-"))
		}
		rows = append(rows, row)
	}
	output.Table(out.Out, headers, rows)
	return nil
}

// printView renders a single entry as KV pairs (human), JSON, or YAML (#1707).
func printView(out *output.Writer, entry snapshotEntry) error {
	if out.IsJSON() {
		return out.EmitJSON(entry)
	}
	if out.IsYAML() {
		return out.EmitYAML(entry)
	}
	// Sort keys for deterministic output, with 'name' first if present.
	keys := make([]string, 0, len(entry))
	for k := range entry {
		keys = append(keys, k)
	}
	// Stable-ish sort: name, schedule, then alpha.
	priority := map[string]int{"name": 0, "endpoint": 1, "schedule": 2, "window": 3, "continues-after": 4}
	sortKeys(keys, priority)
	pairs := make([][2]string, 0, len(keys))
	for _, k := range keys {
		v := entry[k]
		var s string
		switch vv := v.(type) {
		case string:
			s = vv
		case nil:
			s = ""
		default:
			b, _ := json.Marshal(vv)
			s = string(b)
		}
		pairs = append(pairs, [2]string{k, s})
	}
	output.KV(out.Out, pairs)
	return nil
}

func sortKeys(keys []string, priority map[string]int) {
	// Insertion sort — n is small (per-entry field count).
	for i := 1; i < len(keys); i++ {
		for j := i; j > 0; j-- {
			if keyLess(keys[j], keys[j-1], priority) {
				keys[j], keys[j-1] = keys[j-1], keys[j]
			} else {
				break
			}
		}
	}
}

func keyLess(a, b string, priority map[string]int) bool {
	pa, oka := priority[a]
	pb, okb := priority[b]
	switch {
	case oka && okb:
		return pa < pb
	case oka:
		return true
	case okb:
		return false
	default:
		return a < b
	}
}

func firstNonEmpty(vals ...string) string {
	for _, v := range vals {
		if v != "" {
			return v
		}
	}
	return ""
}

// findEntryByName locates the first entry whose `name` (or fallback key)
// matches the supplied value.
func findEntryByName(entries []snapshotEntry, name string, keys ...string) snapshotEntry {
	if len(keys) == 0 {
		keys = []string{"name"}
	}
	for _, e := range entries {
		for _, k := range keys {
			if v, ok := e[k].(string); ok && v == name {
				return e
			}
		}
	}
	return nil
}

// formatTime returns the value as-is if it already looks RFC3339, else
// passes it through. Used for "LAST_FIRE" columns that might be null.
func formatTime(v any) string {
	if v == nil {
		return "-"
	}
	s, ok := v.(string)
	if !ok {
		b, _ := json.Marshal(v)
		return string(b)
	}
	if _, err := time.Parse(time.RFC3339, s); err == nil {
		return s
	}
	return s
}
