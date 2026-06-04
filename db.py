"""
SQLite store for per-shop usage, templates, and billing.
"""
import json
import sqlite3
import os
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
