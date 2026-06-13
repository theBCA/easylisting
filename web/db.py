"""
SQLite store for per-shop usage, templates, and billing.
"""
import json
import sqlite3
import os
from datetime import datetime, timezone

from core.config import plan_limit

DB_PATH   = os.getenv("DB_PATH", "easylisting.sqlite")
FREE_LIMIT = 3
FREE_IMPROVE_LIMIT = 1
PHOTO_VARIANT_MONTHLY_LIMIT = int(os.getenv("PHOTO_VARIANT_MONTHLY_LIMIT", "30"))


def _current_period() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _conn():
    con = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    return con


def init_db():
    with _conn() as con:
        con.execute("PRAGMA journal_mode=WAL")
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
        con.execute("""
            CREATE TABLE IF NOT EXISTS payment_events (
                id                     INTEGER PRIMARY KEY AUTOINCREMENT,
                event                  TEXT NOT NULL,
                shop_id                TEXT,
                shop_name              TEXT,
                plan                   TEXT,
                surface                TEXT,
                domain                 TEXT,
                stripe_session_id      TEXT,
                stripe_customer_id     TEXT,
                stripe_subscription_id TEXT,
                detail                 TEXT,
                created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_payment_events_time ON payment_events(created_at)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_payment_events_event ON payment_events(event)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_payment_events_shop ON payment_events(shop_id)")
        init_magic_tables(con)
        con.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                shop_id    TEXT,
                message    TEXT NOT NULL,
                reply_to   TEXT,
                page       TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS mobile_tokens (
                token         TEXT PRIMARY KEY,
                shop_id       TEXT NOT NULL,
                access_token  TEXT,
                refresh_token TEXT,
                expires_at    INTEGER,
                shop_name     TEXT,
                is_guest      INTEGER DEFAULT 0,
                guest_id      TEXT,
                is_email      INTEGER DEFAULT 0,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        for ddl in (
            "ALTER TABLE mobile_tokens ADD COLUMN refresh_token TEXT",
            "ALTER TABLE mobile_tokens ADD COLUMN expires_at INTEGER",
            "ALTER TABLE shops ADD COLUMN stripe_customer_id TEXT DEFAULT NULL",
            "ALTER TABLE shops ADD COLUMN stripe_subscription_id TEXT DEFAULT NULL",
            "ALTER TABLE shops ADD COLUMN plan TEXT DEFAULT 'free'",
            "ALTER TABLE shops ADD COLUMN free_improve_used INTEGER DEFAULT 0",
            "ALTER TABLE shops ADD COLUMN photo_variant_used INTEGER DEFAULT 0",
            "ALTER TABLE shops ADD COLUMN photo_variant_period TEXT DEFAULT NULL",
            "ALTER TABLE shops ADD COLUMN listing_used INTEGER DEFAULT 0",
            "ALTER TABLE shops ADD COLUMN listing_period TEXT DEFAULT NULL",
            "ALTER TABLE shops ADD COLUMN improve_used INTEGER DEFAULT 0",
            "ALTER TABLE shops ADD COLUMN improve_period TEXT DEFAULT NULL",
            "ALTER TABLE shops ADD COLUMN last_seen TIMESTAMP DEFAULT NULL",
            "ALTER TABLE shops ADD COLUMN country TEXT DEFAULT NULL",
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


def _bump_monthly(con, shop_id: str, used_col: str, period_col: str, count: int = 1):
    """Increment a monthly counter, resetting it when the period rolls over."""
    period = _current_period()
    row = con.execute(
        f"SELECT {period_col} AS p FROM shops WHERE shop_id = ?", (str(shop_id),)
    ).fetchone()
    if row and row["p"] == period:
        con.execute(
            f"UPDATE shops SET {used_col} = {used_col} + ? WHERE shop_id = ?",
            (int(count), str(shop_id)),
        )
    else:
        con.execute(
            f"UPDATE shops SET {period_col} = ?, {used_col} = ? WHERE shop_id = ?",
            (period, int(count), str(shop_id)),
        )


def _touch_shop(con, shop_id: str, country: str = None):
    if country:
        con.execute(
            "UPDATE shops SET last_seen = CURRENT_TIMESTAMP, country = ? WHERE shop_id = ?",
            (country, str(shop_id)),
        )
    else:
        con.execute(
            "UPDATE shops SET last_seen = CURRENT_TIMESTAMP WHERE shop_id = ?",
            (str(shop_id),),
        )


def increment_usage(shop_id: str, country: str = None):
    with _conn() as con:
        # Lifetime counter (free-tier paywall) + monthly counter (paid-plan caps).
        con.execute(
            "UPDATE shops SET free_used = free_used + 1 WHERE shop_id = ?",
            (str(shop_id),),
        )
        _bump_monthly(con, shop_id, "listing_used", "listing_period")
        _touch_shop(con, shop_id, country)


def increment_improve_usage(shop_id: str, country: str = None):
    with _conn() as con:
        con.execute(
            "UPDATE shops SET free_improve_used = free_improve_used + 1 WHERE shop_id = ?",
            (str(shop_id),),
        )
        _bump_monthly(con, shop_id, "improve_used", "improve_period")
        _touch_shop(con, shop_id, country)


def _monthly_remaining(shop: dict, used_col: str, period_col: str, cap: int) -> int:
    used = shop.get(used_col, 0) if shop.get(period_col) == _current_period() else 0
    return max(0, cap - used)


def can_generate(shop_id: str, limit: int = FREE_LIMIT) -> tuple[bool, int]:
    shop = get_shop(shop_id)
    if not shop:
        return True, limit
    if shop["has_premium"]:
        cap = plan_limit(shop.get("plan"), "listings")
        remaining = _monthly_remaining(shop, "listing_used", "listing_period", cap)
        return remaining > 0, remaining
    remaining = max(0, limit - shop["free_used"])
    return remaining > 0, remaining


def can_improve(shop_id: str, limit: int = FREE_IMPROVE_LIMIT) -> tuple[bool, int]:
    shop = get_shop(shop_id)
    if not shop:
        return True, limit
    if shop["has_premium"]:
        cap = plan_limit(shop.get("plan"), "improve")
        remaining = _monthly_remaining(shop, "improve_used", "improve_period", cap)
        return remaining > 0, remaining
    remaining = max(0, limit - shop.get("free_improve_used", 0))
    return remaining > 0, remaining


def can_generate_photo_variants(shop_id: str, count: int,
                                limit: int = None) -> tuple[bool, int]:
    shop = get_shop(shop_id)
    if not shop or not shop.get("has_premium"):
        return False, 0
    cap = limit if limit is not None else plan_limit(shop.get("plan"), "photo_images")
    if cap <= 0:
        return False, 0
    remaining = _monthly_remaining(shop, "photo_variant_used", "photo_variant_period", cap)
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
    con.execute("""
        CREATE TABLE IF NOT EXISTS fp_sessions (
            fp_id        TEXT PRIMARY KEY,
            email_shop_id TEXT NOT NULL,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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


def get_marketing_stats(exclude_hashes: list[str] | None = None) -> dict:
    excl = tuple(exclude_hashes or [])
    placeholders = ",".join("?" * len(excl)) if excl else None

    def _excl_ve(col="email_hash"):
        return f" AND {col} NOT IN ({placeholders})" if excl else ""

    def _excl_ml(col="email_hash"):
        return f" AND {col} NOT IN ({placeholders})" if excl else ""

    def _excl_mc(col="email_hash"):
        return f" AND {col} NOT IN ({placeholders})" if excl else ""

    # Build exclusion list based on shop_id prefix for shops table queries
    excl_shop_ids = tuple(f"guest_email_{h[:20]}" for h in excl)

    with _conn() as con:
        # Count from verified_emails (populated when magic link is first sent)
        total_hashes = con.execute(
            f"SELECT COUNT(*) as n FROM verified_emails WHERE 1=1{_excl_ve()}", excl
        ).fetchone()["n"]
        # Also count from shops table (guest_email_ prefix) — survives even if
        # verified_emails is empty after a DB reset
        total_email_shops = con.execute(
            "SELECT COUNT(*) as n FROM shops WHERE shop_id LIKE 'guest_email_%'"
            + (f" AND shop_id NOT IN ({','.join('?'*len(excl_shop_ids))})" if excl_shop_ids else ""),
            excl_shop_ids,
        ).fetchone()["n"]
        total_hashes = max(total_hashes, total_email_shops)

        total_links_sent = con.execute(
            f"SELECT COUNT(*) as n FROM magic_links WHERE 1=1{_excl_ml()}", excl
        ).fetchone()["n"]
        total_verified = con.execute(
            f"SELECT COUNT(*) as n FROM magic_links WHERE used_at IS NOT NULL{_excl_ml()}", excl
        ).fetchone()["n"]
        total_consented = con.execute(
            f"SELECT COUNT(*) as n FROM marketing_consents WHERE unsubscribed_at IS NULL{_excl_mc()}", excl
        ).fetchone()["n"]
        total_unsubscribed = con.execute(
            f"SELECT COUNT(*) as n FROM marketing_consents WHERE unsubscribed_at IS NOT NULL{_excl_mc()}", excl
        ).fetchone()["n"]
        by_locale = con.execute(
            f"""SELECT locale, COUNT(*) as n FROM marketing_consents
               WHERE unsubscribed_at IS NULL{_excl_mc()} GROUP BY locale""", excl
        ).fetchall()
        by_day = con.execute(
            f"""SELECT DATE(consented_at) as day, COUNT(*) as n
               FROM marketing_consents WHERE unsubscribed_at IS NULL{_excl_mc()}
               GROUP BY DATE(consented_at) ORDER BY day DESC LIMIT 30""", excl
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


def has_verified_email(email_hash: str) -> bool:
    """True if this email hash has at least one used (clicked) magic link."""
    with _conn() as con:
        row = con.execute(
            "SELECT COUNT(*) as n FROM magic_links WHERE email_hash = ? AND used_at IS NOT NULL",
            (email_hash,),
        ).fetchone()
        return bool(row and row["n"] > 0)


def get_email_shop(email_hash: str) -> str | None:
    """Return shop_id if this email exists in verified_emails, None if brand new."""
    with _conn() as con:
        row = con.execute(
            "SELECT shop_id FROM verified_emails WHERE email_hash = ?", (email_hash,)
        ).fetchone()
        return row["shop_id"] if row else None


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


def log_payment_event(event: str, shop_id: str = None, shop_name: str = None,
                      plan: str = None, surface: str = None, domain: str = None,
                      stripe_session_id: str = None, stripe_customer_id: str = None,
                      stripe_subscription_id: str = None, detail: dict | str | None = None):
    if isinstance(detail, dict):
        detail = json.dumps(detail, sort_keys=True)
    with _conn() as con:
        con.execute(
            """INSERT INTO payment_events
               (event, shop_id, shop_name, plan, surface, domain, stripe_session_id,
                stripe_customer_id, stripe_subscription_id, detail)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event,
                str(shop_id) if shop_id is not None else None,
                shop_name,
                plan,
                surface,
                domain,
                stripe_session_id,
                stripe_customer_id,
                stripe_subscription_id,
                detail,
            ),
        )


def get_payment_summary(days: int = 30) -> dict:
    with _conn() as con:
        params = (f"-{days}",)
        by_event = con.execute("""
            SELECT event, COUNT(*) AS n
            FROM payment_events
            WHERE created_at >= datetime('now', ? || ' days')
            GROUP BY event
            ORDER BY n DESC
        """, params).fetchall()
        by_plan = con.execute("""
            SELECT COALESCE(plan, 'unknown') AS plan, COUNT(*) AS n
            FROM payment_events
            WHERE created_at >= datetime('now', ? || ' days')
              AND event IN ('checkout_created', 'checkout_completed')
            GROUP BY COALESCE(plan, 'unknown')
            ORDER BY n DESC
        """, params).fetchall()
        by_domain = con.execute("""
            SELECT COALESCE(domain, 'unknown') AS domain, COUNT(*) AS n
            FROM payment_events
            WHERE created_at >= datetime('now', ? || ' days')
              AND event IN ('checkout_created', 'checkout_completed')
            GROUP BY COALESCE(domain, 'unknown')
            ORDER BY n DESC
        """, params).fetchall()
        recent = con.execute("""
            SELECT event, shop_id, shop_name, plan, surface, domain, created_at
            FROM payment_events
            ORDER BY id DESC
            LIMIT 30
        """).fetchall()

    events = {r["event"]: r["n"] for r in by_event}
    attempts = events.get("checkout_created", 0)
    completed = events.get("checkout_completed", 0)
    return {
        "days": days,
        "by_event": events,
        "by_plan": [dict(r) for r in by_plan],
        "by_domain": [dict(r) for r in by_domain],
        "recent": [dict(r) for r in recent],
        "attempts": attempts,
        "completed": completed,
        "conversion_pct": round((completed / attempts) * 100, 1) if attempts else 0,
    }


def get_shop_by_stripe_customer(customer_id: str) -> dict | None:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM shops WHERE stripe_customer_id = ?", (customer_id,)
        ).fetchone()
        return dict(row) if row else None


# ── Platform credentials ──────────────────────────────────────────────────────

def save_platform_credentials(shop_id: str, platform: str, creds: dict):
    with _conn() as con:
        con.execute(
            """INSERT INTO platform_credentials (shop_id, platform, credentials)
               VALUES (?, ?, ?)
               ON CONFLICT(shop_id, platform) DO UPDATE
               SET credentials = excluded.credentials,
                   connected_at = CURRENT_TIMESTAMP""",
            (str(shop_id), platform, json.dumps(creds)),
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
            return json.loads(row["credentials"])
        except Exception:
            return None


def delete_platform_credentials(shop_id: str, platform: str):
    with _conn() as con:
        con.execute(
            "DELETE FROM platform_credentials WHERE shop_id=? AND platform=?",
            (str(shop_id), platform),
        )


def save_fp_session(fp_id: str, email_shop_id: str):
    with _conn() as con:
        con.execute(
            "INSERT OR REPLACE INTO fp_sessions (fp_id, email_shop_id) VALUES (?, ?)",
            (fp_id, email_shop_id),
        )


def get_fp_session(fp_id: str) -> str | None:
    with _conn() as con:
        row = con.execute(
            "SELECT email_shop_id FROM fp_sessions WHERE fp_id = ?", (fp_id,)
        ).fetchone()
        return row["email_shop_id"] if row else None


# ── Mobile token auth ─────────────────────────────────────────────────────────

def create_mobile_token(
    shop_id: str,
    access_token: str = None,
    shop_name: str = None,
    is_guest: bool = False,
    guest_id: str = None,
    is_email: bool = False,
    refresh_token: str = None,
    expires_at: int = None,
) -> str:
    import secrets as _sec
    token = _sec.token_urlsafe(32)
    with _conn() as con:
        con.execute(
            """INSERT INTO mobile_tokens
               (token, shop_id, access_token, refresh_token, expires_at,
                shop_name, is_guest, guest_id, is_email)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (token, str(shop_id), access_token, refresh_token, expires_at,
             shop_name, int(is_guest), guest_id, int(is_email)),
        )
    return token


def update_mobile_token_access(token: str, access_token: str,
                               refresh_token: str = None, expires_at: int = None):
    with _conn() as con:
        con.execute(
            """UPDATE mobile_tokens
               SET access_token = ?, refresh_token = COALESCE(?, refresh_token),
                   expires_at = ?
               WHERE token = ?""",
            (access_token, refresh_token, expires_at, token),
        )


def get_by_mobile_token(token: str) -> dict | None:
    if not token:
        return None
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM mobile_tokens WHERE token = ?", (token,)
        ).fetchone()
        return dict(row) if row else None


def delete_mobile_token(token: str):
    with _conn() as con:
        con.execute("DELETE FROM mobile_tokens WHERE token = ?", (token,))


def save_feedback(shop_id: str, message: str, reply_to: str = None, page: str = None):
    with _conn() as con:
        con.execute(
            "INSERT INTO feedback (shop_id, message, reply_to, page) VALUES (?, ?, ?, ?)",
            (shop_id, message[:2000], (reply_to or "")[:200], (page or "")[:200]),
        )
