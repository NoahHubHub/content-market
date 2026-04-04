# Content Market

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
git clone https://github.com/NoahHubHub/Content-Market.git
cd Content-Market

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
| `ASSET_LINK_FINGERPRINT` | SHA-256 Fingerprint für TWA | Nur Play Store |
| `ANDROID_PACKAGE_NAME` | Android App Package Name | Nur Play Store |

## YouTube API Compliance

Diese App nutzt die YouTube Data API v3 gemäß den [YouTube API Terms of Service](https://developers.google.com/youtube/terms/api-services-terms-of-service).

- Videodaten werden nach 30 Tagen ohne aktive Nutzung gelöscht
- Alle Nutzer werden über die Datenschutzseite auf die Google-Datenschutzerklärung hingewiesen
- Die App ist kein Finanzprodukt – alle Werte sind virtuelle Spielwährung
- YouTube® ist eine eingetragene Marke von Google LLC

## Lizenz

Privat — alle Rechte vorbehalten.
