# Security & backups

## Security
* Passwords hashed with **Argon2**; minimum strength enforced; account lock after 5 failed logins (15 min).
  Every refused login gets **one answer** (401, "wrong user name or password"), whether the name is unknown, the
  password is wrong, the account is locked or the user is inactive, and takes about the same time, so nobody can
  find out which user names exist. Tries sent at the same moment are counted one by one (they still lock).
* **Unlocking a user**: the owner presses «باز کردن قفل» next to the user in *Settings → «پرسنل و کاربران»* (saving the user
  opens it too). If the owner is the one locked out, run `python manage.py unlock <username>` in the program folder,
  or wait 15 minutes.
* **JWT** access tokens (8 h). Roles: owner, admin, accountant, receptionist, staff, ai_agent — each API
  route checks a permission (`read, write, finance, reports, settings, users, backup, plugins`).
  Receptionists and staff cannot see financial reports or the day's takings; only the owner can restore backups or
  manage users. The `ai_agent` role reads and adds customers / deposits but cannot move money (see `AI.md`).
* **Logins end at once** when a password changes (by the user or by the owner) or a user is deactivated: every token
  carries the user's `token_version`, which goes up then. The device that changed its own password stays logged in.
* **Host check (DNS rebinding)**: the program answers only when opened as `localhost`, `127.0.0.1` or another IP
  address (e.g. the PC's address on the clinic network). A server opened with a domain name, a reverse proxy that
  passes a host name, or webhooks from Meta through a tunnel need that name in `HESABDAR_ALLOWED_HOSTS`
  (in `backend/.env` or the environment; comma separated, e.g. `hesabdar.example.com,salon-pc`; `*` turns the check
  off). Otherwise the answer is 400. The local Windows install (opened at 127.0.0.1) needs nothing.
* Unexpected errors (and AI chat errors) show only a short code on screen (e.g. «کد 3fa91c»); the details are in
  `backend/data/logs/hesabdar.log` under that code.
* **Tamper-evident audit log**: every write is recorded with a SHA-256 hash chained to the previous record;
  *Settings → Security* verifies the chain (`/api/audit/verify` lists every broken record). Before 1.20.0 two records written together
  could be linked to the same earlier record (a bug, not tampering); on the first start of 1.20.0 those links are
  mended once (`audit.rechained` setting, an `audit.rechain` record lists them). A record whose content was changed
  or that points to a deleted record is never mended and stays reported.
* Card numbers are never stored in full (first 6 + last 4 only). Plugin secrets are masked in API responses.
* Webhooks are signed, and an unsigned call is refused (401):
  * bank-SMS and SMS forwarders: `X-Hesabdar-Signature` = HMAC-SHA256 of the raw body with `HESABDAR_WEBHOOK_SECRET`;
    a bank SMS with no tracking number and no date that arrives again within a day is the same SMS (no new money);
  * WhatsApp / Instagram: Meta's `X-Hub-Signature-256` checked with the **App Secret** entered in the receipts plugin
    settings; while it is empty these two webhooks accept nothing.
* The customer-receipts plugin (`messaging_receipts`) is **off on a new install**; the owner turns it on after setting it up.
  A receipt sent by a customer books a deposit by itself only when the bank shows the same amount **and tracking number**;
  a match on amount and time alone waits for a person to press «ثبت». Instagram pictures are downloaded only from
  Meta's servers (https, at most 8 MB); a saved receipt file never takes its name from the message.
* Security headers (nosniff, frame deny, referrer policy) on every response.
* In production the app refuses to start without `HESABDAR_SECRET_KEY`. Always deploy behind HTTPS.
* SQLite runs in WAL mode with `synchronous=FULL` and foreign keys on (crash-safe).

## Backups
* Automatic every `HESABDAR_BACKUP_INTERVAL_HOURS` (default 6) + on demand; newest `HESABDAR_BACKUP_KEEP` kept.
* Online snapshot → `PRAGMA integrity_check` → gzip → **AES (Fernet) encryption** with a key derived
  (PBKDF2-SHA256, 390k iterations) from `HESABDAR_BACKUP_KEY` (local: `backend/data/.backup_key`).
* Manifest with SHA-256 of the plaintext; *Verify* decrypts and re-checks checksum and integrity.
* *Restore* takes a safety backup of the current state first, then restores — one click from the UI
  or `python manage.py restore <name>`.
* Set *Settings → General → mirror folder* to a USB drive or a Google Drive/Dropbox synced folder for an
  off-site copy. **Keep a copy of the backup key somewhere safe** — without it backups cannot be decrypted.
* PostgreSQL deployments: use `pg_dump` / managed-database snapshots in addition.
