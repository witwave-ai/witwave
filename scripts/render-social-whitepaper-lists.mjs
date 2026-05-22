#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, "..");
const siteDir = path.resolve(process.argv[2] || path.join(repoRoot, "social/website"));
const catalogPath = path.join(repoRoot, "social/website/content/whitepapers.json");

const catalog = readJson(catalogPath);
const whitepapers = (catalog.whitepapers || []).filter(
  (paper) => paper.display !== false && paper.status !== "archived",
);

renderPageRows({ file: path.join(siteDir, "index.html"), marker: "WHITEPAPER_ROWS:home", hrefPrefix: "" });
renderPageRows({
  file: path.join(siteDir, "whitepapers/index.html"),
  marker: "WHITEPAPER_ROWS:whitepapers",
  hrefPrefix: "../",
});

function renderPageRows({ file, marker, hrefPrefix }) {
  if (!fs.existsSync(file)) {
    throw new Error(`page not found: ${file}`);
  }

  const source = fs.readFileSync(file, "utf8");
  const rows = [
    "          <!-- Generated from content/whitepapers.json by scripts/render-social-whitepaper-lists.mjs. -->",
    whitepapers.map((paper, index) => renderRow(paper, index, hrefPrefix)).join("\n\n"),
  ].join("\n");
  const updated = replaceMarkedBlock(source, marker, rows);
  fs.writeFileSync(file, updated);
}

function renderRow(paper, index, hrefPrefix) {
  const slug = stringify(paper.slug);
  const href = `${hrefPrefix}${stringify(paper.readerPath) || `reader/?paper=${slug}`}`;
  const title = stringify(paper.title) || stringify(paper.shortTitle) || slug;
  const kicker = stringify(paper.kicker) || "Living white paper";
  const deck = stringify(paper.cardDeck) || stringify(paper.deck) || `Read ${title}.`;
  const number = String(index + 1).padStart(2, "0");

  return `          <a class="paper-path-item" href="${escapeAttr(href)}">
            <span class="paper-number">${number}</span>
            <span class="paper-path-copy">
              <span class="card-kicker">${escapeHtml(kicker)}</span>
              <strong>${escapeHtml(title)}</strong>
              <span>${escapeHtml(deck)}</span>
            </span>
            <span class="paper-path-action">Read</span>
          </a>`;
}

function replaceMarkedBlock(source, marker, replacement) {
  const pattern = new RegExp(
    `(\\s*)<!-- ${escapeRegExp(marker)}:start -->[\\s\\S]*?\\n\\s*<!-- ${escapeRegExp(marker)}:end -->`,
  );
  const match = source.match(pattern);
  if (!match) {
    throw new Error(`marker block not found: ${marker}`);
  }

  const indent = match[1];
  return source.replace(pattern, `${indent}<!-- ${marker}:start -->\n${replacement}\n${indent}<!-- ${marker}:end -->`);
}

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function stringify(value) {
  return typeof value === "string" ? value.trim() : "";
}

function escapeHtml(value) {
  return stringify(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll("'", "&#39;");
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
