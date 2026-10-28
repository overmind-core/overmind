# Auth, tenancy and billing

## Auth

Clerk when `CLERK_API_SECRET_KEY` is set. A blank secret means self-hosted local JWT: `POST /api/auth/local/`, email and password, create on first use, no verification. The Console uses the local form when `VITE_SELF_HOSTED=true` and `VITE_CLERK_PUBLISHABLE_KEY` is blank. ChatGPT login, account linking and stored provider credentials are removed.

Tenancy is project-scoped (`User` ↔ `Project` through `ProjectMembership`), with no org layer. Every queryable model is filtered by project.

- **Guests** (`User.is_guest`, `POST /api/auth/guest/`) hold one project and can read and claim. `GuestJWTAuthentication` refuses other writes with `guest_upgrade_required`. A view that accepts guest writes opts in with `guest_allowed = True`, never with a permission class or middleware: a view's own `permission_classes` replaces the defaults. A claim (Clerk only) moves memberships and deactivates the guest; `tasks/guest_cleanup.py` deletes inactive and unclaimed guests after 7 days.
- **Invites**: with Clerk an email is sent; without Clerk the `ProjectInvite` row is claimed on the first sign-in for that email.

## API keys

`scope=account` (every project the user belongs to) or `scope=project` (one `resourceIds` entry). REST and MCP `create_api_key` share `APIToken.create_for_user` and the Console dialog's fields: name, description, expiry of 30, 90, 180 or 365 days, or 0 for never. MCP omits `project_id` for an account key. The plaintext is returned once. A project credential cannot mint an account key; guests cannot mint keys. MCP OAuth credentials work only on MCP (mcp skill).

## Billing

- Ledger charges always run. Remaining-credit 402s, the Free seat limit and Stripe Checkout apply only when `STRIPE_SECRET_KEY` is set (`services/billing_provider.py`); otherwise spend is recorded and shown, and gates and grants are no-ops.
- Native-agent model choice and funding belong to the coding-agent environment. Training, evaluation and serving keep their own billing.
- Compute estimates use live provider rates with fetch timestamps (`services/compute_costs.py`: Modal workspace rates, five-minute cache, no static fallback). They never create ledger charges. One quote uses one rate snapshot.
- Modal training charges use deduplicated worker usage receipts and recorded hardware, never the delay before the collector notices completion. Missing usage stays unmeasured; historical ledger entries are not rewritten.

## Sales endpoints

`GET /api/internal/sales/{overview,funnel,accounts,accounts/<id>}/` are cross-tenant metrics behind one static bearer token, `SALES_DASHBOARD_TOKEN`. `overbae/urls.py` registers them only when the token is set at import (`tests/settings.py` sets one). Each path and query is computed at most once an hour through the Django cache, and responses carry `computed_at`/`refreshes_at`. They skip user authentication, stay out of MCP and the OpenAPI schema, and exclude guests and guest-only projects (`services/sales_metrics.py`).

`sales-dashboard/` is their standalone Vite app. It imports the Console's `styles.css`, `public/` and `components/ui` through the `@` alias, so keep those primitives free of app-level imports. It stores the token in localStorage, and `bun run dev` proxies `/api` to `SALES_API_PROXY` (default `http://localhost:8000`).
