# Implementation Plan - HOMEPAGE V2 (Python/FastAPI)

**Goal**: Build a Minimum Viable Product (MVP) of the HOMEPAGE platform — a federated local internet node — using Python and FastAPI.

## Phase 1: Foundation & Local Node

### Step 1: Project Scaffolding
- [ ] Initialize Python project (FastAPI, Uvicorn, SQLAlchemy/Tortoise)
- [ ] Set up `pyproject.toml` / `requirements.txt`
- [ ] Create directory structure (`app/`, `tests/`, `media/`)
- [ ] configure `podman-compose.yml` for dev

### Step 2: Database & Models (User Core)
- [ ] Design `User` model (username, password_hash, public_key, private_key_encrypted)
- [ ] Design `ServerConfig` model (domain, policy preferences)
- [ ] Set up synchronous (migrations) and asynchronous (runtime) DB connection
- [ ] Create migration script (Alembic or Tortoise-Aerich)

### Step 3: Authentication & Identity
- [x] `POST /auth/register`: Create local user
- [x] `POST /auth/login`: Issue Session/JWT
- [x] Generate Ed25519 identity keypair on registration
- [x] Middleware for current_user dependency
- **Note**: Switched to `argon2` for password hashing (better security + compatibility).

### Step 4: Content Posting (Local)
- [x] `Post` model (content, media_path, signature)
- [x] `POST /api/posts`: Create signed post
- [x] `GET /`: Render local timeline (Jinja2)
- [x] Basic markdown rendering (via Jinja2 autoescape=False or localized lib)

### Step 5: Media Handling
- [x] File upload endpoint
- [x] Constraint checks (max size, type)
- [x] Serve media via static file mount (dev) or Nginx/Caddy (prod)

---

---

## Phase 2: Federation (ActivityPub)

### Step 1: WebFinger & Actor Profile
- [x] Update `config.py` with `DOMAIN` and `PROTOCOL`
- [x] `/.well-known/webfinger`: Resolves `acct:user@domain` -> Actor URL
- [x] `/users/{username}`: Serves Actor object (JSON-LD)
- [x] `Person` object structure (public key, inbox, outbox)

### Step 2: HTTP Signatures & Security
- [ ] Generate RSA-2048 keys for users (ActivityPub standard) 
  - *Note: migrating from or strictly using Ed25519 requires care, AP usually prefers RSA.*
- [ ] Middleware to verify incoming HTTP Signatures
- [ ] Utilities to sign outgoing requests

### Step 3: Inbox & Outbox
- [ ] `POST /users/{username}/inbox`: Receive activities (Follow, Create, Like)
- [ ] `GET /users/{username}/outbox`: Public activity history
- [ ] Activity processing queue (BackgroundTasks)

### Step 4: Following & Discovery
- [ ] `Follow` activity handler (Accept/Reject)
- [ ] Resolve remote actors (WebFinger lookup)
- [ ] Store remote followers in DB
