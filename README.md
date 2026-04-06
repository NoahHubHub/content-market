# Clip Capital

Entdecke und sammle YouTube-Videos. Setze virtuelles Budget ein für echte Videos, deren Werte anhand echter YouTube-Metriken berechnet werden — Views, Likes, Kommentare, Velocity, Kanalhistorie.

## Features

- **Engagement** — Sammeln & Entfernen von Video-Units mit realem YouTube-Wert-Algorithmus
- **Gamification** — XP, Level, Achievements, tägliche Streaks
- **Wettbewerb** — Duelle, Saisonrankings, Leagues mit Invite-Codes
- **Hot Takes** — tägliche Vorhersagen auf View-Entwicklung
- **Daily Drop** — IPO-artige zeitlimitierte Angebote
- **PWA** — installierbar auf Android (Chrome) und iOS (Safari)

## Tech Stack

| Schicht | Technologie |
|---|---|
| Backend | FastAPI (Python) |
| Datenbank | PostgreSQL (Prod) / SQLite (Dev) |
| Templates | Jinja2 |
| Scheduling | APScheduler |
| Externe API | YouTube Data API v3 |
| Deployment | Railway (Procfile) |

## Lokales Setup

```bash
# 1. Repo klonen
git clone https://github.com/NoahHubHub/Clip-Capital.git
cd clip-capital

# 2. Virtuelle Umgebung
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. Abhängigkeiten installieren
pip install -r requirements.txt

# 4. Umgebungsvariablen setzen
cp .env.example .env
# .env editieren: YOUTUBE_API_KEY und SECRET_KEY eintragen

# 5. Starten
uvicorn main:app --reload
```

App läuft auf http://localhost:8000

## Deployment auf Railway

1. Repository auf [railway.app](https://railway.app) importieren
2. PostgreSQL-Plugin hinzufügen (Dashboard → New → Database → PostgreSQL)
3. In den Environment Variables setzen:
   - `YOUTUBE_API_KEY` — Google Cloud Console → YouTube Data API v3
   - `SECRET_KEY` — zufälliger langer String (z.B. `python -c "import secrets; print(secrets.token_hex(32))"`)
   - `DATABASE_URL` — wird automatisch von Railway gesetzt wenn das PostgreSQL-Plugin aktiv ist
4. Deploy startet automatisch

## Android Launch (Play Store via TWA)

Voraussetzungen: Node.js, Java JDK 11+, Android SDK

```bash
npm install -g @bubblewrap/cli

# Mit deiner Railway-URL initialisieren
bubblewrap init --manifest https://DEINE-RAILWAY-URL/static/manifest.json

# APK bauen
bubblewrap build
```

Danach in den Railway Environment Variables setzen:
- `ASSET_LINK_FINGERPRINT` — SHA-256 Fingerprint aus dem Bubblewrap-Output
- `ANDROID_PACKAGE_NAME` — Package Name aus dem Bubblewrap-Setup (z.B. `com.contentmarket.app`)

Die generierte `.aab`-Datei kann direkt in der Google Play Console hochgeladen werden.

## Umgebungsvariablen

| Variable | Beschreibung | Pflicht |
|---|---|---|
| `YOUTUBE_API_KEY` | YouTube Data API v3 Key | Ja |
| `SECRET_KEY` | Session-Verschlüsselung | Ja |
| `DATABASE_URL` | Datenbank-URL (Standard: SQLite) | Nein (Dev) |
| `APP_URL` | Vollständige App-URL (z.B. `https://clip-capital.up.railway.app`) | Ja (Prod) |
| `GOOGLE_CLIENT_ID` | Google OAuth 2.0 Client ID | Nur OAuth |
| `GOOGLE_CLIENT_SECRET` | Google OAuth 2.0 Client Secret | Nur OAuth |
| `ASSET_LINK_FINGERPRINT` | SHA-256 Fingerprint für TWA | Nur Play Store |
| `ANDROID_PACKAGE_NAME` | Android App Package Name | Nur Play Store |

## Google OAuth 2.0 einrichten

1. [Google Cloud Console](https://console.cloud.google.com/apis/credentials) öffnen
2. „OAuth 2.0-Client-ID" erstellen (Typ: Webanwendung)
3. Autorisierte Weiterleitungs-URI hinzufügen: `https://DEINE-APP-URL/auth/google/callback`
4. `GOOGLE_CLIENT_ID` und `GOOGLE_CLIENT_SECRET` in Railway setzen
5. OAuth-Consent-Screen konfigurieren — Scopes: `openid`, `email`, `profile`, `youtube.readonly`

## YouTube API Compliance

Diese App nutzt ausschließlich **öffentliche YouTube Data API v3 Endpunkte** — es werden keine privaten Nutzerdaten von YouTube abgerufen.

### Genutzte API-Methoden

| Methode | Zweck |
|---|---|
| `videos.list` (part: `snippet,statistics,contentDetails`) | Videometadaten und Statistiken abrufen |
| `search.list` (part: `snippet`, type: `video`) | Videos suchen |
| `channels.list` (part: `snippet,statistics,contentDetails`) | Kanalinfo abrufen |
| `videos.list` (chart: `mostPopular`) | Trending-Videos für den Markt |

### Datenschutz & Löschfristen

| Datenkategorie | Aufbewahrungsfrist | Scheduler-Job |
|---|---|---|
| YouTube Statistikdaten (Views, Likes, Kommentare) | 30 Tage | `cleanup_old_stats` tägl. 04:00 UTC |
| Videometadaten (ohne aktive Nutzer) | 30 Tage | `cleanup_inactive_videos` tägl. 04:30 UTC |
| Sicherheits-Auditlogs | 90 Tage | `cleanup_old_audit_logs` tägl. 05:00 UTC |
| Transaktionshistorie | bis Kontolöschung | — |

### Weitere Compliance-Punkte

- Alle Nutzer stimmen explizit den Nutzungsbedingungen und der Datenschutzerklärung zu (Checkbox bei Registrierung / OAuth-Erstanmeldung)
- Nutzer können ihr Konto und alle Daten jederzeit selbst löschen (`/account`)
- Datenschutzrichtlinie öffentlich unter `/privacy`
- Die App ist kein Finanzprodukt — alle Werte sind virtuelle Spielwährung ohne realen Gegenwert
- Clip Capital ist nicht mit YouTube oder Google LLC verbunden — YouTube® ist eine eingetragene Marke von Google LLC

## Kontakt

Fragen zur App oder Datenschutz: **clipcapitalcontact@gmail.com**

## Lizenz

Privat — alle Rechte vorbehalten.
