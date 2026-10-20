# Security audit — 19 October 2026

Scope: all application projects found under `D:\Master`: FastAPI/SQLite backend, public website, Admin and Librarian dashboards, shared JavaScript, installable mobile web app, Expo/React Native app, dependency manifests/installed versions, relevant configuration and Git history. This includes the current uncommitted features.

**Result: 14 application/configuration findings, including two Critical findings, plus five distinct dependency advisories.** Severity reflects this application and the prerequisites below. HTTP exposure and avatar privacy require deployment/policy context. This is a source/local-state audit, not a guarantee that every vulnerability has been found.

No application source fixes were applied. No real account passwords were changed, no production records were deleted or updated, no real OTP/email was sent, and no SMTP credential was tested against its provider. Secret values and user identities are omitted. Reproductions used fabricated data and isolated SQLite databases; the existing database was inspected read-only for default-password matches. Browser/device exploit execution and external infrastructure testing were not performed.

| ID | Severity | Finding |
|---|---|---|
| F01 | Critical | Public registration can create privileged accounts |
| F02 | Critical | Active privileged accounts retain documented default passwords |
| F03 | High | Configured SMTP credential is committed in Git history |
| F04 | High | Stored XSS in staff inventory book condition |
| F05 | High | Stored attribute XSS in the public catalog |
| F06 | High, deployment-dependent | Current configuration permits credential/token transport over HTTP |
| F07 | Medium | Password-reset grants survive a legitimate password change |
| F08 | Medium | Native logout retains private data and password drafts |
| F09 | Medium, privacy-policy dependent | Profile photos are anonymously retrievable and tracked in Git |
| F10 | Medium | A patron can persist data that breaks staff PDF/Word exports |
| F11 | Medium | Missing submission/storage quotas allow resource exhaustion |
| F12 | Medium | Concurrent checkouts can issue one physical copy twice |
| F13 | Medium | Alternate checkout paths bypass circulation eligibility checks |
| F14 | Low | Inventory/circulation audit logs misidentify the acting staff member |

## F01 — Public registration can create privileged accounts

**Locations:** [schemas.py:33](D:/Master/schemas.py:33), [auth_router.py:242](D:/Master/routers/auth_router.py:242), [auth_router.py:288](D:/Master/routers/auth_router.py:288).

`RegisterRequest.account_type` accepts the entire `UserRole` enum. Registration copies it into the pending record and then into an active, email-verified user. No server-side student/faculty restriction or privileged-role approval occurs.

**Impact/prerequisites:** An unauthenticated person who can receive a registration OTP and select an available department/course can become a superadmin, administer accounts, and provision staff.

**Validation:** An isolated TestClient flow, with SMTP mocked, returned registration 202, verification 201, login 200, a superadmin role, and access to the protected user-management endpoint. No production account was created.

**Fix:** Restrict public registration to an explicit allowlist of patron roles at both schema and endpoint/service boundaries. Assign privileged roles only through authenticated administration. Review pending registrations and existing privileged accounts for abuse.

## F02 — Active privileged accounts retain default passwords

**Locations:** [crud.py:963](D:/Master/crud.py:963), [main.py:81](D:/Master/main.py:81), [schemas.py:123](D:/Master/schemas.py:123), [crud.py:148](D:/Master/crud.py:148), [admin-dashboard.html:732](D:/Master/admin-dashboard.html:732).

Startup seeds accounts with fixed passwords documented in the repository. Read-only verification found an active, verified superadmin and librarian whose hashes still match those defaults, plus an active student default account. Staff provisioning also uses a prefilled shared password and accepts an unconstrained password string without mandatory first-login replacement.

**Impact/prerequisites:** Anyone who can reach authentication and knows the documented credentials can take over privileged accounts without exploiting F01.

**Validation:** Read-only password-hash comparisons; no real login, user identity, or password value is included here.

**Fix:** Immediately replace default account passwords and revoke their sessions; disable unused demonstration accounts. Provision through one-time invitations or generated credentials with forced replacement. Disable demo seeding in production and apply the same password policy to staff creation.

## F03 — SMTP credential committed in Git

**Location:** [.env.example:13](D:/Master/.env.example:13).

The tracked example contains nonplaceholder SMTP credential material that exactly matches the currently configured local `SMTP_PASSWORD`. The same material occurs in four historical commits, including current HEAD.

**Impact/prerequisites:** Repository readers, forks, backups, and build artifacts may expose the mail credential. Provider-side validity and the provider permissions were not tested.

**Validation:** Redacted equality and history checks, with no outbound authentication. Matching commit IDs: `6a0f45ebe3048f5161ecad95c51e63885562a124`, `c6cc2675df8f724901274820e9539ff59afc2e99`, `9a119ad24c3a08d3028fd7c871454494c6207828`, `76e0223766f8791a43c545cde55fdbbfc9f444c2`.

**Fix:** Revoke/rotate the credential at the mail provider first. Replace the example with a placeholder, then coordinate repository-history/artifact cleanup and enable secret scanning. Deleting the current value alone does not revoke historical copies.

## F04 — Stored XSS in staff book condition

**Locations:** [admin-dashboard.html:1500](D:/Master/admin-dashboard.html:1500), [librarian-dashboard.html:1218](D:/Master/librarian-dashboard.html:1218), [schemas.py:239](D:/Master/schemas.py:239), [admin_router.py:87](D:/Master/routers/admin_router.py:87).

The book `condition` is inserted directly into `innerHTML`. An authenticated librarian can write arbitrary condition text through the book-update API. Another staff member opening inventory renders that stored content as markup.

**Impact/prerequisites:** A librarian can execute script in an administrator's origin/session and call privileged APIs. Browser tokens are JavaScript-readable; the CSP permits inline handlers at [main.py:131](D:/Master/main.py:131).

**Validation:** Synthetic marker input passed schema validation; isolated parsing of the actual rendering pattern preserved an executable event-handler attribute. No real browser session was attacked.

**Fix:** Render condition with `textContent` or complete HTML encoding, and enforce a server-side enum of supported conditions. Remove inline script allowances after replacing existing inline handlers.

## F05 — Stored attribute XSS in public catalog

**Locations:** [index.html:2364](D:/Master/index.html:2364), [index.html:2411](D:/Master/index.html:2411).

The public `esc()` helper encodes text through a DOM element's `textContent/innerHTML`; it does not encode quote characters for attribute contexts. That output is interpolated inside a quoted image `alt` attribute using the stored book title.

**Impact/prerequisites:** A librarian able to edit a title and provide a valid cover can cause catalog visitors to render attacker-controlled event-handler markup. Staff/PWA tokens stored on the same origin can also be at risk.

**Validation:** Synthetic input accepted by the book schema produced a separate executable attribute in isolated HTML parsing. No public page was modified.

**Fix:** Create image nodes with DOM APIs and assign `alt`/`src` properties. Alternatively use an encoder that handles both quote characters in attributes. Audit other attribute uses of the same helper.

## F06 — HTTP credential transport remains permitted

**Locations:** [main.py:118](D:/Master/main.py:118), [mobile-native/App.js:53](D:/Master/mobile-native/App.js:53), [mobile-native/app.config.js:2](D:/Master/mobile-native/app.config.js:2).

Redacted local configuration checks show development mode, HTTPS enforcement disabled, and a non-HTTPS public base URL. The supplied dashboard context used HTTP. The native app accepts HTTP API bases; Android cleartext remains enabled unless the build environment explicitly selects production.

**Impact/prerequisites:** A network attacker in a directly exposed HTTP deployment can observe or alter passwords, bearer tokens, OTPs, and application data. A correctly configured TLS reverse proxy could mitigate transport for its route; deployed proxy behavior was not assessed.

**Fix:** Enforce HTTPS end to end for deployed clients, require production build configuration, and reject HTTP API addresses in release builds. Protect verification/recovery links with HTTPS and restrict trusted proxy headers. Do not expose development servers to untrusted networks.

## F07 — Reset grants survive password changes

**Locations:** [auth_router.py:409](D:/Master/routers/auth_router.py:409), [auth_router.py:519](D:/Master/routers/auth_router.py:519).

Password changes revoke sessions, but other unconsumed password-reset grants remain valid. Reset completion consumes only the particular submitted grant.

**Impact/prerequisites:** Someone retaining a previously verified reset grant can reset the password again after the legitimate owner changes it, within the grant's remaining lifetime (10 minutes for the verified mobile grant).

**Validation:** In an isolated flow, a reset grant was issued, a legitimate password change succeeded, and the old grant still completed a reset followed by successful login. SMTP was mocked.

**Fix:** Invalidate all outstanding reset OTPs/grants when a password changes or resets, and bind grants to a credential version. Make grant consumption and password update atomic.

## F08 — Native logout leaves private state behind

**Locations:** [App.js:146](D:/Master/mobile-native/App.js:146), [App.js:162](D:/Master/mobile-native/App.js:162), [App.js:439](D:/Master/mobile-native/App.js:439), [App.js:545](D:/Master/mobile-native/App.js:545), [App.js:915](D:/Master/mobile-native/App.js:915).

Logout clears secure-storage tokens and a few identity fields but retains assistant history, password-change drafts, modal flags, cached conversations, and account-specific notifications. The next account can render retained assistant messages, and its next assistant request forwards the previous user's history. Password drafts are cleared only after a successful change.

**Impact/prerequisites:** On a shared device without restarting the app, the next user may read prior private history or reveal retained password drafts using the form's show controls.

**Validation:** Source/data-flow confirmation. No device exploitation or real password retrieval was performed.

**Fix:** Centralize session teardown for logout, inactivity, 401, password changes, and account switches. Reset all private state/drafts/modals, cancel reminders, and discard late responses from earlier sessions.

## F09 — Public and repository exposure of profile photos

**Locations:** [main.py:163](D:/Master/main.py:163), [auth_router.py:484](D:/Master/routers/auth_router.py:484), tracked files under `uploads/profile-photos/`.

Profile-photo files are served by a public static mount without authentication. At least one uploaded profile photo is tracked in Git, making its otherwise random URL discoverable from repository contents.

**Impact/prerequisites:** Anyone possessing a URL or repository copy can retrieve the image without a session. If avatars are intentionally public, treat anonymous serving as a documented privacy/design decision; the repository retention of uploaded personal files still needs review.

**Validation:** A local TestClient request with no Authorization header returned 200 and an image response. No image content or identity is reproduced here.

**Fix:** Serve private photos through an authenticated owner/staff endpoint or short-lived signed URLs. Stop tracking uploaded personal files and review retained history/backups under the application's privacy policy.

## F10 — Stored suggestions can break staff exports

**Locations:** [schemas.py:324](D:/Master/schemas.py:324), [mobile_router.py:106](D:/Master/routers/mobile_router.py:106), [report_router.py:97](D:/Master/routers/report_router.py:97), [report_router.py:104](D:/Master/routers/report_router.py:104).

Acquisition text has no size/control-character validation. Word cell assignment rejects XML-incompatible characters, while a sufficiently long table cell exceeds ReportLab's page layout capacity.

**Impact/prerequisites:** An authenticated patron can persist one malformed suggestion that makes matching staff Word/PDF queue exports fail until it is excluded or archived.

**Validation:** Fabricated persisted suggestions caused Word `ValueError` for a NUL character and PDF `LayoutError` for a 24,000-character justification in isolated tests.

**Fix:** Validate text length and control characters at ingestion; safely handle existing data. Support splitting/truncation of long export cells, and return controlled errors that identify problematic records without disabling all exports.

## F11 — Unbounded submissions and upload retention

**Locations:** [main.py:188](D:/Master/main.py:188), [main.py:193](D:/Master/main.py:193), [schemas.py:267](D:/Master/schemas.py:267), [schemas.py:324](D:/Master/schemas.py:324), [mobile_router.py:59](D:/Master/routers/mobile_router.py:59), [auth_router.py:484](D:/Master/routers/auth_router.py:484), [admin_router.py:374](D:/Master/routers/admin_router.py:374), [report_router.py:74](D:/Master/routers/report_router.py:74).

Public feedback accepts persistent unauthenticated writes without route throttling. Acquisition/borrow submissions lack rate limits and text bounds. Acquisition lists and queue exports load all matching records. Public catalog pagination has no upper/lower bounds (`limit=-1` removes SQLite's limit). Profile-photo uploads retain old files; a single patron can repeatedly add files up to the 5 MB per-file limit without a cumulative quota.

**Impact/prerequisites:** Anonymous feedback abuse or authenticated submission/upload abuse can grow the database/filesystem and increase staff-report memory/CPU use. Reverse-proxy limits may reduce exposure but no such configuration is supplied.

**Validation:** A 2,000,000-character acquisition justification passes schema validation; source confirms unbounded write/list paths and file retention. No flooding, disk filling, or production load testing was performed.

**Fix:** Add shared per-IP/account throttles, bounded fields/request sizes, queue pagination, per-user storage quotas, and safe cleanup of replaced photos. Bound public pagination with explicit validated limits.

## F12 — Non-atomic checkout can duplicate a physical loan

**Locations:** [crud.py:233](D:/Master/crud.py:233), [crud.py:457](D:/Master/crud.py:457), [crud.py:679](D:/Master/crud.py:679), [models.py:168](D:/Master/models.py:168).

Copy selection and inventory updates use separate reads followed by ordinary object assignments. No atomic claim or database constraint prevents two transactions from assigning the same copy to separate active loans.

**Impact/prerequisites:** Two simultaneous staff checkouts can corrupt availability and associate one physical copy with two borrowers.

**Validation:** Two synchronized sessions against an isolated temporary SQLite database both checked out the sole copy: two active loans, one distinct copy tag, and zero remaining available copies.

**Fix:** Transactionally claim a specific copy with conditional writes and checked affected-row counts; enforce one active loan per copy. Use shared atomic circulation transitions for checkouts and returns.

## F13 — Alternate checkout paths skip eligibility rules

**Locations:** [crud.py:853](D:/Master/crud.py:853), [crud.py:457](D:/Master/crud.py:457), [admin_router.py:238](D:/Master/routers/admin_router.py:238), [schemas.py:283](D:/Master/schemas.py:283).

The deployed API accepts a legacy `borrow_clear` mode that bypasses normal fines, loan-limit, and archived-book checks. Approved-request checkout also omits patron suspension/fines/loan-limit checks.

**Impact/prerequisites:** A staff-authenticated client can issue books that normal circulation would reject. If staff overrides are intended, the exception needs explicit permissions, a reason, and an audit trail rather than an alternate unchecked path.

**Validation:** An isolated student with unpaid fines and three active loans was rejected by ordinary borrowing but successfully received an archived book as a fourth loan through the alternate mode. A separate online checkout succeeded for a suspended, indebted, over-limit patron.

**Fix:** Route every checkout through the same eligibility/atomic inventory service. Remove legacy modes from deployed schemas; implement intentional overrides explicitly.

## F14 — Audit logs do not reliably record the actor

**Locations:** [admin_router.py:244](D:/Master/routers/admin_router.py:244), [crud.py:729](D:/Master/crud.py:729), [crud.py:826](D:/Master/crud.py:826), [crud.py:359](D:/Master/crud.py:359).

Circulation discards the authenticated actor when calling its service. Several mutations log the affected patron or a generic role such as Librarian instead of the actual staff account.

**Impact:** Investigators cannot reliably attribute inventory/circulation changes, weakening detection and accountability for staff misuse.

**Fix:** Pass actor identity into mutation services and record actor and affected patron separately in the same transaction.

## Dependency advisories

The native lockfile audit reported **24 affected dependency entries: 16 High and 8 Moderate**. These are propagated dependency-tree entries arising from **three distinct advisory causes**, not 24 separate exploitable application bugs. The backend audit reported three occurrences in two installed packages; the ECDSA occurrence is duplicated, leaving two distinct Python advisories.

| Package and installed/locked version | Advisory severity | Advisory | Application reachability and action |
|---|---|---|---|
| braces 3.0.3 | High | [GHSA-vfj7-8cjw-p6xm / CVE-2026-93687](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm) | Stack exhaustion from nested patterns; present through Metro/micromatch build tooling. No untrusted deployed-app pattern path was established. Advisory lists no patched version at scan time; isolate dev/build tooling and track upstream remediation. |
| node-forge 1.4.0 | High | [GHSA-86w9-cpqp-85rv / CVE-2026-85393](https://github.com/advisories/GHSA-86w9-cpqp-85rv) | Signature-verification weakness through Expo CLI/code-signing dependencies. No direct app use found. Advisory lists no patched version at scan time; review signing/tooling exposure and upstream updates. |
| uuid 7.0.3 | Moderate in npm audit | [GHSA-w5hq-g745-h8pq / CVE-2026-41907](https://github.com/advisories/GHSA-w5hq-g745-h8pq) | Bounds checks in certain UUID functions; fixed version listed is 11.1.1. The inspected xcode caller uses v4, whereas this advisory describes v3/v5/v6 with caller buffers, so an affected application path was not established. |
| python-jose 3.5.0 | Critical advisory; not demonstrated exploitable here | [GHSA-3qf3-8w2g-rqmx / CVE-2026-85394](https://github.com/advisories/GHSA-3qf3-8w2g-rqmx) | Current [security.py:22](D:/Master/security.py:22) and [security.py:64](D:/Master/security.py:64) use HS256 with an explicit algorithm allowlist and a symmetric secret. The advisory's asymmetric-public-key confusion prerequisite is not present. Track a patch/migration rather than treating this as a confirmed JWT bypass in this app. |
| ecdsa 0.19.2 | High advisory; not demonstrated exploitable here | [GHSA-wj6h-64fc-37mp / CVE-2024-23342](https://github.com/advisories/GHSA-wj6h-64fc-37mp) | Timing exposure affects ECDSA signing/key generation; current app uses HMAC JWTs and no ECDSA signing path was found. No patched version is listed. Remove unused crypto dependencies or migrate the JWT stack after compatibility tests. |

Do not blindly apply `npm audit fix --force`: this audit's proposed Expo/React Native downgrades are major incompatible changes. Update/replace dependencies through a tested compatible platform release. Commit and maintain a lockfile; the current `.gitignore` ignores `package-lock.json`, and Python requirements use lower bounds without an application lock, so fresh installations are not reproducible.

All affected npm dependency entries from the audit (severity may propagate from a dependency):

| Entry | npm severity | Cause/dependency |
|---|---|---|
| @expo/cli | high | @expo/code-signing-certificates, @expo/config, @expo/config-plugins, @expo/inline-modules, @expo/metro, @expo/metro-config, @expo/prebuild-config, node-forge |
| @expo/code-signing-certificates | high | node-forge |
| @expo/config | moderate | @expo/config-plugins |
| @expo/config-plugins | moderate | xcode |
| @expo/inline-modules | moderate | @expo/config-plugins |
| @expo/local-build-cache-provider | moderate | @expo/config |
| @expo/metro | high | metro, metro-config, metro-file-map, metro-transform-worker |
| @expo/metro-config | high | @expo/config, @expo/metro |
| @expo/prebuild-config | moderate | @expo/config, @expo/config-plugins |
| @react-native-community/datetimepicker | high | expo |
| @react-native/community-cli-plugin | high | metro, metro-config |
| @react-native/virtualized-lists | high | react-native |
| braces | high | GHSA-vfj7-8cjw-p6xm |
| expo | high | @expo/cli, @expo/config, @expo/config-plugins, @expo/local-build-cache-provider, @expo/metro, @expo/metro-config |
| expo-sharing | moderate | @expo/config-plugins |
| metro | high | metro-config, metro-file-map, metro-transform-worker |
| metro-config | high | metro |
| metro-file-map | high | micromatch |
| metro-transform-worker | high | metro |
| micromatch | high | braces |
| node-forge | high | GHSA-86w9-cpqp-85rv |
| react-native | high | @react-native/community-cli-plugin, @react-native/virtualized-lists |
| uuid | moderate | GHSA-w5hq-g745-h8pq |
| xcode | moderate | uuid |

## Scanner triage and coverage limits

Bandit scanned 4,161 lines of application Python without parse errors and produced six alerts. None adds a separately confirmed vulnerability: the URL-open target is a fixed HTTPS provider endpoint; XML `escape` is used for output encoding rather than XML parsing; empty login initialization and the protocol label `bearer` are not secret credentials; malformed login input is rejected after the broad parse exception. These alerts are retained in the raw scan for review.

Reviewed protections that were present: parameterized ORM queries; role checks and patron owner checks in examined message/notification/cancellation routes; role-separated staff inboxes; explicit CORS origins; native SecureStore token use; image type/dimension/per-file-size checks; formula-safe CSV text; escaped PDF markup; shell-only PWA caching rather than API-response caching. These observations do not establish the absence of all SQL injection, IDOR, upload, or cache bugs.

Additional hardening: remove CSP inline script allowances as part of the XSS fixes; consider HttpOnly secure browser sessions, dependency integrity controls, a shared rate-limit store for multi-worker deployment, and a less restrictive password maximum that permits long passphrases. These are not counted as separate confirmed findings.

**Priority:** address F01/F02 immediately; rotate the SMTP credential; repair both stored-XSS sinks; enforce deployed HTTPS. Then fix reset-grant invalidation, session teardown, input/export validation and availability limits, and atomic circulation rules. Review existing privileged accounts, sessions and logs after closing the account-takeover paths.

**Raw evidence:** [npm audit](D:/Master/build/security/npm-advisories.json), [Python dependency audit](D:/Master/build/security/python-advisories.json), [Bandit application scan](D:/Master/build/security/bandit-app.json). Audit tools were placed under `build/security/tools`, separate from the runtime environment. The root npm lockfile contains no packages; the actual Node project audited is `mobile-native`.

