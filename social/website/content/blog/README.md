# blog content

Blog entries are authored as Markdown files under `social/posts/` with YAML-style frontmatter. The browser cannot list a
directory on GitHub Pages, so `social/posts/posts.json` is the manifest that tells the website which Markdown files are
eligible to load.

The website does not keep a symlinked mirror of the posts in this folder. In production, the blog JavaScript resolves
the latest `main` commit SHA through the public GitHub API, then fetches the manifest and Markdown from commit-pinned
raw URLs. The publish workflow also generates static HTML pages for published posts so crawlers and link previews have
canonical URLs.

## Files

```text
social/posts/
├── posts.json              # manifest of Markdown files the browser can fetch
└── yyyy-mm-dd-title.md     # Markdown post with frontmatter
```

## Frontmatter contract

```yaml
---
title: "Post title"
slug: "2026-05-13-post-title"
status: "published" # draft | scheduled | published | archived
display: true
sample: false
published_at: "2026-05-13"
summary: >-
  Short card and reader summary. Use folded YAML for summaries that need to wrap across multiple source lines.
tags: ["agentic-ai", "software-engineering"]
surfaces: ["blog", "x", "linkedin"]
published_urls:
  blog: "https://witwave.ai/blog/2026-05-13-post-title/"
  x: null
  linkedin: null
---
```

Post filenames should follow `yyyy-mm-dd-title-goes-here.md`, and the `slug` should normally match the filename without
the `.md` extension.

Only posts with `status: "published"` and `display: true` appear on the public blog index and generated article pages.
Drafts can remain in the manifest for local checks without appearing publicly.

## Local preview

To preview unpublished local post changes, serve the repository root and open the site through its repo path:

```bash
python3 -m http.server 8080 --directory .
```

Then open `http://localhost:8080/social/website/blog/`.

Serving only `social/website/` is still useful for layout checks, but the blog will fetch public content from GitHub in
that mode.

## Empty state

Open `/social/website/blog/?empty=1` during repo-root preview, or `/blog/?empty=1` on the published site, to force the
no-posts state even when published posts exist.

## Distribution links

Use `published_urls` to add links after a post is shared elsewhere. Keep unavailable surfaces as `null`; the website
only renders links with real URLs.
