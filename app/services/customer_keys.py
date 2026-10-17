"""Customer key ownership and the one-time master-key activation gate."""
import secrets

from app.services.dashboard import cipher


def ensure_master_key(membership):
    if not membership.master_key_encrypted:
        raw = "gw_master_" + secrets.token_urlsafe(32)
        membership.master_key_encrypted = cipher().encrypt(raw.encode()).decode()
