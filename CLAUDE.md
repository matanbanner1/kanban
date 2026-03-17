# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Local Development

No build step. Open directly in a browser:

```
kanban/kanban.html#MASTERKEY/BINID
```

The URL hash supplies credentials — never a query string. `kanban/kanban-secrets.js` (gitignored) can hold a `MASTER_KEY` constant for local convenience but is not loaded by the app; credentials must always come from the URL hash.

## Deployment

Push to `main`. GitHub Actions (`.github/workflows/deploy.yml`) publishes the contents of `kanban/` to the `gh-pages` branch, which GitHub Pages serves from the repo root.

Live URL: `https://matanbanner1.github.io/kanban/kanban.html`

## Architecture

Everything lives in `kanban/kanban.html` — HTML, CSS, and JS in one file (~1200 lines). There is no framework, bundler, or external dependency.

**Backend:** JSONBin.io REST API (`https://api.jsonbin.io/v3`). The entire board state is a single JSON blob stored in one bin.

**Auth flow:** `parseConfig()` reads `location.hash` (format `#MASTERKEY/BINID`), falling back to `localStorage('kanban-config')`. `saveConfig()` writes back to both. The hash is never sent in HTTP requests.

**State shape:**
```js
{ todo: [], inprogress: [], done: [] }
// each card: { id (UUID), title, body, tag, tagClass }
```

**Data flow for any mutation:**
1. Mutate `state` directly
2. Call `renderBoard()` (full DOM re-render from state)
3. Call `scheduleSave()` → 700 ms debounce → `pushState()` (PUT to JSONBin)
4. After save, `schedulePoll()` resumes 15 s polling via `fetchState()`

**Rendering:** `renderBoard()` wipes `#board` and rebuilds from scratch. There is no virtual DOM or diffing. `buildCard()` constructs a single card element and attaches all its event listeners inline.

**Label picker:** `buildLabelPicker()` is a self-contained closure that returns `{ getValue() }`. It reads existing labels from global `state` at construction time. Color changes made during edit propagate to all cards sharing the same label name inside `commitModal()`.

**Theme:** Two parallel dark-mode paths — `@media (prefers-color-scheme: dark)` for the default, and `html[data-theme="dark"]` for the explicit toggle. The toggle stores preference in `localStorage('kanban-theme')`.

**Service worker** (`kanban/sw.js`): cache-first for the app shell (`CACHE = 'kanban-v2'`), network-only for `jsonbin.io` requests. Bump the cache version string when changing any cached file to force clients to update.
