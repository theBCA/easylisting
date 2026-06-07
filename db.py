"""
SQLite store for per-shop usage, templates, and billing.
"""
import json
import sqlite3
import os
import hashlib as _hashlib
import base64 as _base64
from datetime import datetime, timezone

DB_PATH   = os.getenv("DB_PATH", "easylisting.sqlite")
FREE_LIMIT = 3
FREE_IMPROVE_LIMIT = 1
PHOTO_VARIANT_MONTHLY_LIMIT = int(os.getenv("PHOTO_VARIANT_MONTHLY_LIMIT", "30"))


def _conn():
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def init_db():
    with _conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS shops (
                shop_id                TEXT PRIMARY KEY,
                shop_name              TEXT,
                free_used              INTEGER DEFAULT 0,
                has_premium            INTEGER DEFAULT 0,
                own_api_key            TEXT DEFAULT NULL,
                stripe_customer_id     TEXT DEFAULT NULL,
                stripe_subscription_id TEXT DEFAULT NULL,
                created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS templates (
                shop_id    TEXT PRIMARY KEY,
                data       TEXT DEFAULT '{}',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS abuse_signals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                event       TEXT NOT NULL,
                ip_hash     TEXT,
                guest_id    TEXT,
                fp_hash     TEXT,
                detail      TEXT,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_abuse_ip   ON abuse_signals(ip_hash)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_abuse_fp   ON abuse_signals(fp_hash)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_abuse_time ON abuse_signals(created_at)")
        init_magic_tables(con)
        for ddl in (
            "ALTER TABLE shops ADD COLUMN stripe_customer_id TEXT DEFAULT NULL",
            "ALTER TABLE shops ADD COLUMN stripe_subscription_id TEXT DEFAULT NULL",
            "ALTER TABLE shops ADD COLUMN plan TEXT DEFAULT 'free'",
            "ALTER TABLE shops ADD COLUMN free_improve_used INTEGER DEFAULT 0",
            "ALTER TABLE shops ADD COLUMN photo_variant_used INTEGER DEFAULT 0",
            "ALTER TABLE shops ADD COLUMN photo_variant_period TEXT DEFAULT NULL",
        ):
            try:
                con.execute(ddl)
            except Exception:
                pass


def get_shop(shop_id: str) -> dict | None:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM shops WHERE shop_id = ?", (str(shop_id),)
        ).fetchone()
        return dict(row) if row else None


def ensure_shop(shop_id: str, shop_name: str):
    with _conn() as con:
        con.execute(
            "INSERT OR IGNORE INTO shops (shop_id, shop_name) VALUES (?, ?)",
            (str(shop_id), shop_name),
        )


def increment_usage(shop_id: str):
    with _conn() as con:
        con.execute(
            "UPDATE shops SET free_used = free_used + 1 WHERE shop_id = ?",
            (str(shop_id),),
        )


def increment_improve_usage(shop_id: str):
    with _conn() as con:
        con.execute(
            "UPDATE shops SET free_improve_used = free_improve_used + 1 WHERE shop_id = ?",
            (str(shop_id),),
        )


def can_generate(shop_id: str, limit: int = FREE_LIMIT) -> tuple[bool, int]:
    shop = get_shop(shop_id)
    if not shop:
        return True, limit
    if shop["has_premium"]:
        return True, 999
    remaining = max(0, limit - shop["free_used"])
    return remaining > 0, remaining


def can_improve(shop_id: str, limit: int = FREE_IMPROVE_LIMIT) -> tuple[bool, int]:
    shop = get_shop(shop_id)
    if not shop:
        return True, limit
    if shop["has_premium"]:
        return True, 999
    remaining = max(0, limit - shop.get("free_improve_used", 0))
    return remaining > 0, remaining


def can_generate_photo_variants(shop_id: str, count: int,
                                limit: int = PHOTO_VARIANT_MONTHLY_LIMIT) -> tuple[bool, int]:
    shop = get_shop(shop_id)
    if not shop or shop.get("plan") != "pro":
        return False, 0

    period = datetime.now(timezone.utc).strftime("%Y-%m")
    used = shop.get("photo_variant_used", 0) if shop.get("photo_variant_period") == period else 0
    remaining = max(0, limit - used)
    return remaining >= count, remaining


def increment_photo_variant_usage(shop_id: str, count: int):
    period = datetime.now(timezone.utc).strftime("%Y-%m")
    with _conn() as con:
        row = con.execute(
            "SELECT photo_variant_period, photo_variant_used FROM shops WHERE shop_id = ?",
            (str(shop_id),),
        ).fetchone()
        if row and row["photo_variant_period"] == period:
            con.execute(
                "UPDATE shops SET photo_variant_used = photo_variant_used + ? WHERE shop_id = ?",
                (int(count), str(shop_id)),
            )
        else:
            con.execute(
                "UPDATE shops SET photo_variant_period = ?, photo_variant_used = ? WHERE shop_id = ?",
                (period, int(count), str(shop_id)),
            )


# ── Templates ─────────────────────────────────────────────────────────────────

def save_template(shop_id: str, data: dict):
    with _conn() as con:
        con.execute(
            """INSERT INTO templates (shop_id, data) VALUES (?, ?)
               ON CONFLICT(shop_id) DO UPDATE
               SET data = excluded.data, updated_at = CURRENT_TIMESTAMP""",
            (str(shop_id), json.dumps(data)),
        )


def get_template(shop_id: str) -> dict:
    with _conn() as con:
        row = con.execute(
            "SELECT data FROM templates WHERE shop_id = ?", (str(shop_id),)
        ).fetchone()
        if not row:
            return {}
        try:
            return json.loads(row["data"])
        except Exception:
            return {}


# ── Magic link auth ──────────────────────────────────────────────────────────

def init_magic_tables(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS magic_links (
            token      TEXT PRIMARY KEY,
            email_hash TEXT NOT NULL,
            shop_id    TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            used_at    TIMESTAMP DEFAULT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_ml_email ON magic_links(email_hash)")
    con.execute("""
        CREATE TABLE IF NOT EXISTS verified_emails (
            email_hash TEXT PRIMARY KEY,
            shop_id    TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS marketing_consents (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            email             TEXT NOT NULL,
            email_hash        TEXT NOT NULL,
            locale            TEXT NOT NULL DEFAULT 'en',
            source            TEXT NOT NULL DEFAULT 'magic_link',
            consented_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            unsubscribe_token TEXT UNIQUE NOT NULL,
            unsubscribed_at   TIMESTAMP DEFAULT NULL
        )
    """)
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_mc_email_hash ON marketing_consents(email_hash)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_mc_unsub ON marketing_consents(unsubscribe_token)")
    con.execute("""
        CREATE TABLE IF NOT EXISTS platform_credentials (
            shop_id      TEXT NOT NULL,
            platform     TEXT NOT NULL,
            credentials  TEXT NOT NULL,
            connected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (shop_id, platform)
        )
    """)


def get_or_create_email_shop(email_hash: str) -> str:
    with _conn() as con:
        row = con.execute(
            "SELECT shop_id FROM verified_emails WHERE email_hash = ?", (email_hash,)
        ).fetchone()
        if row:
            return row["shop_id"]
        shop_id = f"guest_email_{email_hash[:20]}"
        con.execute(
            "INSERT OR IGNORE INTO verified_emails (email_hash, shop_id) VALUES (?,?)",
            (email_hash, shop_id),
        )
        return shop_id


def create_magic_link(token: str, email_hash: str, shop_id: str):
    with _conn() as con:
        con.execute(
            "INSERT INTO magic_links (token, email_hash, shop_id) VALUES (?,?,?)",
            (token, email_hash, shop_id),
        )


def use_magic_link(token: str) -> dict | None:
    with _conn() as con:
        # Atomic: only marks used if not already used AND within 15-minute window
        cur = con.execute(
            """UPDATE magic_links SET used_at = CURRENT_TIMESTAMP
               WHERE token = ? AND used_at IS NULL
               AND created_at >= datetime('now', '-15 minutes')""",
            (token,),
        )
        if cur.rowcount == 0:
            return None
        row = con.execute(
            "SELECT * FROM magic_links WHERE token = ?", (token,)
        ).fetchone()
        return dict(row) if row else None


def add_marketing_consent(email: str, email_hash: str, locale: str = "en",
                          source: str = "magic_link") -> str | None:
    """Store explicit opt-in consent. Returns unsubscribe_token, or None if already exists."""
    import secrets as _secrets
    token = _secrets.token_urlsafe(32)
    try:
        with _conn() as con:
            con.execute(
                """INSERT OR IGNORE INTO marketing_consents
                   (email, email_hash, locale, source, unsubscribe_token)
                   VALUES (?, ?, ?, ?, ?)""",
                (email, email_hash, locale, source, token),
            )
            row = con.execute(
                "SELECT unsubscribe_token FROM marketing_consents WHERE email_hash = ?",
                (email_hash,),
            ).fetchone()
            return row["unsubscribe_token"] if row else None
    except Exception:
        return None


def unsubscribe_by_token(token: str) -> bool:
    """Mark consent withdrawn. Returns True if a row was updated."""
    with _conn() as con:
        cur = con.execute(
            """UPDATE marketing_consents
               SET unsubscribed_at = CURRENT_TIMESTAMP
               WHERE unsubscribe_token = ? AND unsubscribed_at IS NULL""",
            (token,),
        )
        return cur.rowcount > 0


def get_marketing_stats() -> dict:
    with _conn() as con:
        total_hashes = con.execute(
            "SELECT COUNT(*) as n FROM verified_emails"
        ).fetchone()["n"]
        total_links_sent = con.execute(
            "SELECT COUNT(*) as n FROM magic_links"
        ).fetchone()["n"]
        total_verified = con.execute(
            "SELECT COUNT(*) as n FROM magic_links WHERE used_at IS NOT NULL"
        ).fetchone()["n"]
        total_consented = con.execute(
            "SELECT COUNT(*) as n FROM marketing_consents WHERE unsubscribed_at IS NULL"
        ).fetchone()["n"]
        total_unsubscribed = con.execute(
            "SELECT COUNT(*) as n FROM marketing_consents WHERE unsubscribed_at IS NOT NULL"
        ).fetchone()["n"]
        by_locale = con.execute(
            """SELECT locale, COUNT(*) as n FROM marketing_consents
               WHERE unsubscribed_at IS NULL GROUP BY locale"""
        ).fetchall()
        by_day = con.execute(
            """SELECT DATE(consented_at) as day, COUNT(*) as n
               FROM marketing_consents WHERE unsubscribed_at IS NULL
               GROUP BY DATE(consented_at) ORDER BY day DESC LIMIT 30"""
        ).fetchall()
    return {
        "email_hashes_total":   total_hashes,
        "magic_links_sent":     total_links_sent,
        "magic_links_verified": total_verified,
        "verify_rate_pct":      round(total_verified / total_links_sent * 100, 1) if total_links_sent else 0,
        "marketing_subscribed": total_consented,
        "marketing_unsubscribed": total_unsubscribed,
        "by_locale": {r["locale"]: r["n"] for r in by_locale},
        "consents_by_day": [{"day": r["day"], "n": r["n"]} for r in by_day],
    }


def count_recent_magic_links(email_hash: str, minutes: int = 60) -> int:
    with _conn() as con:
        row = con.execute(
            """SELECT COUNT(*) as n FROM magic_links
               WHERE email_hash = ?
               AND created_at >= datetime('now', ? || ' minutes')""",
            (email_hash, f"-{minutes}"),
        ).fetchone()
        return row["n"] if row else 0


# ── Abuse tracking ───────────────────────────────────────────────────────────

def log_abuse_signal(event: str, ip_hash: str = None, guest_id: str = None,
                     fp_hash: str = None, detail: str = None):
    try:
        with _conn() as con:
            con.execute(
                "INSERT INTO abuse_signals (event, ip_hash, guest_id, fp_hash, detail) VALUES (?,?,?,?,?)",
                (event, ip_hash, guest_id, fp_hash, detail),
            )
    except Exception:
        pass  # never let tracking break the main flow


def get_abuse_summary(days: int = 7) -> dict:
    with _conn() as con:
        rows = con.execute("""
            SELECT event, COUNT(*) as n
            FROM abuse_signals
            WHERE created_at >= datetime('now', ? || ' days')
            GROUP BY event ORDER BY n DESC
        """, (f"-{days}",)).fetchall()
        by_event = {r["event"]: r["n"] for r in rows}

        top_ips = con.execute("""
            SELECT ip_hash, COUNT(DISTINCT guest_id) as guests, COUNT(*) as events
            FROM abuse_signals
            WHERE created_at >= datetime('now', ? || ' days') AND ip_hash IS NOT NULL
            GROUP BY ip_hash ORDER BY guests DESC LIMIT 20
        """, (f"-{days}",)).fetchall()

        top_fps = con.execute("""
            SELECT fp_hash, COUNT(DISTINCT guest_id) as guests, COUNT(*) as events
            FROM abuse_signals
            WHERE created_at >= datetime('now', ? || ' days') AND fp_hash IS NOT NULL
            GROUP BY fp_hash ORDER BY guests DESC LIMIT 20
        """, (f"-{days}",)).fetchall()

    return {
        "by_event":  by_event,
        "top_ips":   [dict(r) for r in top_ips],
        "top_fps":   [dict(r) for r in top_fps],
    }


# ── Stripe / premium ──────────────────────────────────────────────────────────

def set_premium(shop_id: str, stripe_customer_id: str,
                stripe_subscription_id: str, active: bool, plan: str = "pro"):
    with _conn() as con:
        con.execute(
            """UPDATE shops
               SET has_premium = ?,
                   plan = ?,
                   stripe_customer_id = ?,
                   stripe_subscription_id = ?
               WHERE shop_id = ?""",
            (1 if active else 0,
             plan if active else "free",
             stripe_customer_id,
             stripe_subscription_id,
             str(shop_id)),
        )


def get_shop_by_stripe_customer(customer_id: str) -> dict | None:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM shops WHERE stripe_customer_id = ?", (customer_id,)
        ).fetchone()
        return dict(row) if row else None


# ── Platform credentials (encrypted at rest) ──────────────────────────────────

def _cred_fernet():
    from cryptography.fernet import Fernet as _Fernet
    secret = os.getenv("FLASK_SECRET", "dev-secret-not-set")
    derived = _hashlib.sha256(secret.encode()).digest()
    return _Fernet(_base64.urlsafe_b64encode(derived))


def _encrypt_creds(data: dict) -> str:
    return _cred_fernet().encrypt(json.dumps(data).encode()).decode()


def _decrypt_creds(stored: str) -> dict:
    try:
        return json.loads(_cred_fernet().decrypt(stored.encode()))
    except Exception:
        # Migration path: fall back to plain JSON for rows written before encryption was added.
        return json.loads(stored)


def save_platform_credentials(shop_id: str, platform: str, creds: dict):
    with _conn() as con:
        con.execute(
            """INSERT INTO platform_credentials (shop_id, platform, credentials)
               VALUES (?, ?, ?)
               ON CONFLICT(shop_id, platform) DO UPDATE
               SET credentials = excluded.credentials,
                   connected_at = CURRENT_TIMESTAMP""",
            (str(shop_id), platform, _encrypt_creds(creds)),
        )


def get_platform_credentials(shop_id: str, platform: str) -> dict | None:
    with _conn() as con:
        row = con.execute(
            "SELECT credentials FROM platform_credentials WHERE shop_id=? AND platform=?",
            (str(shop_id), platform),
        ).fetchone()
        if not row:
            return None
        try:
            return _decrypt_creds(row["credentials"])
        except Exception:
            return None


def delete_platform_credentials(shop_id: str, platform: str):
    with _conn() as con:
        con.execute(
            "DELETE FROM platform_credentials WHERE shop_id=? AND platform=?",
            (str(shop_id), platform),
        )
