"""Hosted demonstration boundary: private workspace access and durable storage."""
import hashlib
import hmac
import os
import time
from urllib.parse import urlsplit

from fastapi import HTTPException


def hosted():
    return os.getenv("REFUND_HOSTED") == "true" or os.getenv("RENDER") == "true"


def validate_settings(testing=False):
    if not hosted():
        return
    if len(os.getenv("REFUND_ACCESS_PASSWORD", "")) < 16:
        raise RuntimeError("Hosted mode requires a private REFUND_ACCESS_PASSWORD of at least 16 characters.")
    if not testing and not all(os.getenv(k) for k in ("DATABASE_URL", "CHECKPOINT_DATABASE_URL")):
        raise RuntimeError("Hosted mode requires durable Supabase operations and checkpoint connections.")
    checkpoint = urlsplit(os.getenv("CHECKPOINT_DATABASE_URL", ""))
    if checkpoint.port == 6543:
        raise RuntimeError("CHECKPOINT_DATABASE_URL must use the Supabase session pooler (5432), not transaction mode.")
    if not os.getenv("RENDER_EXTERNAL_HOSTNAME") and not os.getenv("REFUND_PUBLIC_URL"):
        raise RuntimeError("Set REFUND_PUBLIC_URL or Render's external hostname for hosted access.")


def check_password(store, request, password):
    key = hashlib.sha256((request.client.host or "unknown").encode()).hexdigest()
    stamp = time.time()
    with store.connect() as c:
        c.execute("DELETE FROM login_attempts WHERE expires<?", (stamp,))
        c.execute("INSERT INTO login_attempts VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET attempts=login_attempts.attempts+1",
                  (key, 1, stamp + 900))
        row = c.execute("SELECT attempts FROM login_attempts WHERE id=?", (key,)).fetchone()
        if row["attempts"] > 10:
            raise HTTPException(429, "Too many sign-in attempts. Try again in 15 minutes.")
    expected = os.environ["REFUND_ACCESS_PASSWORD"]
    if not hmac.compare_digest(password.encode(), expected.encode()):
        raise HTTPException(401, "Invalid workspace password")
    with store.connect() as c:
        c.execute("DELETE FROM login_attempts WHERE id=?", (key,))
