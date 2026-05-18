const blogScript = document.currentScript;
const sourceRepository = {
  owner: "witwave-ai",
  repo: "witwave",
  branch: "main",
};
const sourceMainContentBase = `https://raw.githubusercontent.com/${sourceRepository.owner}/${sourceRepository.repo}/${sourceRepository.branch}/`;
const sourceMainRefUrl = `https://api.github.com/repos/${sourceRepository.owner}/${sourceRepository.repo}/git/ref/heads/${sourceRepository.branch}`;
const sourceContentBase = getSourceContentBase();

const blogList = document.querySelector("#blog-list");
const blogEmpty = document.querySelector("#blog-empty");
const blogPost = document.querySelector("#blog-post");
const blogPostTitle = document.querySelector("#blog-post-title");
const blogPostSummary = document.querySelector("#blog-post-summary");
const blogPostMeta = document.querySelector("#blog-post-meta");
const blogPostActions = document.querySelector("#blog-post-actions");
const blogPostNav = document.querySelector("#blog-post-nav");

initBlog().catch((error) => {
  console.error(error);
  if (blogList) {
    blogList.innerHTML = renderBlogError("Unable to load blog posts.", "Please try again in a moment.");
  }
  if (blogPost) {
    blogPost.innerHTML = renderBlogError("Unable to load this post.", "Please try again in a moment.");
  }
});

async function initBlog() {
  const posts = await loadPosts();

  if (blogList) {
    renderBlogIndex(posts);
  }

  if (blogPost) {
    renderBlogPost(posts);
  }
}

async function loadPosts() {
  const contentBase = await resolveContentBase();
  const blogCatalogUrl = new URL("social/posts/posts.json", contentBase);
  const catalog = await fetchJson(blogCatalogUrl);
  const entries = catalog.posts || [];
  const loaded = await Promise.all(
    entries.map(async (entry) => {
      const markdownUrl = new URL(entry.markdownPath, contentBase);
      const rawMarkdown = await fetchText(markdownUrl);
      const parsed = parseFrontmatter(rawMarkdown);
      return {
        ...entry,
        ...parsed.data,
        markdownPath: entry.markdownPath,
        body: parsed.body,
      };
    }),
  );

  return loaded.sort((left, right) => getPostTime(right) - getPostTime(left));
}

async function resolveContentBase() {
  if (!shouldResolveGitHubCommit(sourceContentBase)) {
    return sourceContentBase;
  }

  try {
    const ref = await fetchJson(sourceMainRefUrl);
    const sha = ref?.object?.sha;
    if (isGitSha(sha)) {
      return `https://raw.githubusercontent.com/${sourceRepository.owner}/${sourceRepository.repo}/${sha}/`;
    }
    console.warn("GitHub ref response did not include a valid commit SHA; falling back to raw main.");
  } catch (error) {
    console.warn("Unable to resolve latest blog content SHA; falling back to raw main.", error);
  }

  return sourceContentBase;
}

function shouldResolveGitHubCommit(base) {
  try {
    const url = new URL(base);
    const path = url.pathname.replace(/\/+$/, "");
    const mainPath = `/${sourceRepository.owner}/${sourceRepository.repo}/${sourceRepository.branch}`;
    return url.origin === "https://raw.githubusercontent.com" && path === mainPath;
  } catch {
    return false;
  }
}

function isGitSha(value) {
  return typeof value === "string" && /^[a-f0-9]{40}$/i.test(value);
}

function getSourceContentBase() {
  const override = blogScript?.dataset.sourceBase;
  if (override) {
    return new URL(override, window.location.href).href;
  }

  const isLocalHost = ["localhost", "127.0.0.1"].includes(window.location.hostname);
  if (isLocalHost && window.location.pathname.includes("/social/website/")) {
    return new URL("/", window.location.href).href;
  }

  return sourceMainContentBase;
}

function renderBlogIndex(posts) {
  const params = new URLSearchParams(window.location.search);
  const visiblePosts = params.has("empty") ? [] : posts.filter(isDisplayablePost);

  if (!visiblePosts.length) {
    blogList.hidden = true;
    blogEmpty.hidden = false;
    return;
  }

  blogEmpty.hidden = true;
  blogList.hidden = false;
  blogList.innerHTML = visiblePosts.map(renderBlogCard).join("");
}

function renderBlogPost(posts) {
  const visiblePosts = posts.filter(isDisplayablePost);
  const requestedSlug = new URLSearchParams(window.location.search).get("post");
  const selected = visiblePosts.find((post) => post.slug === requestedSlug) || visiblePosts[0];
  const selectedTitle = getPostTitle(selected);
  const selectedSummary = getPostSummary(selected);

  if (!selected) {
    document.title = "No Blog Posts | Witwave";
    blogPostTitle.textContent = "Reader controls";
    blogPostSummary.textContent = "No published posts are available yet.";
    blogPostMeta.innerHTML = "";
    blogPost.innerHTML = renderBlogError(
      "No published posts yet.",
      "The first field notes are still being shaped. Start with the whitepapers in the meantime.",
    );
    blogPostActions.innerHTML = `<a class="button primary" href="../">Back to blog</a>`;
    blogPostNav.innerHTML = "";
    return;
  }

  document.title = `${selectedTitle} | Witwave`;
  blogPostTitle.textContent = "Reader controls";
  blogPostSummary.textContent = "Choose a field note or return to the blog archive.";
  blogPostMeta.innerHTML = "";
  blogPostActions.innerHTML = renderPostActions(selected);
  blogPostNav.innerHTML = renderPostNav(visiblePosts, selected.slug);
  blogPost.innerHTML = renderBlogArticle(selected, selectedTitle, selectedSummary);
}

function renderBlogArticle(post, title, summary) {
  const articleBody = stripLeadingMarkdownHeading(post.body || "");
  return `
    <header class="blog-article-header">
      <p class="eyebrow">Field note</p>
      <h1 id="blog-article-title">${escapeHtml(title)}</h1>
      ${summary ? `<p>${escapeHtml(summary)}</p>` : ""}
      <div class="blog-meta">${renderPostMeta(post)}</div>
    </header>
    <div class="blog-article-body">
      ${renderMarkdown(articleBody).html}
    </div>
  `;
}

function stripLeadingMarkdownHeading(markdown) {
  const lines = markdown.replace(/\r\n/g, "\n").split("\n");
  const firstContentIndex = lines.findIndex((line) => line.trim());
  if (firstContentIndex === -1) return "";

  if (/^#\s+/.test(lines[firstContentIndex].trim())) {
    return lines
      .slice(firstContentIndex + 1)
      .join("\n")
      .trim();
  }

  return markdown.trim();
}

function renderBlogCard(post) {
  const tags = (post.tags || []).slice(0, 4);
  const socialLinks = renderDistributionLinks(post, "blog-card-socials");
  const title = getPostTitle(post);
  const summary = getPostSummary(post);
  return `
    <article class="blog-card${post.sample ? " sample" : ""}">
      <div class="blog-card-topline">
        <span>${escapeHtml(formatPostDate(post.published_at))}</span>
        ${post.sample ? '<span class="status-pill">Sample</span>' : '<span class="status-pill">Published</span>'}
      </div>
      <h2>${escapeHtml(title)}</h2>
      <p>${escapeHtml(summary)}</p>
      ${tags.length ? `<ul class="tag-list compact">${tags.map((tag) => `<li>${escapeHtml(tag)}</li>`).join("")}</ul>` : ""}
      <div class="blog-card-actions">
        <a class="text-link" href="${escapeAttr(getPostHref(post))}">Read post</a>
        ${socialLinks}
      </div>
    </article>
  `;
}

function renderPostMeta(post) {
  const pieces = [formatPostDate(post.published_at), post.status || "published"];
  if (post.sample) pieces.push("sample");
  return `<p>${pieces.map(escapeHtml).join(" / ")}</p>`;
}

function renderPostActions(post) {
  const distribution = renderDistributionLinks(post, "reader-actions blog-distribution-links");
  return `
    <a class="button primary" href="../">All posts</a>
    ${distribution}
  `;
}

function renderPostNav(posts, activeSlug) {
  return posts
    .map((post) => {
      const activeClass = post.slug === activeSlug ? " active" : "";
      return `
        <a class="reader-paper-link${activeClass}" href="?post=${encodeURIComponent(post.slug)}">
          <span>${escapeHtml(getPostTitle(post))}</span>
          <small>${escapeHtml((post.tags || []).slice(0, 2).join(" / "))}</small>
        </a>
      `;
    })
    .join("");
}

function renderDistributionLinks(post, className) {
  const urls = post.published_urls || {};
  const links = Object.entries(urls)
    .filter(([surface, url]) => surface !== "blog" && url)
    .map(
      ([surface, url]) =>
        `<a href="${escapeAttr(url)}" target="_blank" rel="noreferrer">${escapeHtml(getSurfaceLabel(surface))}</a>`,
    );

  if (!links.length) return "";
  return `<div class="${className}" aria-label="Published elsewhere">${links.join("")}</div>`;
}

function isDisplayablePost(post) {
  return post.display !== false && post.status === "published";
}

function getPostTime(post) {
  if (!post.published_at) return 0;
  const timestamp = Date.parse(post.published_at);
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

function getPostTitle(post) {
  return stringifyFrontmatterText(post?.title) || stringifyFrontmatterText(post?.slug) || "Untitled post";
}

function getPostSummary(post) {
  return stringifyFrontmatterText(post?.summary);
}

function getPostHref(post) {
  if (isLocalPreview()) {
    return `post/?post=${encodeURIComponent(post.slug)}`;
  }

  const publishedUrl = post?.published_urls?.blog;
  if (publishedUrl) {
    try {
      const url = new URL(publishedUrl);
      if (url.origin === window.location.origin) {
        return url.pathname;
      }
      return url.href;
    } catch {
      return publishedUrl;
    }
  }

  return `${window.location.origin}/blog/${encodeURIComponent(post.slug)}/`;
}

function isLocalPreview() {
  return window.location.protocol === "file:" || ["localhost", "127.0.0.1"].includes(window.location.hostname);
}

function stringifyFrontmatterText(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map(stringifyFrontmatterText).filter(Boolean).join(", ");
  return "";
}

function formatPostDate(value) {
  if (!value) return "Unscheduled";
  const date = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }).format(
    date,
  );
}

function getSurfaceLabel(surface) {
  const labels = {
    twitter: "X",
    x: "X",
    linkedin: "LinkedIn",
    mastodon: "Mastodon",
    threads: "Threads",
    bluesky: "Bluesky",
    "github-discussion": "GitHub Discussion",
    hn: "Hacker News",
    newsletter: "Newsletter",
  };
  return labels[surface] || surface;
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

function parseFrontmatter(markdown) {
  const normalized = markdown.replace(/\r\n/g, "\n");
  const lines = normalized.split("\n");

  if (lines[0]?.trim() !== "---") {
    return { data: {}, body: normalized };
  }

  const endIndex = lines.findIndex((line, index) => index > 0 && line.trim() === "---");
  if (endIndex === -1) {
    return { data: {}, body: normalized };
  }

  const data = {};
  let activeMap = null;
  let activeKey = null;

  for (const line of lines.slice(1, endIndex)) {
    if (!line.trim()) continue;

    const nested = line.match(/^\s+([A-Za-z0-9_-]+):\s*(.*)$/);
    if (nested && activeMap) {
      activeMap[nested[1]] = parseFrontmatterValue(nested[2]);
      continue;
    }

    const scalarContinuation = line.match(/^\s+(.+)$/);
    if (scalarContinuation && activeMap && activeKey && !Object.keys(activeMap).length) {
      data[activeKey] = parseFrontmatterValue(scalarContinuation[1]);
      activeMap = null;
      activeKey = null;
      continue;
    }

    const match = line.match(/^([A-Za-z0-9_-]+):\s*(.*)$/);
    if (!match) continue;

    const [, key, rawValue] = match;
    if (rawValue === "") {
      data[key] = {};
      activeMap = data[key];
      activeKey = key;
      continue;
    }

    data[key] = parseFrontmatterValue(rawValue);
    activeMap = null;
    activeKey = null;
  }

  return {
    data,
    body: lines
      .slice(endIndex + 1)
      .join("\n")
      .trim(),
  };
}

function parseFrontmatterValue(rawValue) {
  const value = rawValue.trim();
  if (value === "null" || value === "~") return null;
  if (value === "true") return true;
  if (value === "false") return false;
  if (value.startsWith("[") && value.endsWith("]")) {
    const inner = value.slice(1, -1).trim();
    if (!inner) return [];
    return inner.split(",").map((item) => stripQuotes(item.trim()));
  }
  return stripQuotes(value);
}

function stripQuotes(value) {
  if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
    return value.slice(1, -1);
  }
  return value;
}

function renderBlogError(title, message) {
  return `
    <div class="reader-error">
      <h2>${escapeHtml(title)}</h2>
      <p>${escapeHtml(message)}</p>
    </div>
  `;
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
  if (!current) return false;
  if (current === "---") return false;
  if (current.startsWith("```") || current.startsWith(">")) return false;
  if (/^#{1,6}\s+/.test(current)) return false;
  if (getListType(current)) return false;
  if (isTableStart(lines, index)) return false;
  if (index > 0 && isTableStart(lines, index - 1)) return false;
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
