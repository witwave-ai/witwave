# website content operations

This folder keeps website content metadata and draft material separate from the rendered static pages.

## Content types

- `whitepapers.json` maps the foundational papers to website cards and the browser reader. Public drafts can use
  `status: "draft"`; hidden drafts should also set `display: false`.
- `team.json` maps the public working-team roster to names, roles, avatars, and source paths. Agent entries point back
  to agent cards; the human steward entry can omit an avatar and use a subdued presentation.
- `whitepapers/` exposes the Markdown files used by the browser reader. These entries are symlinked back to
  `social/papers/` so local previews reflect paper edits immediately.
- Blog posts are intentionally not mirrored here. The public blog reads `social/posts/posts.json` and the Markdown files
  it lists directly from the public `witwave-ai/witwave` repository.
- `marketing/` holds positioning copy that may be reused across the homepage, deck, and future landing pages.

## Operating rule

The website should make the content strategy obvious to an agent opening the folder cold. If a new page is added, add
the source or catalog entry that explains why the page exists and how it should be maintained.

When publishing to a separate website repository, copy with symlinks resolved (`rsync -aL`) so
`content/whitepapers/*.md` becomes regular files in the published source.
