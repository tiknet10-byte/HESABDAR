# Security & backups

## Security
* Passwords hashed with **Argon2**; minimum strength enforced; account lock after 5 failed logins (15 min).
* **JWT** access tokens (8 h). Roles: owner, admin, accountant, receptionist, staff, ai_agent — each API
  route checks a permission (`read, write, finance, reports, settings, users, backup, plugins`).
  Receptionists cannot see financial reports; only the owner can restore backups or manage users.
* **Tamper-evident audit log**: every write is recorded with a SHA-256 hash chained to the previous record;
  *Settings → Security* verifies the chain.
* Card numbers are never stored in full (first 6 + last 4 only). Plugin secrets are masked in API responses.
* Webhooks are HMAC-signed; security headers (nosniff, frame deny, referrer policy) on every response.
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
