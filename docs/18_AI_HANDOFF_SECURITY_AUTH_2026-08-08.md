# ATLAX Handoff Report

Date: 2026-08-08

This note captures the security and auth hardening work completed in this session so the next AI agent can continue safely.

## What We Fixed

### 1. Dashboard token storage risk
- Removed `localStorage` storage of the dashboard bearer token.
- Removed all dashboard-side manual `Authorization: Bearer ...` header injection.
- Switched the dashboard session to an HttpOnly cookie issued by the API.

### 2. Backend auth flow
- `POST /api/auth/login` now sets an auth cookie and returns user info only.
- Added `GET /api/auth/me` for session hydration.
- Added `POST /api/auth/logout` to clear the cookie.
- `verify_token()` now accepts either a bearer token or the auth cookie.
- Added `require_admin()` and applied it to all admin-only routes.

### 3. CORS hardening
- Replaced wildcard CORS with explicit allowed origins.
- Added `ATLAX_CORS_ORIGINS` support.
- Added a guard that rejects `*` in the origin list.

### 4. Secret handling
- Config writes now reject secret-bearing fields.
- Secret values were removed from tracked config and moved to environment-variable-only handling.
- Webhook secret loading is now env-only.

## Frontend Files Changed

- `dashboard/src/context/AuthContext.jsx`
  - Uses cookie-based session hydration.
  - Adds `ready` and `isAuthenticated` state.
  - Uses `axios.defaults.withCredentials = true`.
  - Includes a small session-version guard to avoid stale auth responses overwriting newer state.

- `dashboard/src/App.jsx`
  - `ProtectedRoute` now waits for auth hydration before redirecting.
  - Auth check is based on `isAuthenticated`, not a stored token.

- `dashboard/src/pages/Dashboard.jsx`
  - Heatmap polling no longer reads a token from browser storage.

- `dashboard/src/pages/Subscribers.jsx`
  - Removed all token lookup and manual bearer header code.

## Backend Files Changed

- `core/api/server.py`
  - Cookie-based auth support added.
  - Admin gating added.
  - Explicit CORS origins added.
  - Config secret-field validation added.

- `tests/unit/api/test_server_auth.py`
  - Updated to validate cookie auth instead of token return values.
  - Added coverage for `/api/auth/me`.
  - Added assertions that login response does not expose `access_token`.

## Verification

- Frontend build succeeded with `pnpm build` in `dashboard/`.
- Python syntax parse passed for:
  - `core/api/server.py`
  - `tests/unit/api/test_server_auth.py`

## Known Environment Limits

- `pytest` is not installed in the currently available Python runtime, so the test suite could not be executed directly here.
- A few broader repo tests were already known to have older constructor/signature assumptions in unrelated areas. Do not treat those as caused by this auth work without checking first.

## Guidance For The Next AI

1. Do not reintroduce browser-stored auth tokens.
2. Keep the dashboard on cookie-based session auth.
3. Keep admin routes protected with `require_admin`.
4. Keep CORS origins explicit.
5. Treat secrets as environment-only values.
6. If you extend auth or config behavior, update the matching tests in `tests/unit/api/test_server_auth.py`.

## Files To Read First

- `AGENTS.md`
- `docs/16_SECURITY.md`
- `docs/05_API_SPECIFICATION.md`
- `docs/13_CONFIGURATION.md`
- `docs/03_ARCHITECTURE.md`
- `core/api/server.py`
- `dashboard/src/context/AuthContext.jsx`
- `tests/unit/api/test_server_auth.py`

