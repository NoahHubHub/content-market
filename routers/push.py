"""Push notification routes: subscribe, unsubscribe, and send utility."""
import json
import os
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import Column, Integer, String, Text
from database import Base, engine, get_db
from helpers import get_login

router = APIRouter(prefix="/push")


# ── Inline model (avoids touching models.py) ──────────────────────────────────

class PushSubscription(Base):
    __tablename__ = "push_subscriptions"
    __table_args__ = {"extend_existing": True}
    id       = Column(Integer, primary_key=True)
    user_id  = Column(Integer, nullable=False, index=True)
    endpoint = Column(String(512), unique=True, nullable=False)
    keys     = Column(Text, nullable=False)   # JSON: {p256dh, auth}


Base.metadata.create_all(bind=engine)


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("/vapid-public-key")
async def vapid_public_key():
    key = os.getenv("VAPID_PUBLIC_KEY", "")
    return JSONResponse({"publicKey": key})


@router.post("/subscribe")
async def subscribe(request: Request, db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    if not db_user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    body = await request.json()
    endpoint = body.get("endpoint", "")
    keys     = json.dumps(body.get("keys", {}))

    if not endpoint:
        return JSONResponse({"error": "Missing endpoint"}, status_code=400)

    existing = db.query(PushSubscription).filter_by(endpoint=endpoint).first()
    if existing:
        existing.user_id = db_user.id
        existing.keys    = keys
    else:
        db.add(PushSubscription(user_id=db_user.id, endpoint=endpoint, keys=keys))
    db.commit()
    return JSONResponse({"ok": True})


@router.delete("/unsubscribe")
async def unsubscribe(request: Request, db: Session = Depends(get_db)):
    db_user = get_login(request, db)
    if not db_user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    body     = await request.json()
    endpoint = body.get("endpoint", "")
    sub = db.query(PushSubscription).filter_by(endpoint=endpoint, user_id=db_user.id).first()
    if sub:
        db.delete(sub)
        db.commit()
    return JSONResponse({"ok": True})


# ── Send utility (called from scheduler or other routes) ───────────────────────

def send_push_to_user(user_id: int, title: str, body: str, url: str = "/", db=None):
    """Send a push notification to all subscriptions of a user.
    Requires VAPID_PRIVATE_KEY, VAPID_PUBLIC_KEY, VAPID_CLAIMS_EMAIL env vars.
    Silently skips if pywebpush is not installed or VAPID keys are missing.
    """
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        return

    private_key = os.getenv("VAPID_PRIVATE_KEY", "")
    claims_email = os.getenv("VAPID_CLAIMS_EMAIL", "mailto:admin@contentmarket.app")
    if not private_key:
        return

    close_db = False
    if db is None:
        from database import SessionLocal
        db = SessionLocal()
        close_db = True

    try:
        subs = db.query(PushSubscription).filter_by(user_id=user_id).all()
        payload = json.dumps({"title": title, "body": body, "url": url})
        for sub in subs:
            keys = json.loads(sub.keys)
            try:
                webpush(
                    subscription_info={"endpoint": sub.endpoint, "keys": keys},
                    data=payload,
                    vapid_private_key=private_key,
                    vapid_claims={"sub": claims_email},
                )
            except WebPushException:
                # Remove expired/invalid subscriptions
                db.delete(sub)
                db.commit()
    finally:
        if close_db:
            db.close()
