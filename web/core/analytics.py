"""PostHog analytics wrapper. No-ops silently when POSTHOG_TOKEN is unset."""
import os
import posthog as _ph

_ph.host = "https://eu.i.posthog.com"


def capture(distinct_id: str, event: str, properties: dict | None = None) -> None:
    if os.getenv("ENV") == "test":
        return
    key = os.getenv("POSTHOG_TOKEN", "")
    if not key:
        return
    _ph.api_key = key
    _ph.capture(str(distinct_id), event, properties or {})
