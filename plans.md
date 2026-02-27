# HOMEPAGE Platform (Plans) v1.1

## 0. Purpose
Build **HOMEPAGE**, a **locally hosted, family‑safe, nostalgia‑era internet** platform with optional federation between trusted servers. Individual servers (nodes) can be named uniquely (e.g., "House Fantastico").

Design goals:
- No algorithms
- No deepfakes
- No porn / NSFW
- No tracking
- Human‑moderated
- Simple, fast, readable
- Neighborhood‑scale communities

This document is the authoritative execution plan.

---

## 1. Guiding Principles
- Local‑first
- Federation by **whitelist only**
- Policy‑aware handshake
- Minimal content types
- Human moderation
- Portability of identity
- Open formats
- Personalization via Themes

---

### 2.1 Server Node (Host)
Runs (inside Podman containers or as a standalone service):
- Core Engine (FastAPI)
- Database (SQLite/PostgreSQL)
- Identity & Federation (ActivityPub)
- Media Storage (Media Utils)

### 2.2 Dedicated Client (Client)
A specialized application that connects to the host server without a standard web browser.
- Interfaces with the server API
- Native OS integration (notifications, file system)
- Offline cache for browsing without network
- Simplified, distraction-free UI

---

## 3. Core Features

### 3.1 Local Hosting
- Runs on LAN
- Works offline within network
- No external dependencies

### 3.2 Content Types (Phase 1)
- Plain text posts (signed)
- Markdown pages
- Static images
- Audio

### 3.3 Dashboard (Launchpad)
- A central hub ("Home") displaying available features and future expansions.
- "Coming Soon" placeholders for Photos, Files, and Calendar.

### 3.4 Identity
Format:
```
username@servername
```
Accounts exist only on their home server.

---

## 4. Personalization & Themes
HOMEPAGE supports app-wide themes that change the entire visual identity of the node for a user.

### 4.1 Theme Presets
- **Midnight**: Dark, high-contrast slate and blue.
- **Retro**: Windows 95 / Classic OS aesthetic (gray, beveled borders).
- **Ocean**: Deep blues and teals.
- **Sunset**: Warm roses and oranges.
- **Forest**: Deep greens and timber tones.

### 4.2 Custom CSS
Users can inject their own CSS into their profile pages for advanced "MySpace-style" personalization.

---

## 11. Task Tracking

### Task: Project Scaffolding (V2)
Goal: Rebuild the core as a robust, federated-ready FastAPI app.
Status: **COMPLETE**
Solutions: 
- Switched to FastAPI + SQLAlchemy (Async).
- Implemented Pydantic schemas for data validation.
- Organized routers for Auth, Posts, Users, and Federation.

### Task: User Accounts & Profiles
Goal: Register, login, and customize profiles.
Status: **COMPLETE**
Solutions:
- PBKDF2 password hashing.
- JWT-based authentication (Cookies).
- Profile editing with Display Name, Bio, and Avatar URL.
- **Theme Selection**: Integration of theme presets into user settings.

### Task: Posting & Feed
Goal: Create posts and view them in a reverse-chronological feed.
Status: **COMPLETE**
Solutions:
- Post creation with Content-ID signing.
- `/feed` endpoint for the social timeline.
- Signature verification skeleton for federated posts.

### Task: Dashboard Home
Goal: Create a central landing page for the HOMEPAGE platform.
Status: **COMPLETE**
Solutions:
- New `/` route serving a "Launchpad" UI.
- Unified navigation with "Home", "Feed", and "Community".

### Task: Immersive Themes
Goal: Make themes impact the entire app, not just profiles.
Status: **COMPLETE**
Solutions:
- Refactored `base.html` to use global CSS variables (`--bg`, `--primary`).
- Applied `theme-` class to the `<body>` tag based on user preference.
- Customized Retro theme to override border-radii and transitions globally.

### Task: Federation (ActivityPub)
Goal: Foundation for cross-server communication.
Status: **IN PROGRESS**
Next Actions:
- Finalize Inbox delivery logic.
- Implement Outbox for social federation.

### Task: Image Attachments
Goal: Enable image uploads for posts.
Status: **COMPLETE**
Solutions:
- Implemented `media_utils` for safe file handling.
- Added file upload support to post submission form.
- Direct image rendering in timeline and profiles.

### Task: Permissions & Server Management
Goal: Role-based permissions and global server settings.
Status: **COMPLETE**
Solutions:
- Three-tier roles: Admin, Parent, Child.
- Content policy system with forbidden/warning tags.
- Detailed policy enforcement dashboard with violation logging.
- Global server configuration (Name, Description, Policies).

### Task: Family Calendar
Goal: Coordinate family events with external sync support.
Status: **IN PROGRESS**
Solutions:
- Created basic calendar UI and event models.
- Implemented local manual event creation.
- Added placeholders for Google/Outlook sync.
- Next: Implement Google OAuth2 and background sync logic.

### Task: Host/Client Architecture
Goal: Move beyond web browsers to a dedicated client/server model.
Status: **PLANNING**
Next Actions:
- Define API contract for the client.
- Select client-side technology (Python/PyQt, Node/Electron, or similar).
- Implement server-side enhancements for dedicated client connectivity (Auth tokens, etc).

---

## 12. Roadblocks & Fixes

- **Issue**: Login redirect failing due to missing Request object.
  - **Cause**: Template rendering required `request` context but it wasn't passed in error states.
  - **Fix**: Updated `login_submit` to correctly pass the request.

- **Issue**: Themes only applied to banners.
  - **Cause**: CSS rules were scoped only to `.profile-container`.
  - **Fix**: Moved theme logic to `base.html` using CSS variables on the `body` tag.

- **Issue**: Custom CSS rendering as text.
  - **Cause**: Jinja2 escaping and corrupted curly braces.
  - **Fix**: Used `| safe` filter and cleanly refactored the injection block.

---

## 15. Roadmap

### Phase 0 – Baseline (Current)
- [x] V2 Scaffold
- [x] Auth & Profiles
- [x] Feed & Dashboard
- [x] Immersive Themes

### Phase 1 – Federation
- [ ] Actor/WebFinger Complete
- [ ] Inbox/Outbox Delivery
- [ ] Remote Follow Logic

### Phase 2 – Media & Features
- [ ] Family Photos (Locked)
- [ ] File Vault (Locked)
- [x] Image Upload Implementation

### Phase 3 – Host/Client Model
- [ ] Dedicated Client Application (Electron/Python)
- [ ] API Token Authentication
- [ ] Local Node Auto-Discovery
- [ ] Native Push Notifications