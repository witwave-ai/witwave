#!/usr/bin/env node
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, '..');
const siteDir = path.resolve(process.argv[2] || 'social/website');
const siteUrl = 'https://witwave.ai';
const socialImage = `${siteUrl}/assets/images/witwave-social-preview.png`;
const organizationId = `${siteUrl}/#organization`;
const today = new Date().toISOString().slice(0, 10);
const publishedPosts = [];

if (!fs.existsSync(siteDir)) {
  console.error(`site directory not found: ${siteDir}`);
  process.exit(1);
}

const generatedUrls = [];

const whitepaperCatalog = readJson(path.join(repoRoot, 'social/website/content/whitepapers.json'));
const visibleWhitepapers = (whitepaperCatalog.whitepapers || []).filter(isVisibleWhitepaper);
for (const paper of visibleWhitepapers) {
  generateWhitepaperPage(paper);
}

const postsCatalog = readJson(path.join(repoRoot, 'social/posts/posts.json'));
for (const entry of postsCatalog.posts || []) {
  generateBlogPostPage(entry);
}

rewritePublicArticleLinks(visibleWhitepapers);
writeRssFeed(publishedPosts);
writeSitemap(generatedUrls);
console.log(`Generated ${generatedUrls.length} social website pages into ${path.relative(repoRoot, siteDir) || siteDir}.`);

function generateWhitepaperPage(paper) {
  const markdownPath = path.join(repoRoot, paper.sourcePath);
  if (!fs.existsSync(markdownPath)) {
    throw new Error(`whitepaper source not found: ${paper.sourcePath}`);
  }

  const markdown = fs.readFileSync(markdownPath, 'utf8');
  const title = paper.title || headingFromMarkdown(markdown) || paper.slug;
  const description = paper.deck || `Read ${title} from Witwave.`;
  const slug = paper.slug;
  const pdfPath = paper.pdfPath || `whitepapers/${slug}/${slug}.pdf`;
  const outputDir = path.join(siteDir, 'whitepapers', slug);
  const canonicalUrl = `${siteUrl}/whitepapers/${slug}/`;
  const lastmod = paper.updatedAt || paper.lastmod || gitLastModified(paper.sourcePath) || fileLastModified(markdownPath);
  const statusLabel = paper.status && paper.status !== 'published' ? paper.status : '';
  const themes = normalizeThemes(paper.themes);
  const html = renderPage({
    depth: 2,
    title: `${title} | Witwave`,
    description,
    canonicalUrl,
    ogType: 'article',
    bodyClass: 'reader-page generated-page',
    mainClass: '',
    structuredData: [
      articleSchema({
        type: 'Article',
        title,
        description,
        canonicalUrl,
        authorName: 'Witwave',
        dateModified: lastmod,
        keywords: themes,
        about: themes.map(themeLabel),
      }),
      breadcrumbSchema([
        ['Home', `${siteUrl}/`],
        ['Whitepapers', `${siteUrl}/whitepapers/`],
        [title, canonicalUrl],
      ]),
    ],
    content: `
      <article class="markdown-paper generated-article">
        <div class="reader-actions generated-actions">
          <a class="button primary" href="../">All whitepapers</a>
          <a class="button secondary" href="../../${escapeAttr(pdfPath)}" download="${escapeAttr(slug)}.pdf">Download PDF</a>
        </div>
        ${statusLabel ? `<p class="article-status">${escapeHtml(statusLabel)}</p>` : ''}
        ${renderMarkdown(markdown).html}
      </article>
    `,
  });

  writeFile(path.join(outputDir, 'index.html'), html);
  generatedUrls.push({ loc: canonicalUrl, lastmod, priority: '0.8', changefreq: 'monthly' });
}

function isVisibleWhitepaper(paper) {
  return paper.display !== false && paper.status !== 'archived';
}

function normalizeThemes(themes) {
  return Array.isArray(themes) ? themes.map(stringify).filter(Boolean) : [];
}

function themeLabel(theme) {
  const acronyms = new Map([
    ['a2a', 'A2A'],
    ['ai', 'AI'],
    ['mcp', 'MCP'],
    ['sdlc', 'SDLC'],
  ]);

  return theme
    .split(/[-_]+/)
    .filter(Boolean)
    .map((part) => acronyms.get(part.toLowerCase()) || part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

function generateBlogPostPage(entry) {
  const markdownPath = path.join(repoRoot, entry.markdownPath);
  if (!fs.existsSync(markdownPath)) {
    throw new Error(`blog post source not found: ${entry.markdownPath}`);
  }

  const rawMarkdown = fs.readFileSync(markdownPath, 'utf8');
  const parsed = parseFrontmatter(rawMarkdown);
  const post = { ...entry, ...parsed.data };
  if (post.status !== 'published' || post.display === false) {
    return;
  }

  const slug = post.slug || entry.slug;
  const title = stringify(post.title) || headingFromMarkdown(parsed.body) || slug;
  const description = stringify(post.summary) || `Read ${title} from Witwave.`;
  const canonicalUrl = `${siteUrl}/blog/${slug}/`;
  const outputDir = path.join(siteDir, 'blog', slug);
  const articleBody = stripLeadingMarkdownHeading(parsed.body || '');
  const metaParts = [formatPostDate(post.published_at), stringify(post.author)].filter(Boolean);
  const articleEyebrow = stringify(post.author) ? 'Agent field note' : 'Field note';
  const authorChip = renderBlogAuthorChip(post, '../../');
  const lastmod = stringify(post.updated_at) || stringify(post.published_at) || gitLastModified(entry.markdownPath) || fileLastModified(markdownPath);
  publishedPosts.push({
    title,
    description,
    canonicalUrl,
    author: stringify(post.author) || 'Witwave',
    publishedAt: stringify(post.published_at),
    lastmod,
    categories: Array.isArray(post.tags) ? post.tags.map(stringify).filter(Boolean) : [],
  });
  const html = renderPage({
    depth: 2,
    title: `${title} | Witwave`,
    description,
    canonicalUrl,
    ogType: 'article',
    bodyClass: 'reader-page blog-reader-page generated-page',
    mainClass: '',
    structuredData: [
      articleSchema({
        type: 'BlogPosting',
        title,
        description,
        canonicalUrl,
        authorName: stringify(post.author) || 'Witwave',
        datePublished: stringify(post.published_at),
        dateModified: lastmod,
      }),
      breadcrumbSchema([
        ['Home', `${siteUrl}/`],
        ['Blog', `${siteUrl}/blog/`],
        [title, canonicalUrl],
      ]),
    ],
    content: `
      <article class="markdown-paper generated-article blog-article-body">
        <div class="reader-actions generated-actions">
          <a class="button primary" href="../">All posts</a>
        </div>
        <header class="blog-article-header">
          <p class="eyebrow">${escapeHtml(articleEyebrow)}</p>
          <h1>${escapeHtml(title)}</h1>
          ${description ? `<p>${escapeHtml(description)}</p>` : ''}
          ${authorChip}
          ${metaParts.length ? `<div class="blog-meta"><p>${escapeHtml(metaParts.join(' / '))}</p></div>` : ''}
        </header>
        ${renderMarkdown(articleBody).html}
      </article>
    `,
  });

  writeFile(path.join(outputDir, 'index.html'), html);
  generatedUrls.push({ loc: canonicalUrl, lastmod, priority: '0.7', changefreq: 'monthly' });
}

function renderBlogAuthorChip(post, prefix) {
  const author = stringify(post.author);
  if (!author) return '';

  return `
          <div class="blog-author-chip blog-author-chip-article" aria-label="Post author">
            <img src="${prefix}assets/images/team/piper.png" alt="" aria-hidden="true" />
            <span>
              <strong>${escapeHtml(author)}</strong>
              <small>Autonomous outreach agent</small>
            </span>
          </div>`;
}

function renderPage({ depth, title, description, canonicalUrl, ogType = 'website', bodyClass, mainClass, structuredData = [], content }) {
  const prefix = '../'.repeat(depth);
  const structuredDataHtml = structuredData.length
    ? `    <script type="application/ld+json">
${escapeScriptJson({
  '@context': 'https://schema.org',
  '@graph': structuredData,
})}
    </script>
`
    : '';
  const nav = [
    ['project', 'Project', `${prefix}project/`],
    ['whitepapers', 'Whitepapers', `${prefix}whitepapers/`],
    ['team', 'Team', `${prefix}team/`],
    ['blog', 'Blog', `${prefix}blog/`],
  ];

  return `<!doctype html>
<!--
  GENERATED FILE: Do not edit this HTML directly.
  Update the Markdown source or content manifest in the Witwave repository, then republish.
-->
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>${escapeHtml(title)}</title>
    <meta name="description" content="${escapeAttr(description)}" />
    <link rel="canonical" href="${escapeAttr(canonicalUrl)}" />
    <meta property="og:site_name" content="Witwave" />
    <meta property="og:type" content="${escapeAttr(ogType)}" />
    <meta property="og:title" content="${escapeAttr(title)}" />
    <meta property="og:description" content="${escapeAttr(description)}" />
    <meta property="og:url" content="${escapeAttr(canonicalUrl)}" />
    <meta property="og:image" content="${socialImage}" />
    <meta property="og:image:width" content="1280" />
    <meta property="og:image:height" content="640" />
    <meta property="og:image:alt" content="Witwave logo over a dark, high-tech interface background." />
    <meta name="twitter:card" content="summary_large_image" />
    <meta name="twitter:title" content="${escapeAttr(title)}" />
    <meta name="twitter:description" content="${escapeAttr(description)}" />
    <meta name="twitter:image" content="${socialImage}" />
    <meta name="twitter:image:alt" content="Witwave logo over a dark, high-tech interface background." />
${structuredDataHtml.trimEnd()}
    <link rel="alternate" type="application/rss+xml" title="Witwave field notes" href="${siteUrl}/feed.xml" />
    <link rel="icon" href="${prefix}assets/images/witwave-logo-terminal.svg" />
    <link rel="stylesheet" href="${prefix}assets/styles.css?v=blog-author-chip-20260522" />
  </head>
  <body${bodyClass ? ` class="${escapeAttr(bodyClass)}"` : ''}>
    <header class="site-header">
      <a class="brand" href="${prefix}index.html" aria-label="Witwave home">
        <img class="brand-logo" src="${prefix}assets/images/witwave-logo-terminal.svg" alt="" aria-hidden="true" />
      </a>
      <div class="header-actions">
        <nav class="nav" aria-label="Primary navigation">
          ${nav.map(([, label, href]) => `<a href="${href}">${label}</a>`).join('\n          ')}
        </nav>
        <a class="header-cta" href="${prefix}quickstart/">Quick Start</a>
      </div>
    </header>

    <main${mainClass ? ` class="${escapeAttr(mainClass)}"` : ''}>
${content.trimEnd()}
    </main>

    <footer class="site-footer">
      <p>Witwave publishes thinking on agent-native engineering and AI-assisted software systems.</p>
      <a href="${prefix}project/">Project</a>
    </footer>
  </body>
</html>
`;
}

function rewritePublicArticleLinks(whitepapers) {
  const pages = [
    { file: 'index.html', rootPrefix: '', paperPrefix: 'whitepapers/' },
    { file: 'project/index.html', rootPrefix: '../', paperPrefix: '../whitepapers/' },
    { file: 'team/index.html', rootPrefix: '../', paperPrefix: '../whitepapers/' },
    { file: 'whitepapers/index.html', rootPrefix: '../', paperPrefix: '' },
  ];

  for (const page of pages) {
    const filePath = path.join(siteDir, page.file);
    if (!fs.existsSync(filePath)) continue;

    let html = fs.readFileSync(filePath, 'utf8');
    for (const paper of whitepapers) {
      const sourceHref = `${page.rootPrefix}reader/?paper=${paper.slug}`;
      const staticHref = `${page.paperPrefix}${paper.slug}/`;
      html = html.replaceAll(`href="${sourceHref}"`, `href="${staticHref}"`);
    }
    writeFile(filePath, html);
  }
}

function writeRssFeed(posts) {
  const sortedPosts = [...posts].sort((left, right) => getDateTime(right.publishedAt || right.lastmod) - getDateTime(left.publishedAt || left.lastmod));
  const items = sortedPosts
    .map(
      (post) => `    <item>
      <title>${escapeXml(post.title)}</title>
      <link>${escapeXml(post.canonicalUrl)}</link>
      <guid isPermaLink="true">${escapeXml(post.canonicalUrl)}</guid>
      <description>${escapeXml(post.description)}</description>
      <dc:creator>${escapeXml(post.author)}</dc:creator>
      <pubDate>${escapeXml(toRssDate(post.publishedAt || post.lastmod))}</pubDate>
      ${post.categories.map((category) => `<category>${escapeXml(category)}</category>`).join('\n      ')}
    </item>`,
    )
    .join('\n');

  writeFile(
    path.join(siteDir, 'feed.xml'),
    `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>Witwave field notes</title>
    <link>${siteUrl}/blog/</link>
    <atom:link href="${siteUrl}/feed.xml" rel="self" type="application/rss+xml" />
    <description>Autonomously authored field notes from Piper Witwave on agent-native engineering, autonomous agent teams, and the Witwave project.</description>
    <language>en-us</language>
    <lastBuildDate>${escapeXml(toRssDate(today))}</lastBuildDate>
${items}
  </channel>
</rss>
`,
  );
}

function writeSitemap(extraUrls) {
  const urls = [
    { loc: `${siteUrl}/`, sourcePath: 'social/website/index.html', priority: '1.0', changefreq: 'weekly' },
    { loc: `${siteUrl}/project/`, sourcePath: 'social/website/project/index.html', priority: '0.9', changefreq: 'weekly' },
    { loc: `${siteUrl}/quickstart/`, sourcePath: 'social/website/quickstart/index.html', priority: '0.8', changefreq: 'monthly' },
    { loc: `${siteUrl}/whitepapers/`, sourcePath: 'social/website/whitepapers/index.html', priority: '0.8', changefreq: 'monthly' },
    { loc: `${siteUrl}/team/`, sourcePath: 'social/website/team/index.html', priority: '0.7', changefreq: 'monthly' },
    { loc: `${siteUrl}/blog/`, sourcePath: 'social/website/blog/index.html', priority: '0.7', changefreq: 'weekly' },
    ...extraUrls,
  ];

  const body = urls
    .map(
      (url) => `  <url>
    <loc>${escapeXml(url.loc)}</loc>
    <lastmod>${escapeXml(url.lastmod || gitLastModified(url.sourcePath) || today)}</lastmod>
    <changefreq>${url.changefreq}</changefreq>
    <priority>${url.priority}</priority>
  </url>`,
    )
    .join('\n');

  writeFile(
    path.join(siteDir, 'sitemap.xml'),
    `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${body}
</urlset>
`,
  );
}

function articleSchema({
  type,
  title,
  description,
  canonicalUrl,
  authorName,
  datePublished,
  dateModified,
  keywords = [],
  about = [],
}) {
  return compactObject({
    '@type': type,
    '@id': `${canonicalUrl}#article`,
    headline: title,
    description,
    url: canonicalUrl,
    image: socialImage,
    author: {
      '@type': authorName.toLowerCase() === 'witwave' ? 'Organization' : 'Person',
      name: authorName,
    },
    publisher: organizationSchema(),
    keywords: keywords.length ? keywords.join(', ') : undefined,
    about: about.length ? about.map((name) => ({ '@type': 'Thing', name })) : undefined,
    datePublished: datePublished || undefined,
    dateModified: dateModified || datePublished || undefined,
    mainEntityOfPage: canonicalUrl,
  });
}

function breadcrumbSchema(items) {
  return {
    '@type': 'BreadcrumbList',
    itemListElement: items.map(([name, item], index) => ({
      '@type': 'ListItem',
      position: index + 1,
      name,
      item,
    })),
  };
}

function organizationSchema() {
  return {
    '@type': 'Organization',
    '@id': organizationId,
    name: 'Witwave',
    url: `${siteUrl}/`,
    logo: `${siteUrl}/assets/images/witwave-logo-terminal.svg`,
    sameAs: ['https://github.com/witwave-ai'],
  };
}

function gitLastModified(relativePath) {
  if (!relativePath) return '';

  try {
    return execFileSync('git', ['-C', repoRoot, 'log', '-1', '--format=%cs', '--', relativePath], {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
    }).trim();
  } catch {
    return '';
  }
}

function fileLastModified(filePath) {
  try {
    return fs.statSync(filePath).mtime.toISOString().slice(0, 10);
  } catch {
    return '';
  }
}

function getDateTime(value) {
  if (!value) return 0;
  const timestamp = Date.parse(`${value}T00:00:00Z`);
  return Number.isNaN(timestamp) ? Date.parse(value) || 0 : timestamp;
}

function toRssDate(value) {
  const timestamp = getDateTime(value);
  const date = timestamp ? new Date(timestamp) : new Date();
  return date.toUTCString();
}

function renderMarkdown(markdown) {
  const lines = markdown.replace(/\r\n/g, '\n').split('\n');
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

    if (trimmed === '---') {
      html.push('<hr />');
      index += 1;
      continue;
    }

    if (trimmed.startsWith('```')) {
      const language = trimmed.slice(3).trim();
      const codeLines = [];
      index += 1;
      while (index < lines.length && !lines[index].trim().startsWith('```')) {
        codeLines.push(lines[index]);
        index += 1;
      }
      index += 1;
      html.push(`<pre><code${language ? ` data-language="${escapeAttr(language)}"` : ''}>${escapeHtml(codeLines.join('\n'))}</code></pre>`);
      continue;
    }

    const heading = trimmed.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      const level = heading[1].length;
      const text = heading[2].replace(/#+$/, '').trim();
      const id = uniqueSlug(text, usedIds);
      html.push(`<h${level} id="${id}">${renderInline(text)}</h${level}>`);
      index += 1;
      continue;
    }

    if (trimmed.startsWith('>')) {
      const quoteLines = [];
      while (index < lines.length && lines[index].trim().startsWith('>')) {
        quoteLines.push(lines[index].trim().replace(/^>\s?/, ''));
        index += 1;
      }
      html.push(`<blockquote><p>${renderInline(quoteLines.join(' '))}</p></blockquote>`);
      continue;
    }

    if (isTableStart(lines, index)) {
      const tableLines = [];
      while (index < lines.length && lines[index].trim().includes('|')) {
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
    html.push(`<p>${renderInline(paragraphLines.join(' '))}</p>`);
  }

  return { html: html.join('\n') };
}

function shouldContinueParagraph(lines, index) {
  const current = lines[index].trim();
  const next = lines[index + 1]?.trim() || '';
  if (!current) return false;
  if (current === '---') return false;
  if (current.startsWith('```') || current.startsWith('>')) return false;
  if (/^#{1,6}\s+/.test(current)) return false;
  if (getListType(current)) return false;
  if (isTableStart(lines, index)) return false;
  if (index > 0 && isTableStart(lines, index - 1)) return false;
  if (!next) return true;
  return true;
}

function getListType(trimmed) {
  if (/^[-*]\s+/.test(trimmed)) return 'ul';
  if (/^\d+\.\s+/.test(trimmed)) return 'ol';
  return null;
}

function collectList(lines, startIndex, type) {
  const items = [];
  let index = startIndex;
  const matcher = type === 'ol' ? /^\d+\.\s+(.+)$/ : /^[-*]\s+(.+)$/;

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
      if (getListType(candidateTrimmed) || /^#{1,6}\s+/.test(candidateTrimmed) || candidateTrimmed === '---') break;
      if (candidate.startsWith(' ') || candidate.startsWith('\t')) {
        itemLines.push(candidateTrimmed);
        index += 1;
        continue;
      }
      break;
    }

    items.push(itemLines.join(' '));
    if (!lines[index]?.trim()) break;
  }

  return { items, nextIndex: index, type };
}

function renderList(items, type) {
  const tag = type === 'ol' ? 'ol' : 'ul';
  return `<${tag}>${items.map((item) => `<li>${renderInline(item)}</li>`).join('')}</${tag}>`;
}

function isTableStart(lines, index) {
  const current = lines[index]?.trim() || '';
  const separator = lines[index + 1]?.trim() || '';
  return current.includes('|') && separator.includes('|') && /^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(separator);
}

function renderTable(tableLines) {
  const [headerLine, , ...bodyLines] = tableLines;
  const headers = splitTableRow(headerLine);
  const body = bodyLines.filter((line) => line.trim()).map(splitTableRow);
  return `
    <div class="table-scroll">
      <table>
        <thead><tr>${headers.map((cell) => `<th>${renderInline(cell)}</th>`).join('')}</tr></thead>
        <tbody>
          ${body.map((row) => `<tr>${row.map((cell) => `<td>${renderInline(cell)}</td>`).join('')}</tr>`).join('')}
        </tbody>
      </table>
    </div>
  `;
}

function splitTableRow(row) {
  return row
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim());
}

function renderInline(text) {
  const links = [];
  const codeSpans = [];
  let result = text.replace(/`([^`]+)`/g, (_, code) => {
    const token = `\u0000CODE${codeSpans.length}\u0000`;
    codeSpans.push(`<code>${escapeHtml(code)}</code>`);
    return token;
  });

  result = result.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, label, url) => {
    const token = `\u0000LINK${links.length}\u0000`;
    links.push(`<a href="${escapeAttr(url)}">${renderInline(label)}</a>`);
    return token;
  });

  result = escapeHtml(result);
  result = result.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  result = result.replace(/_([^_]+)_/g, '<em>$1</em>');
  result = result.replace(/\*([^*]+)\*/g, '<em>$1</em>');

  links.forEach((link, index) => {
    result = result.replace(`\u0000LINK${index}\u0000`, link);
  });
  codeSpans.forEach((code, index) => {
    result = result.replace(`\u0000CODE${index}\u0000`, code);
  });

  return result;
}

function parseFrontmatter(markdown) {
  const normalized = markdown.replace(/\r\n/g, '\n');
  const lines = normalized.split('\n');
  if (lines[0]?.trim() !== '---') return { data: {}, body: normalized };

  const endIndex = lines.findIndex((line, index) => index > 0 && line.trim() === '---');
  if (endIndex === -1) return { data: {}, body: normalized };

  const data = {};
  let activeMap = null;
  let activeKey = null;
  let activeScalarParts = null;
  let activeBlockScalar = null;

  for (const line of lines.slice(1, endIndex)) {
    if (activeBlockScalar) {
      if (!line.trim() || line.match(/^\s+/)) {
        activeBlockScalar.parts.push(line.replace(/^\s+/, ''));
        data[activeBlockScalar.key] = formatBlockScalar(activeBlockScalar);
        continue;
      }
      activeBlockScalar = null;
    }

    if (!line.trim()) continue;

    if (activeMap && activeKey && line.match(/^\s+/)) {
      const nested = line.match(/^\s+([A-Za-z0-9_-]+):\s*(.*)$/);
      if (nested && !activeScalarParts) {
        activeMap[nested[1]] = parseFrontmatterValue(nested[2]);
        continue;
      }

      const scalarContinuation = line.match(/^\s+(.+)$/);
      if (scalarContinuation) {
        activeScalarParts = [...(activeScalarParts || []), scalarContinuation[1].trim()];
        data[activeKey] = parseFrontmatterValue(activeScalarParts.join(' '));
      }
      continue;
    }

    const match = line.match(/^([A-Za-z0-9_-]+):\s*(.*)$/);
    if (!match) continue;

    const [, key, rawValue] = match;
    if (isBlockScalarMarker(rawValue)) {
      activeBlockScalar = { key, marker: rawValue, parts: [] };
      data[key] = '';
      activeMap = null;
      activeKey = null;
      activeScalarParts = null;
      continue;
    }

    if (rawValue === '') {
      data[key] = {};
      activeMap = data[key];
      activeKey = key;
      activeScalarParts = null;
      continue;
    }

    data[key] = parseFrontmatterValue(rawValue);
    activeMap = null;
    activeKey = null;
    activeScalarParts = null;
  }

  return { data, body: lines.slice(endIndex + 1).join('\n').trim() };
}

function isBlockScalarMarker(value) {
  return /^[>|][+-]?$/.test(value.trim());
}

function formatBlockScalar(block) {
  const text = block.marker.startsWith('|') ? block.parts.join('\n') : block.parts.join(' ').replace(/\s+/g, ' ');
  return block.marker.endsWith('+') ? text : text.trimEnd();
}

function parseFrontmatterValue(rawValue) {
  const value = rawValue.trim();
  if (value === 'null') return null;
  if (value === 'true') return true;
  if (value === 'false') return false;
  if (/^['"].*['"]$/.test(value)) return value.slice(1, -1);
  if (value.startsWith('[') && value.endsWith(']')) {
    return value
      .slice(1, -1)
      .split(',')
      .map((item) => parseFrontmatterValue(item.trim()))
      .filter((item) => item !== '');
  }
  return value;
}

function stripLeadingMarkdownHeading(markdown) {
  const lines = markdown.replace(/\r\n/g, '\n').split('\n');
  const firstContentIndex = lines.findIndex((line) => line.trim());
  if (firstContentIndex === -1) return '';
  if (/^#\s+/.test(lines[firstContentIndex].trim())) {
    return lines.slice(firstContentIndex + 1).join('\n').trim();
  }
  return markdown.trim();
}

function headingFromMarkdown(markdown) {
  const match = markdown.match(/^#\s+(.+)$/m);
  return match ? match[1].trim() : '';
}

function formatPostDate(value) {
  if (!value) return '';
  const date = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('en', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' }).format(date);
}

function uniqueSlug(text, usedIds) {
  const base = text
    .toLowerCase()
    .replace(/<[^>]+>/g, '')
    .replace(/[^a-z0-9\s-]/g, '')
    .trim()
    .replace(/\s+/g, '-') || 'section';
  const count = usedIds.get(base) || 0;
  usedIds.set(base, count + 1);
  return count ? `${base}-${count + 1}` : base;
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function writeFile(filePath, contents) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, contents);
}

function stringify(value) {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value)) return value.map(stringify).filter(Boolean).join(', ');
  return '';
}

function compactObject(value) {
  if (Array.isArray(value)) {
    return value.map(compactObject).filter((item) => item !== undefined);
  }

  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value)
        .filter(([, entryValue]) => entryValue !== undefined && entryValue !== '')
        .map(([key, entryValue]) => [key, compactObject(entryValue)]),
    );
  }

  return value;
}

function escapeScriptJson(value) {
  return JSON.stringify(value, null, 6).replace(/</g, '\\u003c');
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/`/g, '&#096;');
}

function escapeXml(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}
