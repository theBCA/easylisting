"""Flask extension singletons.

Instantiated WITHOUT an app so blueprints can `from extensions import limiter, csrf`
without importing `app` (avoids circular imports). The app binds them via init_app()
in app.py.
"""
import os

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect

limiter = Limiter(
    get_remote_address,
    default_limits=["200 per day", "60 per hour"],
    storage_uri=os.getenv("REDIS_URL", "memory://"),
)

csrf = CSRFProtect()
