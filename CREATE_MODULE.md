# Create Module — GeoCities-Style Website Builder

## Overview

The **Create** module lets every user build and host personal web pages attached to
their profile.  Think GeoCities (1996) rebuilt for the HOMEPAGE platform.

Pages are published at:

```
/users/{username}/created/               ← index of all pages
/users/{username}/created/{page_slug}    ← individual page
```

The builder is a full visual editor with **drag-and-drop** element placement,
live preview, and a raw **HTML / CSS** code panel.

---

## Architecture

### Data model (`app/models.py`)

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `owner_id` | FK → users.id | Who owns this page |
| `slug` | String | URL-safe name, unique per user |
| `title` | String | Human-readable title |
| `html_content` | Text | Full HTML body (sanitised on save) |
| `css_content` | Text | Custom CSS scoped to the page |
| `page_json` | Text | JSON of the visual-editor element tree (for round-trip editing) |
| `is_published` | Boolean | Draft vs live |
| `created_at` | DateTime | |
| `updated_at` | DateTime | |

**Unique constraint**: `(owner_id, slug)`.

### Router (`app/routers/create.py`)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/create` | user | Dashboard — list my pages |
| GET | `/create/new` | user | Open editor (blank) |
| GET | `/create/edit/{page_id}` | owner | Open editor for existing page |
| POST | `/create/save` | owner | Save page (JSON body) |
| POST | `/create/delete/{page_id}` | owner | Delete a page |
| GET | `/users/{username}/created/` | public | List published pages |
| GET | `/users/{username}/created/{slug}` | public | Serve published page |

The public "hosting" routes are registered on the **users** prefix so they live
at the natural URL without requiring the `create` module prefix in the path.

### Templates

| File | Purpose |
|---|---|
| `templates/create_dashboard.html` | "My Pages" list |
| `templates/create_editor.html` | Drag-and-drop + code editor |
| `templates/create_viewer.html` | Serves a published page to visitors |
| `templates/create_pages_list.html` | Public index of a user's pages |

### Visual Editor Features (client-side)

The editor is entirely in-browser JavaScript (no extra dependencies):

1. **Element Toolbar** — insert headings (h1-h6), paragraphs, images, links,
   divs, horizontal rules, lists, and spacers.
2. **Drag & Drop** — elements on the canvas can be reordered via drag handles.
3. **Inline Editing** — click any text element to edit it in place (`contenteditable`).
4. **Property Panel** — when an element is selected, a right sidebar shows:
   - tag type, CSS classes, inline styles, link href, image src
5. **Code View** — toggleable split pane with raw HTML and CSS editors.
   Switching to code view serialises the canvas; switching back parses and
   rebuilds it.
6. **Live Preview** — an iframe preview that updates on every change.
7. **Background Picker** — choose page background colour / image / gradient.
8. **Save / Publish** — save as draft or publish in one click.

### Module Registration

* Added to `ALL_OPTIONAL` in `app/config.py`.
* Seeded in `init_default_modules()` in `app/server_utils.py`.
* Module-guard prefix `/create` added to `_OPTIONAL_PREFIX_MAP` in `app/main.py`.
* Conditionally imported and included in `app/main.py`.
* Public viewer routes registered on the **users** router so they don't require
  the Create module prefix in their path — they always load because `/users`
  is a core router.  The viewer routes check the DB for pages; if the module is
  disabled they simply 404 (no pages can be created, no pages exist).

### Storage

Page assets (images uploaded _inside_ the builder) are stored under the
existing `media/` directory via the platform's `media_utils` upload flow.
The `<img>` tags reference `/media/{filename}` as usual.

### Content Sanitisation

All HTML saved through the editor is sanitised server-side:
* `<script>`, `<iframe>`, `<object>`, `<embed>`, event handler attributes
  (`onclick`, `onerror`, etc.) and `javascript:` URIs are stripped.
* CSS is restricted to safe properties (no `expression()`, `url()` pointing
  offsite, etc.).

### Security Boundaries

* Only the page **owner** (or an admin) can edit / delete.
* A user cannot write pages for another user's profile.
* Published pages are visible to all authenticated users.
* Content-filter and forbidden-word checks apply to HTML content.

---

## Files Changed / Created

| File | Change |
|---|---|
| `app/models.py` | Add `CreatedPage` model |
| `app/routers/create.py` | **New** — all CRUD + hosting routes |
| `app/main.py` | Import & include `create` router |
| `app/config.py` | Add `"create"` to `ALL_OPTIONAL` |
| `app/server_utils.py` | Seed `create` module in defaults |
| `data/enabled_modules.json` | Add `"create"` |
| `templates/create_dashboard.html` | **New** |
| `templates/create_editor.html` | **New** — visual builder |
| `templates/create_viewer.html` | **New** — hosted page renderer |
| `templates/create_pages_list.html` | **New** — public page index |
| `CREATE_MODULE.md` | **New** — this document |

---

## Future Session Notes

* **Alembic migration** — after the code lands, run
  `alembic revision --autogenerate -m "add created_pages table"` and
  `alembic upgrade head`.  If using the auto-create-tables path
  (`Base.metadata.create_all`), the table appears automatically on next start.
* **Federation** — pages could be announced as ActivityPub `Article` objects
  in a future phase.
* **Themes** — pre-built GeoCities-style theme templates for the editor
  (starfield backgrounds, under-construction GIFs, visitor counters).
* **Asset uploads inside editor** — drag-to-upload images into the builder
  canvas, stored via existing media pipeline.
* **Visitor counter** — simple hit counter per page, displayed as a retro
  badge.
