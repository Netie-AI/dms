# App sign-in (UX-SIGNIN-01)

`studio.netie.ai` is still behind nginx HTTP basic auth. A buyer gets the
browser password popup. There is no Netie mark, no way to request access, and
cancel is nginx's bare 401.

`DMS_APP_LOGIN` replaces that door with a page in the app. **The flag defaults
off.** This change does not turn it on, and it does not edit nginx, compose, or
any other deploy file. Turn it on only after the founder's one-hour objection
window, and only on the host you mean to change.

Off, `/v1/auth/*` is not mounted. The ask path is unchanged. Anyone who can
reach the host still acts as the configured deployment, which is DR-0004.

## What the page does when the flag is on

- Email and password. The password is checked against a `pbkdf2_sha256` hash.
  The plaintext is not stored and not logged.
- If that email is in `dms.users` with a membership, the session role is the
  membership role (viewer, steward, or admin). Otherwise the role is the one
  on the account line.
- Session cookie `dms_session` (HttpOnly, SameSite=Lax). It dies on process
  restart. It is not written to `dms.sessions`.
- A form token (cookie `dms_csrf` plus header `x-csrf-token`) on sign-in,
  sign-out, and request access.
- A short rate limit, counted in this process only.
- Request access files a name, email, company, and note for a steward or
  admin to read at `GET /v1/auth/access-requests`. Nothing is emailed.
- A signed-out call gets JSON a person can read (`sign_in_required`), not an
  empty 401. A signed-in viewer who opens the request list gets
  `access_denied`. The app shows the same sentences on a Netie page.
- Identity headers (`x-dms-role` and the others) are still refused. The
  ledger actor stays the configured deployment.

Sign-in calls the Cortex compliance gate and fails closed when the gate is
down, the same as any other write.

## Accounts

Do not commit a password, a hash, or the account file.

```
python -c "import getpass; from dms_api.app_login import hash_password; print(hash_password(getpass.getpass()))"
```

Run that from the API tree (`PYTHONPATH` includes `apps/api`). Put the hash in
a file outside the repo, one account per line:

```
buyer@company.example viewer pbkdf2_sha256$<salt>$<hash>
steward@company.example steward pbkdf2_sha256$<salt>$<hash>
```

Lines that are not `email role pbkdf2_sha256$...` are skipped, including a
plaintext password. Then:

```
DMS_APP_LOGIN=1
DMS_APP_LOGIN_ACCOUNTS_FILE=/path/outside/the/repo/accounts.txt
```

`DMS_APP_LOGIN_ACCOUNTS` is the same text in the environment, for a short
local run. Prefer the file.

On HTTPS, or when nginx terminates TLS and the app still sees http, also set
`DMS_APP_LOGIN_COOKIE_SECURE=1` so the browser only sends the cookie on HTTPS.
Leave it unset for a local http run.

`DMS_APP_LOGIN_ATTEMPT_LIMIT` defaults to 5 sign-in attempts per email and
client address in 15 minutes. You do not need to set it.

## Switching nginx off basic auth

Do this on the host, after the flag is on and a steward can sign in. Do not
commit the result into this repo.

1. Confirm `DMS_APP_LOGIN=1`, the account file, and `DMS_APP_LOGIN_COOKIE_SECURE=1`
   on the DMS process. Restart DMS. `GET /v1/auth/session` returns
   `enabled: true` and `authenticated: false`.
2. In the studio server block, remove the basic-auth lines:

```
auth_basic "Restricted";
auth_basic_user_file /etc/nginx/.htpasswd;
```

3. Keep the UI and `/api` on one host (`studio.netie.ai` and
   `studio.netie.ai/api`) so the session cookie is first-party. Proxy `/api`
   to the DMS API and strip any leftover basic-auth header:

```
proxy_set_header Authorization "";
```

4. Do not add `error_page 401` back to nginx. A missing session is the app's
   sign-in page. The API's 401 body is JSON (`sign_in_required`).
5. Reload nginx. A buyer should see the Netie sign-in page. Cancel is that
   page, not a bare nginx 401. Request access files a row for the steward.

If the flag is still off, removing basic auth opens the host to anyone who
can reach it (DR-0004). Leave basic auth in place until the flag is on.
