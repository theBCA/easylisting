---
description: Rules that apply when editing database code
globs:
  - "web/db.py"
  - "web/apis/*.py"
  - "web/core/*.py"
---

## Database rules

- All queries use `?` placeholders — never f-strings or `.format()` in SQL
- `ensure_shop(shop_id, shop_name)` must be called before `set_premium()` on any new shop — set_premium does UPDATE, not INSERT
- `get_shop_by_stripe_customer()` is the fallback when webhook metadata lacks `shop_id` — use it before giving up
- Schema changes go in `init_db()` using `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` for backwards compatibility
- No migrations framework — Railway deploys start fresh each time from `init_db()`. Data persists only via the mounted volume.
- Two Railway services = two separate SQLite files. Never assume a shop in one DB exists in the other.
- `_conn()` uses WAL mode for concurrent reads — don't change isolation level
