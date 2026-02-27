# Task: HOMEPAGE V2 Implementation

- [x] **Step 1: Project Scaffolding**
- [x] **Step 2: Database & Models**
- [x] **Step 3: Authentication & Identity**
- [/] **Step 4: Content Posting (Local)**
    - [x] Add signing functions to `auth_utils.py`
    - [x] Create `app/crud_posts.py`
    - [x] Create `app/routers/posts.py`
    - [x] Create Jinja2 templates (`base.html`, `index.html`)
    - [x] Update `main.py`
- [x] **Step 5: Media Handling** <!-- id: 9 -->

## Phase 2: Federation (ActivityPub)
- [x] **Step 1: WebFinger & Actor Profile** <!-- id: 10 -->
    - [x] `/.well-known/webfinger` endpoint <!-- id: 11 -->
    - [x] `/users/{username}` (Actor JSON-LD) <!-- id: 12 -->
- [x] **Step 2: Security (HTTP Signatures)** <!-- id: 17 -->
    - [x] RSA Key Generation <!-- id: 18 -->
    - [x] HTTP Signature Verification Middleware/Utils <!-- id: 19 -->
- [x] **Step 3: Inbox & Outbox (Full Handling)** <!-- id: 13 -->
    - [x] Create `POST /users/{username}/inbox` <!-- id: 14 -->
    - [x] Handle `Follow` requests <!-- id: 15 -->
    - [x] Handle `Create` (Note/Post) activities <!-- id: 16 -->
    - [x] Create `GET /users/{username}/outbox` <!-- id: 20 -->
- [x] **Step 4: Activity Delivery (Outbound)** <!-- id: 21 -->
    - [x] Sign and POST activities to followers <!-- id: 22 -->
    - [x] Background task worker <!-- id: 23 -->
- [x] **Step 5: Follower Management & Accept** <!-- id: 24 -->
    - [x] Send `Accept` activity on follow <!-- id: 25 -->
    - [x] Manage remote follower state <!-- id: 26 -->

## Phase 3: Community & Discovery (Complete) <!-- id: 27 -->
- [x] **Step 1: Community UI & Discovery** <!-- id: 28 -->
    - [x] WebFinger & Actor Lookup UI <!-- id: 29 -->
    - [x] Implement Outbound Follow Requests <!-- id: 30 -->
    - [x] Modernize UI with CSS and HTMX <!-- id: 31 -->

## Phase 4: Persistence & Polishing <!-- id: 32 -->
- [x] **Step 1: Database Migrations (Alembic)** <!-- id: 33 -->
    - [x] Initialized Alembic <!-- id: 40 -->
    - [x] Created Initial Migration representing current models <!-- id: 41 -->
    - [x] Stamped current database state <!-- id: 42 -->
- [x] **Step 2: Profile Settings & Customization** <!-- id: 34 -->
    - [x] Extend User model with Bio & Avatar <!-- id: 37 -->
    - [x] Create Profile & Settings pages <!-- id: 38 -->
    - [x] Integrate Bio/Icon into ActivityPub Actor profile <!-- id: 39 -->
- [x] **Step 3: User Registration & Onboarding** <!-- id: 46 -->
    - [x] Registration Form UI <!-- id: 47 -->
    - [x] Form submission & Key generation integration <!-- id: 48 -->
- [x] **Step 4: Family-Safe Controls (Filtering)** <!-- id: 35 -->
    - [x] Implement Keyword-based content filter <!-- id: 49 -->
    - [x] Integrate filter into local post creation <!-- id: 50 -->
    - [x] Integrate filter into federated post inbox <!-- id: 51 -->
- [x] **Step 5: Local Network Access** <!-- id: 43 -->
    - [x] Configure DOMAIN to local IP (`192.168.1.5`) <!-- id: 44 -->
    - [ ] Update server to listen on `0.0.0.0` <!-- id: 45 -->
- [ ] **Step 6: Final Polishing & Mobile Responsiveness** <!-- id: 36 -->
