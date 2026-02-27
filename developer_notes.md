# Developer Notes (HOMEPAGE)

## Question: What is Podman doing in this situation? Do we need it?

**Answer**: Currently, for local development, we are running the Python application **directly on your host machine** using a virtual environment (`.venv`). We are **NOT** using Podman right now for the development server.

Podman (like Docker) is useful for:
1.  **Production**: Packaging the app so it runs identically on any server (Linux/Windows/Mac).
2.  **Dependencies**: If we add complex services like Redis or PostgreSQL later, running them in Podman is easier than installing them on Windows directly.

**Decision**: Since we are in the "local dev" phase, we can continue running directly with Python (`uvicorn`). I will keep the `Containerfile` and `podman-compose.yml` updated in the background so you have the *option* to use them later, but they are not required for our current progress.

## Status Update (Phase 1, Step 3)
- **Auth Implementation**: Successfully implemented User Registration, Login (JWT), and Profile retrieval.
- **Fixes Applied**:
    -   Switched from `bcrypt` to `argon2` for password hashing to resolve compatibility issues.
    -   Fixed an import error in `auth_utils.py`.
    -   Verified the API works by running a test script against the local server (running on port 8001 to avoid conflicts).

## Next Steps
Proceeding to **Step 4: Content Posting**:
1.  Create the `Post` model (already done).
2.  Implement `create_post` logic (including signing).
3.  Create the `GET /` endpoint to render the timeline using Jinja2 templates.
