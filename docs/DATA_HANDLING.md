# Data Handling & Privacy Documentation

**App:** Clip Capital  
**Last Updated:** 2026-04-08

---

## 1. YouTube Data Collected

| Data Point | Purpose | Retention | Source |
|---|---|---|---|
| Video ID | Portfolio tracking | Until account deletion | YouTube API |
| Title | Display | 30 days (if video unused) | `videos.list` |
| Channel name | Attribution | 30 days (if video unused) | `videos.list` |
| Channel ID | Channel page | 30 days (if video unused) | `videos.list` |
| Thumbnail URL | Display | 30 days (if video unused) | `videos.list` |
| View count | Display only (views/day stat) | 30 days | `videos.list` |
| Like count | Display only | 30 days | `videos.list` |
| Comment count | Display only | 30 days | `videos.list` |

All YouTube data is sourced exclusively from **public** endpoints. No private user data is accessed.

---

## 2. User Data Collected

| Data Point | Purpose | Retention | User Control |
|---|---|---|---|
| Username | Identity | Until account deletion | Delete via `/account` |
| Password hash (bcrypt) | Authentication | Until deletion | Change via `/account` |
| Google ID | OAuth link | Until deletion | Disconnect via Google settings |
| Google email | Account association | Until deletion | Delete via `/account` |
| Google OAuth token (encrypted) | Authentifizierung (Identität) | Until deletion / token expiry | Revoke at myaccount.google.com |
| Portfolio holdings | Game mechanic | Until deletion | Delete via `/account` |
| Transaction history | Audit + game | Until deletion | Export via `/account/export` |
| XP, Level, Achievements | Gamification | Until deletion | Delete via `/account` |
| Login IP address | Security | 90 days | N/A (security requirement) |
| Consent timestamp | Compliance | Until deletion | N/A (compliance requirement) |

---

## 3. Data Storage

- **Location:** PostgreSQL on Railway.app (US region)
- **Encryption at rest:** Railway infrastructure encryption
- **OAuth token encryption:** Fernet (AES-128-CBC, derived from SECRET_KEY)
- **Transport:** HTTPS/TLS for all connections
- **Backups:** Automated daily by Railway

---

## 4. Automatic Data Deletion Schedule

| Data | Retention | Scheduler Job | Time (UTC) |
|---|---|---|---|
| `VideoStats` rows | 30 days | `cleanup_old_stats` | Daily 04:00 |
| `Video` records (inactive) | 30 days | `cleanup_inactive_videos` | Daily 04:30 |
| `AuditLog` rows | 90 days | `cleanup_old_audit_logs` | Daily 05:00 |
| Scheduled user accounts | 30 days after request | `purge_deleted_users` | Daily 03:00 |

---

## 5. User-Initiated Deletion

1. User navigates to `/account`
2. Clicks **"Konto löschen (30-Tage-Fenster)"** — starts 30-day window
3. OR clicks **"Sofort löschen"** — immediate and irreversible
4. For scheduled deletion: all data purged automatically after 30 days
5. User can cancel within the 30-day window via `/account`

---

## 6. Third-Party Sharing

We do **not** sell, rent, or share user data or YouTube data with any third parties.

Exception: Legal requests from law enforcement (with valid legal process).

---

## 7. Security Measures

- Fernet encryption for OAuth tokens
- bcrypt for passwords
- CSRF protection on all state-changing requests
- Rate limiting (slowapi): 5 requests/minute on registration, 10 on login
- Account lockout after 5 failed login attempts
- Session expiry after 30 days of inactivity

---

## 8. Contact

Privacy questions: clipcapitalcontact@gmail.com  
Data export request: `/account/export`  
Data deletion: `/account`

