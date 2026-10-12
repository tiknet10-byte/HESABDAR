# Security & backups

## Security
* Passwords hashed with **Argon2**; minimum strength enforced; account lock after 5 failed logins (15 min).
* **JWT** access tokens (8 h). Roles: owner, admin, accountant, receptionist, staff, ai_agent — each API
  route checks a permission (`read, write, finance, reports, settings, users, backup, plugins`).
  Receptionists cannot see financial reports; only the owner can restore backups or manage users.
* **Tamper-evident audit log**: every write is recorded with a SHA-256 hash chained to the previous record;
  *Settings → Security* verifies the chain.
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
