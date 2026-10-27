# SmartLib pilot deployment and mobile distribution

The repository contains deployment and standalone mobile build configuration.
The Expo project is now linked as `@wenzint20/evsu-smartlib-mobile`; its project ID
in `mobile-native/app.json` is public configuration and should be committed.
No hosted backend, signed APK, or TestFlight release has been verified yet. The
public download cards remain unavailable until real release links are set.

## Hosting choice

For this pilot, use a **paid Render web service with the persistent disk in
`render.yaml`**, in Singapore. It keeps this single-service deployment simple and
preserves the SQLite database and uploaded images. Render's free web filesystem
is ephemeral and free services sleep, so the free tier does not suit this pilot.
See [Render disks](https://render.com/docs/disks) and
[free-tier limits](https://render.com/docs/free).

Render is the selected hosting path. The older Railway instructions below are
retained as an alternative; they are not required for a Render deployment.
No paid resources are created by these files alone. Review Render's current
[pricing](https://render.com/pricing) before confirming the deployment: the paid
web compute and persistent disk are separate resources.

This pilot deliberately uses **one instance and one Uvicorn worker**: the rate
limiter and notification worker are process-local. Before scaling, move rate
limits to a shared store, make mail processing safe across workers, and move
uploads to private object storage. PostgreSQL is supported by `DATABASE_URL` and
the psycopg driver, but this change has not been integration-tested against a live
PostgreSQL service. Switching the URL does not migrate existing SQLite data.

## Protect credentials before deployment

1. Revoke the API key previously exposed in the chat and replace any SMTP
   credential previously committed in `.env.example`. Sanitizing a file cannot
   revoke keys or remove older Git commits. Configure replacements only in the
   host's secret/environment settings. Review provider activity and existing
   privileged accounts before reusing an old database.
2. Keep the real `.env` local. It is ignored by Git and excluded from Docker/EAS
   uploads. Production does not load `.env`; it reads hosting variables.
3. Uploaded images have been removed from the Git index and remain on the local
   disk. Existing Git history can still contain old images and credentials;
   restrict repository access and arrange a separate history cleanup if needed.
   Neither history nor existing user data has been erased by this change.
4. Use a new pilot database, or back up and review a restored database first.
   Production refuses active staff accounts using known demo passwords. Public
   registration can create only students/faculty. Review staff manually; the
   startup check cannot determine whether every existing privileged account is
   legitimate.

## Deploy on Render

For this setup you need a GitHub repository containing the latest backend files,
a Render account with billing enabled for a paid web service and persistent disk,
working SMTP credentials, and a new SuperAdmin identity/password. The optional AI
provider key can be added later. Your Expo project is already linked; no Apple
account is needed just to deploy the backend or build the Android pilot.

Use **New → Blueprint** for this project so Render reads the existing
`render.yaml`. It creates one Docker web service in Singapore, the 512 MB paid
compute plan (`0.5c-512mb`), a 1 GB disk at `/var/data`, the `/ready` health check,
and most environment settings automatically. You do not need to install Docker
on your Windows computer for this workflow.

Render's free web service cannot attach a persistent disk and blocks outbound
SMTP on ports 465/587, so it does not support this configuration as-is. Paid
services can use the configured port 587/STARTTLS provider (subject to that
provider's rules); port 25 is blocked for all Render services. See
[Render's free-service limits](https://render.com/docs/free) and
[persistent disks](https://render.com/docs/disks).

1. From `D:\Master`, review/commit/push the prepared changes to GitHub, including
   `render.yaml`, `Dockerfile`, `.dockerignore`, the new Python runtime modules,
   and updated `requirements.txt`. Several deployment files are currently new
   files and will not reach Render until committed. The real `.env` stays local.
   Complete the credential rotation steps above first.

   ```powershell
   cd D:\Master
   git add .
   git diff --cached --name-only
   git commit -m "Prepare SmartLib deployment on Render"
   git push origin main
   ```

   Check the filename list before committing; real `.env` files must not appear.
   Committing clean examples does not remove credentials from older history.
2. Sign into the [Render Dashboard](https://dashboard.render.com/) using GitHub,
   then choose **New → Blueprint**. Authorize access to this repository and click
   **Connect**. Select branch `main` and Blueprint path `render.yaml`.
3. Give the Blueprint a name such as `smartlib-pilot`. Review the listed Docker
   web service, Singapore region, paid compute and 1 GB disk. Confirm the price
   shown in Render. Keep the repository root; do not deploy `mobile-native` as
   the backend. The Dockerfile already supplies the build/start instructions.
4. Fill the fields Render asks for: `BOOTSTRAP_ADMIN_USERNAME`,
   `BOOTSTRAP_ADMIN_EMAIL`, `BOOTSTRAP_ADMIN_PASSWORD`, `SMTP_HOST`, `SMTP_FROM`,
   `SMTP_USERNAME` and `SMTP_PASSWORD`. Use your real email-provider settings and
   newly rotated credentials. The SuperAdmin password must follow the current
   app policy: 8–12 characters with uppercase, lowercase, a number and a symbol.
   The Blueprint sets port 587, STARTTLS on and SSL off; change these in the
   Blueprint if your provider requires different settings.
5. Click **Deploy Blueprint** (some dashboard versions label this **Apply**).
   Watch the service's **Events/Logs** until deployment is live. Render generates
   `SMARTLIB_SECRET_KEY`; keep it stable. The Blueprint obtains `PUBLIC_BASE_URL`
   from this service's `RENDER_EXTERNAL_URL`, so you do not need to guess the
   domain before the first deployment. If using a custom domain later, update
   the Blueprint mapping to preserve your chosen origin across syncs. See
   [Blueprint service references](https://render.com/docs/blueprint-spec).

The following table explains the settings. Most are already supplied by the
Blueprint; enter secrets in the Render form/Environment page, not in source files.

| Variable | Configuration |
|---|---|
| `SMARTLIB_ENV` | `production` (Blueprint default) |
| `PUBLIC_BASE_URL` | Automatically mapped to this service's `RENDER_EXTERNAL_URL` |
| `SMARTLIB_SECRET_KEY` | Random, at least 32 characters; Blueprint generates it |
| `DATABASE_URL` | `sqlite:////var/data/library.db` (Blueprint default) |
| `SMARTLIB_UPLOAD_DIR` | `/var/data/uploads` (Blueprint default) |
| `SMARTLIB_SEED_DEMO` | `false` |
| `REQUIRE_HTTPS` | `true` |
| `FORWARDED_ALLOW_IPS` | `*` only behind the managed platform's private ingress |
| `BOOTSTRAP_ADMIN_USERNAME` | New institutional staff ID for the first SuperAdmin |
| `BOOTSTRAP_ADMIN_EMAIL` | Actual staff email |
| `BOOTSTRAP_ADMIN_PASSWORD` | Unique 8–12 character password with uppercase, lowercase, number, symbol |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM` | Mail provider and verified sender; port usually `587` |
| `SMTP_USERNAME`, `SMTP_PASSWORD` | New provider credentials stored as secrets |
| `SMTP_STARTTLS`, `SMTP_USE_SSL` | Match the provider, usually `true` / `false` for port 587 |
| `CORS_ALLOWED_ORIGINS` | Leave blank for same-origin web pages and native apps |
| `OPENAI_API_KEY` | Optional replacement server-only key; local assistant works without it |

6. Open the service's assigned `https://…onrender.com` URL, then open `/ready`.
   The readiness endpoint should show `{"status":"ready"}`. Startup initializes
   an empty schema and creates just the configured
   SuperAdmin. Development demo accounts are never seeded in production. Sign
   in, provision librarians and configure departments/courses, then remove the
   three `BOOTSTRAP_ADMIN_*` variables. Keep the signing secret stable across
   restarts; changing it invalidates existing sessions and photo links.
   Your local SQLite catalog/accounts/uploads are not automatically migrated;
   the hosted service starts with its own database on the attached disk.
7. Confirm real registration/OTP email delivery, then check password recovery,
   borrowing, returns, staff notifications, message archives and exports using
   test accounts. `/ready` checks the database; it does not verify SMTP delivery.
   Platform health probes may use HTTP on the private network; user-facing
   traffic is redirected to HTTPS.
8. Set up backups before inviting testers. Back up both SQLite and uploads,
   retain copies outside the service, and rehearse a restore. Stop writes or use
   SQLite's backup API when copying an active database. A persistent volume alone
   is not a tested backup. Monitor disk usage and mail failures. An old database
   with duplicate active copy loans must be reconciled before the new unique
   index can be installed; startup does not silently discard such records.
9. In the linked Expo project, open **Environment variables** and set
   `EXPO_PUBLIC_API_BASE_URL` to the verified Render HTTPS origin for **preview**
   with plain-text visibility. Add it separately to **production** before store
   builds. Do not include `/docs`, `/ready` or an API subpath. Keep all server API
   keys, SMTP passwords and database credentials in Render. Continue with the
   mobile build section only after the hosted backend and mail delivery work.

If deployment fails, use the service logs to identify the failed step. A Docker
build/package-install error occurs before application startup; a production
configuration error usually names a missing/invalid variable without showing its
value. A failed `/ready` after startup needs database/storage investigation.
An email-provider error needs SMTP settings/provider review; `/ready` alone does
not verify email. Keep credentials out of any logs or screenshots shared for help.

## Railway alternative

Use this order: **review/commit/push the source → deploy the backend on Railway →
configure the backend URL in Expo → build the standalone mobile apps**.

Before pushing, revoke/replace the previously exposed API and SMTP credentials.
The working `.env.example` has been sanitized, but older Git commits still contain
credential values. Committing the clean version does not erase that history.
Review the repository's history and GitHub secret-scanning alerts before making
the repository public. Keep `.env` local; commit the clean examples and ignore
rules along with the application changes. Never force-add ignored secret files.

Railway's **Free, Trial and Hobby plans block SMTP**. This application's current
email transport is SMTP, so verification codes, recovery codes and email alerts
need either Railway Pro (or higher), or an implementation of an HTTPS email
provider such as Resend. An HTTP provider has not been implemented by this
deployment setup. The API passing `/ready` does not prove email delivery works.
See [Railway outbound email restrictions](https://docs.railway.com/networking/outbound-networking).

1. In Railway, create a project from **GitHub Repo** and select this repository
   and the `main` branch. Keep the service root at the repository root `/`, where
   `Dockerfile` and `railway.json` reside. The `mobile-native` folder is not the
   backend deployment root. The Dockerfile already supplies the start command.
2. Attach a persistent volume at `/var/data`. Keep one replica. Without this
   volume, the SQLite database and uploads will not survive redeployment.
3. In the service's **Variables**, enter the production settings from the table
   above. For the SQLite pilot, the non-secret base settings are:

   ```dotenv
   SMARTLIB_ENV=production
   DATABASE_URL=sqlite:////var/data/library.db
   SMARTLIB_UPLOAD_DIR=/var/data/uploads
   SMARTLIB_SEED_DEMO=false
   REQUIRE_HTTPS=true
   FORWARDED_ALLOW_IPS=*
   RAILWAY_RUN_UID=0
   ```

   Also configure a new random `SMARTLIB_SECRET_KEY`, the three
   `BOOTSTRAP_ADMIN_*` values, and your working mail-provider credentials as
   Railway variables. Do not copy old exposed credentials from Git history.
4. Under **Settings → Networking**, generate the public domain and set
   `PUBLIC_BASE_URL` to its full HTTPS origin. Deploy/redeploy after the domain
   and required variables are configured. An automatic initial deployment can
   fail the configuration checks while these values are missing; it is not a
   reason to disable the production guards.
5. Open the generated HTTPS URL and `/ready`. Then test sign-in, real OTP email
   delivery and a borrow/return using pilot accounts. Configure backups before
   inviting testers. The new volume starts with an empty production database;
   the local development catalog and accounts are not automatically transferred.
6. In the linked Expo project's **Environment variables**, set
   `EXPO_PUBLIC_API_BASE_URL` to that same HTTPS origin for **preview** (and later
   **production**) with plain-text visibility. This URL is public configuration;
   server credentials stay in Railway. Only then proceed to the mobile builds
   below. See [Expo environment variables](https://docs.expo.dev/eas/environment-variables/manage/).

Railway volumes initially belong to root. `RAILWAY_RUN_UID=0` lets the container
initializer assign the known volume directories and immediately drop to UID
10001 before starting the API. The API itself does not run as root. The volume
and secret values are not provisioned by `railway.json`.
See [Railway volumes](https://docs.railway.com/volumes) and
[public networking](https://docs.railway.com/networking/public-networking).

For generic Docker hosting, trust forwarded headers only from the actual reverse
proxy; do not expose a container configured with `FORWARDED_ALLOW_IPS=*` directly
to the internet. Persistent directories must be writable by UID 10001.

## Build installable Android and iPhone apps

The pilot installs the app itself. Testers do not need Expo Go or a development
server. EAS is used to build/sign the binaries. See
[Expo build profiles](https://docs.expo.dev/build/eas-json/) and
[TestFlight distribution](https://docs.expo.dev/submit/testflight/).

1. Create an Expo account and enroll in the Apple Developer Program for iPhone
   distribution. Set up an App Store Connect app with bundle identifier
   `ph.edu.evsu.smartlib`. Confirm the institution owns both accounts and signing
   credentials. Do not paste account passwords or private signing files into Git.
2. The Expo login and project association were completed on 2026-10-27. Keep the
   resulting `app.json` changes. For a fresh checkout, install the locked
   dependencies and sign in; run `init` only if the project is not already linked.

```powershell
npm.cmd ci
npx.cmd eas-cli@latest login
npx.cmd eas-cli@latest init
```

3. In the Expo dashboard, add `EXPO_PUBLIC_API_BASE_URL` to the **preview**
   environment with the real deployed HTTPS origin. Use plain-text visibility
   for this public configuration. Add it separately to **production** before
   store builds. Set the same value in your local shell when invoking the build:

```powershell
$env:EXPO_PUBLIC_API_BASE_URL = 'https://YOUR-DEPLOYED-DOMAIN'
npx.cmd eas-cli@latest build --platform android --profile pilot-android
npx.cmd eas-cli@latest build --platform ios --profile pilot-ios
npx.cmd eas-cli@latest submit --platform ios --profile pilot-ios --latest
```

Replace the domain before executing. The build configuration rejects HTTP and
local network addresses. Only the API origin belongs in `EXPO_PUBLIC_*` values;
they are visible in the installed app. SMTP, AI, database and signing secrets
must never be included. Keep the API domain stable to avoid rebuilding clients.

4. Let EAS create/manage Android signing credentials and Apple distribution
   credentials when prompted. Keep the Android key so subsequent APKs can update
   existing installations. The Android profile produces an APK, while iOS uses
   a store-signed build for TestFlight, rather than an ad hoc device list.
5. Install the APK on a physical Android phone. For iPhone, configure an external
   TestFlight group and public invite link after Apple processes/reviews the
   build. Apple controls approval; no TestFlight public link can be created in
   this repository. See [Apple TestFlight](https://developer.apple.com/testflight/).
6. Test both physical platforms with the deployed service, including logout on
   a shared phone, offline errors, permissions, file sharing and notifications.
   Successful JS export is not a signed native build or device test. The current
   app uses in-app activity notifications and local due reminders; background
   server push is a separate feature, not supplied by these build profiles.

## Enable the landing-page download links

After the actual builds are available, set these **backend** hosting variables:

| Variable | Value |
|---|---|
| `ANDROID_APK_URL` | Stable public HTTPS download URL of the signed APK (EAS artifact or approved release storage) |
| `IOS_TESTFLIGHT_URL` | Actual `https://testflight.apple.com/join/...` public invite |
| `MOBILE_APP_VERSION` | Display version, initially `1.0.0` |

Restart/redeploy the backend. `/public/app-releases` exposes only this public
metadata; it does not expose other environment settings. The landing page's
**Get the App** section enables each platform independently. Invalid/missing
links leave that platform in its “coming soon” state. Changing a link does not
build an app, upload a binary, or submit it to Apple.

## Full launch follow-up

This change addresses major deployment blockers, not every finding in
`SECURITY-AUDIT-2026-10-19.md`. Before a full launch, repeat dependency audits,
resolve outstanding advisories, load-test circulation and report generation,
review data retention/access policies, and test recovery and monitoring on the
chosen host. The existing inline-script CSP and browser token storage also merit
a separate hardening pass. Native signing, hosted Docker execution, actual mail
delivery and live PostgreSQL have not been verified in this local environment.

Local validation on 2026-10-26: 188 Python checks and 44 JavaScript tests passed
(the Python suite and two subsequent middleware checks), including registration
roles, reset-grant revocation, private photos, startup guards and native session
teardown. A production startup smoke test passed against an isolated persistent
SQLite database. The landing page was checked at desktop/phone sizes with absent,
published and invalid release links, and both Android/iPhone JavaScript bundles
exported. These checks do not replace a Docker deployment or signed device test.
