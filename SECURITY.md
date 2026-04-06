# Security Policy

## Reporting Security Vulnerabilities

If you discover a security vulnerability in Clip Capital, please email:
**clipcapitalcontact@gmail.com** with subject `[SECURITY] Brief description`

Do **not** open public GitHub issues for security vulnerabilities.

### Vulnerability Report Template

```
Subject: [SECURITY] Brief description

- Type of vulnerability
- Location in code/system
- Steps to reproduce (if applicable)
- Potential impact
- Your contact info
```

Response time: We aim to respond within 48 hours.

---

## Security Practices

### Data Protection
- OAuth tokens encrypted with Fernet (AES-128-CBC, derived from `SECRET_KEY`)
- HTTPS/TLS for all data in transit
- CSRF protection on all state-changing operations (`csrf.py` middleware)
- Passwords hashed with bcrypt

### Access Control
- Session-based authentication (cookie, 30-day expiry)
- Role-based access (`is_admin` flag)
- Rate limiting via slowapi
- Account lockout after 5 failed login attempts (15-minute window)

### Compliance
- YouTube API: public endpoints only — no private user data accessed
- Data Retention: 30 days for stats/metadata, 90 days for audit logs
- User Deletion: immediate via `/account/delete` or 30-day scheduled window
- Privacy Policy: `/privacy`

### Audit Trail
- All security-relevant events logged (login, logout, deletion, consent)
- Audit logs retained for 90 days, then automatically purged
- IP addresses recorded for security investigations

---

## Security Update Policy

| Severity | Response time |
|---|---|
| Critical | Within 24 hours |
| High | Within 1 week |
| Medium | Within 2 weeks |
| Low | Next release |

---

## Contact

Security: clipcapitalcontact@gmail.com
