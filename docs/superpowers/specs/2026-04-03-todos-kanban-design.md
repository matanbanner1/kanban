# Todos App Design
**Date:** 2026-04-03  
**Status:** Approved

## Overview

A companion todos app (`todos.html`) that lives alongside the existing `kanban.html` in the same repository and shares the same JSONBin.io bin. The two apps are separate files but linked — a todo item can be promoted into a kanban card with one click.

## Architecture

### Relationship to Kanban
- Two separate HTML files: `kanban.html` (existing) and `todos.html` (new)
- Same bin, same credentials — URL hash format `#MASTERKEY/BINID` is identical
- Same auth, polling (15s), and 700ms debounced save patterns copied from `kanban.html`
- Same CSS custom properties and visual language

### Data Model
The bin's JSON is extended with one new top-level key. `kanban.html` only reads `todo`, `inprogress`, and `done` — it ignores `todos` entirely.

```js
{
  // Existing kanban state (kanban.html reads only these)
  todo:       [],
  inprogress: [],
  done:       [],

  // New todos state (todos.html reads only this)
  todos: {
    today:    [],
    thisWeek: [],
    someday:  []
  }
}
```

**Todo item shape:**
```js
{ id, title, done: false }
```
- No `body` or tag fields — todos are single-line with no tag picker
- Tags/colors can be assigned after promoting to the kanban board

### Promotion Flow
Promoting a todo to the kanban board is a single atomic operation:
1. Remove item from `state.todos[section]`
2. Append `{ id: newUUID(), title: item.title, body: '', tag: '', tagClass: '' }` to `state.todo[]`
3. One `scheduleSave()` call syncs both changes in a single PUT to JSONBin

## UI Layout

Three columns side by side, mirroring the kanban board's visual structure. Each section column has a colored top border matching the kanban column colors:
- **Today** — red (`rgba(180,60,60,0.3)`)
- **This Week** — amber (`rgba(160,130,0,0.3)`)
- **Someday** — green (`rgba(40,130,60,0.3)`)

## Item Behavior

### Adding items
Each column has an inline `+ add item` input at the bottom. Press **Enter** to confirm, **Escape** to cancel. No modal needed — todos are single-line.

### Checking off
Clicking the checkbox marks the item done — title gets a strikethrough, item fades slightly. It stays in its section (no auto-archive). A **"Clear done"** button in the header removes all checked items across all sections.

### Promoting to kanban
A small `→ board` button appears on hover for each item. Clicking it removes the item from todos and adds it as a card in the kanban's "To Do" column. One debounced save handles both mutations atomically.

### Moving between sections
No drag-and-drop. A section picker dropdown appears on hover — "Today / This Week / Someday" — to re-file an item.

## Navigation

Each app links to the other in its header, passing credentials via the URL hash:

- `todos.html` header: `→ Kanban` link → `kanban.html` + `location.hash`
- `kanban.html` header: `→ Todos` link → `todos.html` + `location.hash`

This means no re-authentication when switching between apps.

The existing `manifest.json` stays pointed at `kanban.html` as the PWA entry point. `todos.html` is a companion, not a standalone PWA.

## Implementation Approach

Clone `kanban.html` as the starting point. Remove:
- Three-column board layout and `COLUMNS` config
- Drag-and-drop logic (`dragSrc`, `dragover`/`drop` handlers)
- `buildCard()` and card modal
- Label picker

Keep:
- All CSS custom properties and theme variables
- Auth (`parseConfig`, `saveConfig`, URL hash pattern)
- JSONBin API functions (`fetchState`, `pushState`, `createBin`)
- Polling and debounced save (`scheduleSave`, `schedulePoll`)
- Status indicator (dot + text)
- Theme toggle (light/dark)

Add:
- Three-section layout with colored top borders
- Inline add-item input per section
- Checkbox toggle with strikethrough
- "→ board" promote button (hover)
- Section re-file dropdown (hover)
- "Clear done" header button
- `→ Kanban` header link

## Files Changed

| File | Change |
|------|--------|
| `todos.html` | New file — companion todos app |
| `kanban.html` | Add `→ Todos` link in header |
| `.gitignore` | Add `.superpowers/` |
