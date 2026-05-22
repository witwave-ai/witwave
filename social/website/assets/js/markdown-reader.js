const catalogUrl = new URL("../content/whitepapers.json", window.location.href);
const readerTitle = document.querySelector("#reader-title");
const readerDeck = document.querySelector("#reader-deck");
const readerActions = document.querySelector("#reader-actions");
const readerPaperNav = document.querySelector("#reader-paper-nav");
const markdownPaper = document.querySelector("#markdown-paper");

initReader().catch((error) => {
  console.error(error);
  markdownPaper.innerHTML = `
    <div class="reader-error">
      <h2>Unable to load this paper.</h2>
      <p>Please try again in a moment.</p>
    </div>
  `;
});

async function initReader() {
  const catalog = await fetchJson(catalogUrl);
  const papers = catalog.whitepapers || [];
  const requestedSlug = new URLSearchParams(window.location.search).get("paper");
  const selected = papers.find((paper) => paper.slug === requestedSlug) || papers[0];

  if (!selected) {
    throw new Error("No whitepapers configured");
  }

  document.title = `${selected.shortTitle || selected.title} | Witwave`;
  readerTitle.textContent = "Reader controls";
  readerDeck.textContent = "Choose a paper, download a PDF or Markdown source, or return to the archive.";
  renderActions(selected);
  renderPaperNav(papers, selected.slug);

  const markdownUrl = new URL(`../${selected.markdownPath}`, window.location.href);
  const markdown = await fetchText(markdownUrl);
  const rendered = renderMarkdown(markdown);
  markdownPaper.innerHTML = rendered.html;
}

async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Failed to fetch ${url}: ${response.status}`);
  }
  return response.json();
}

async function fetchText(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Failed to fetch ${url}: ${response.status}`);
  }
  return response.text();
}

function renderActions(paper) {
  const pdfPath = paper.pdfPath || `whitepapers/${paper.slug}/${paper.slug}.pdf`;
  readerActions.innerHTML = `
    <a class="button primary" href="../whitepapers/">All whitepapers</a>
    <a class="button secondary" href="../${escapeAttr(pdfPath)}" download="${escapeAttr(paper.slug)}.pdf">
      Download PDF
    </a>
    <a class="button secondary" href="../${escapeAttr(paper.markdownPath)}" download="${escapeAttr(paper.slug)}.md">
      Download MD
    </a>
  `;
}

function renderPaperNav(papers, activeSlug) {
  readerPaperNav.innerHTML = papers
    .map((paper) => {
      const activeClass = paper.slug === activeSlug ? " active" : "";
      return `
        <a class="reader-paper-link${activeClass}" href="?paper=${encodeURIComponent(paper.slug)}">
          <span>${escapeHtml(paper.shortTitle || paper.title)}</span>
          <small>${escapeHtml((paper.themes || []).slice(0, 2).join(" / "))}</small>
        </a>
      `;
    })
    .join("");
}

function renderMarkdown(markdown) {
  const lines = markdown.replace(/\r\n/g, "\n").split("\n");
  const html = [];
  const usedIds = new Map();
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    const trimmed = line.trim();

    if (!trimmed) {
      index += 1;
      continue;
    }

    if (trimmed === "---") {
      html.push("<hr />");
      index += 1;
      continue;
    }

    if (trimmed.startsWith("```")) {
      const language = trimmed.slice(3).trim();
      const codeLines = [];
      index += 1;
      while (index < lines.length && !lines[index].trim().startsWith("```")) {
        codeLines.push(lines[index]);
        index += 1;
      }
      index += 1;
      html.push(
        `<pre><code${language ? ` data-language="${escapeAttr(language)}"` : ""}>${escapeHtml(
          codeLines.join("\n"),
        )}</code></pre>`,
      );
      continue;
    }

    const heading = trimmed.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      const level = heading[1].length;
      const text = heading[2].replace(/#+$/, "").trim();
      const id = uniqueSlug(text, usedIds);
      html.push(`<h${level} id="${id}">${renderInline(text)}</h${level}>`);
      index += 1;
      continue;
    }

    if (trimmed.startsWith(">")) {
      const quoteLines = [];
      while (index < lines.length && lines[index].trim().startsWith(">")) {
        quoteLines.push(lines[index].trim().replace(/^>\s?/, ""));
        index += 1;
      }
      html.push(`<blockquote><p>${renderInline(quoteLines.join(" "))}</p></blockquote>`);
      continue;
    }

    if (isTableStart(lines, index)) {
      const tableLines = [];
      while (index < lines.length && lines[index].trim().includes("|")) {
        tableLines.push(lines[index]);
        index += 1;
      }
      html.push(renderTable(tableLines));
      continue;
    }

    const listType = getListType(trimmed);
    if (listType) {
      const parsedList = collectList(lines, index, listType);
      html.push(renderList(parsedList.items, parsedList.type));
      index = parsedList.nextIndex;
      continue;
    }

    const paragraphLines = [];
    while (index < lines.length && shouldContinueParagraph(lines, index)) {
      paragraphLines.push(lines[index].trim());
      index += 1;
    }
    html.push(`<p>${renderInline(paragraphLines.join(" "))}</p>`);
  }

  return { html: html.join("\n") };
}

function shouldContinueParagraph(lines, index) {
  const current = lines[index].trim();
  const next = lines[index + 1]?.trim() || "";
  if (!current) return false;
  if (current === "---") return false;
  if (current.startsWith("```") || current.startsWith(">")) return false;
  if (/^#{1,6}\s+/.test(current)) return false;
  if (getListType(current)) return false;
  if (isTableStart(lines, index)) return false;
  if (index > 0 && isTableStart(lines, index - 1)) return false;
  if (!next) return true;
  return true;
}

function getListType(trimmed) {
  if (/^[-*]\s+/.test(trimmed)) return "ul";
  if (/^\d+\.\s+/.test(trimmed)) return "ol";
  return null;
}

function collectList(lines, startIndex, type) {
  const items = [];
  let index = startIndex;
  const matcher = type === "ol" ? /^\d+\.\s+(.+)$/ : /^[-*]\s+(.+)$/;

  while (index < lines.length) {
    const trimmed = lines[index].trim();
    const match = trimmed.match(matcher);
    if (!match) break;

    const itemLines = [match[1]];
    index += 1;

    while (index < lines.length) {
      const candidate = lines[index];
      const candidateTrimmed = candidate.trim();
      if (!candidateTrimmed) break;
      if (getListType(candidateTrimmed) || /^#{1,6}\s+/.test(candidateTrimmed) || candidateTrimmed === "---") break;
      if (candidate.startsWith(" ") || candidate.startsWith("\t")) {
        itemLines.push(candidateTrimmed);
        index += 1;
        continue;
      }
      break;
    }

    items.push(itemLines.join(" "));

    if (!lines[index]?.trim()) {
      break;
    }
  }

  return { items, nextIndex: index, type };
}

function renderList(items, type) {
  const tag = type === "ol" ? "ol" : "ul";
  return `<${tag}>${items.map((item) => `<li>${renderInline(item)}</li>`).join("")}</${tag}>`;
}

function isTableStart(lines, index) {
  const current = lines[index]?.trim() || "";
  const separator = lines[index + 1]?.trim() || "";
  return (
    current.includes("|") && separator.includes("|") && /^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(separator)
  );
}

function renderTable(tableLines) {
  const [headerLine, , ...bodyLines] = tableLines;
  const headers = splitTableRow(headerLine);
  const body = bodyLines.filter((line) => line.trim()).map(splitTableRow);
  return `
    <div class="table-scroll">
      <table>
        <thead><tr>${headers.map((cell) => `<th>${renderInline(cell)}</th>`).join("")}</tr></thead>
        <tbody>
          ${body.map((row) => `<tr>${row.map((cell) => `<td>${renderInline(cell)}</td>`).join("")}</tr>`).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function splitTableRow(row) {
  return row
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function renderInline(text) {
  const codeSpans = [];
  let result = text.replace(/`([^`]+)`/g, (_, code) => {
    const token = `@@CODE${codeSpans.length}@@`;
    codeSpans.push(`<code>${escapeHtml(code)}</code>`);
    return token;
  });

  result = escapeHtml(result);
  result = result.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, label, url) => {
    const target = /^https?:\/\//.test(url) ? ' target="_blank" rel="noreferrer"' : "";
    return `<a href="${escapeAttr(url)}"${target}>${label}</a>`;
  });
  result = result.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  result = result.replace(/(^|\s)_([^_]+)_(?=\s|[.,;:!?)]|$)/g, "$1<em>$2</em>");
  result = result.replace(/(^|\s)\*([^*]+)\*(?=\s|[.,;:!?)]|$)/g, "$1<em>$2</em>");

  codeSpans.forEach((code, codeIndex) => {
    result = result.replace(`@@CODE${codeIndex}@@`, code);
  });

  return result;
}

function uniqueSlug(text, usedIds) {
  const base =
    text
      .replace(/`([^`]+)`/g, "$1")
      .toLowerCase()
      .replace(/&/g, " and ")
      .replace(/[^a-z0-9\s-]/g, "")
      .trim()
      .replace(/\s+/g, "-") || "section";
  const count = usedIds.get(base) || 0;
  usedIds.set(base, count + 1);
  return count === 0 ? base : `${base}-${count + 1}`;
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/`/g, "&#096;");
}
