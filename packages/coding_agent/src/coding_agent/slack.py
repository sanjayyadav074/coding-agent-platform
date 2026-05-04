"""Slack request signature verification (https://api.slack.com/authentication/verifying-requests-from-slack).

Slack signs every request with HMAC-SHA256 over ``v0:{timestamp}:{body}`` using
the app's signing secret.  Always verify both the signature *and* the
timestamp freshness to defeat replay attacks.
"""
from __future__ import annotations

import hashlib
import hmac
import time

_MAX_TIMESTAMP_SKEW_SECONDS = 60 * 5  # Slack's recommended bound


class SlackSignatureError(Exception):
    """Raised when a Slack request fails verification."""


def verify_slack_signature(
    *,
    signing_secret: str,
    request_body: bytes,
    timestamp: str | None,
    signature: str | None,
    now: float | None = None,
) -> None:
    """Validate a Slack-signed request or raise :class:`SlackSignatureError`."""
    if not signing_secret:
        raise SlackSignatureError("Slack signing secret is not configured")
    if not timestamp or not signature:
        raise SlackSignatureError("Missing Slack signature headers")

    try:
        ts_int = int(timestamp)
    except ValueError as exc:
        raise SlackSignatureError("Invalid X-Slack-Request-Timestamp") from exc

    current = now if now is not None else time.time()
    if abs(current - ts_int) > _MAX_TIMESTAMP_SKEW_SECONDS:
        raise SlackSignatureError("Slack timestamp outside allowed window")

    base = b"v0:" + timestamp.encode("ascii") + b":" + request_body
    expected = "v0=" + hmac.new(
        signing_secret.encode("utf-8"), base, hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        raise SlackSignatureError("Slack signature mismatch")
