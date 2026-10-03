# Bank, SMS, WhatsApp and Instagram integrations

All webhooks live under `/api/plugins/...` and work only while the plugin is enabled.
Signed webhooks use HMAC-SHA256 of the raw body with `HESABDAR_WEBHOOK_SECRET`
(local installs: `backend/data/.webhook_secret`) in the `X-Hesabdar-Signature` header.

## Bank data (`bank_sync`)
| Source | How |
|---|---|
| Bank / open-banking API | In *Settings → POS & cards* pick provider `generic_http` and give `{"url", "token", "items_path", "fields"}`. Synced every 15 minutes. For a specific bank/aggregator, add a `BankProvider` subclass. |
| Bank SMS on the salon phone | Install an SMS-forwarder app on the phone that receives bank SMS; forward to `POST /api/plugins/bank_sync/webhook/bank-sms` with JSON `{"text": "...", "sender": "..."}` and the signature header. |
| Statement file | Upload the CSV exported from internet banking (Persian or English column names are detected). |

## Customer receipts (`messaging_receipts`) — beta
| Channel | Endpoint |
|---|---|
| SMS (forwarder app) | `POST /api/plugins/messaging_receipts/webhook/sms` (signed) `{"sender","text","image_base64?"}` — response contains `replies` for the app to send back |
| WhatsApp Business Cloud API | verify `GET …/webhook/whatsapp`, messages `POST …/webhook/whatsapp`; set verify token, access token and phone-number id in plugin settings |
| Instagram Messaging API | `POST …/webhook/instagram`; set the page access token |
| Manual / testing | Reconciliation page → *Customer message* (`/simulate`) |

Bot flow: receipt arrives → parsed (text rules or Claude vision) → matched against bank transactions.
Unknown sender → bot asks for the full name → customer is created with the sender's mobile/Instagram id →
deposit is registered after the bank confirms. Mismatched amounts or receipts that never show up in the bank
raise red alerts on the dashboard. Every chat message is stored and used for learning.

> Personal WhatsApp/Instagram accounts have no official API; use WhatsApp Business / Instagram professional
> accounts, or forward messages through the manual/SMS path.
