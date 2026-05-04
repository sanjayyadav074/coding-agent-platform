from __future__ import annotations

import hashlib
import hmac
import time

import pytest

from coding_agent.slack import SlackSignatureError, verify_slack_signature

SECRET = "abcd1234"  # noqa: S105 - test fixture


def _sign(body: bytes, ts: str) -> str:
    base = b"v0:" + ts.encode() + b":" + body
    return "v0=" + hmac.new(SECRET.encode(), base, hashlib.sha256).hexdigest()


def test_verify_ok() -> None:
    body = b"text=hello"
    ts = str(int(time.time()))
    sig = _sign(body, ts)
    verify_slack_signature(
        signing_secret=SECRET, request_body=body, timestamp=ts, signature=sig
    )


def test_verify_bad_signature() -> None:
    body = b"text=hello"
    ts = str(int(time.time()))
    with pytest.raises(SlackSignatureError):
        verify_slack_signature(
            signing_secret=SECRET, request_body=body, timestamp=ts, signature="v0=deadbeef"
        )


def test_verify_replay_rejected() -> None:
    body = b"text=hello"
    old_ts = str(int(time.time()) - 60 * 60)
    sig = _sign(body, old_ts)
    with pytest.raises(SlackSignatureError):
        verify_slack_signature(
            signing_secret=SECRET, request_body=body, timestamp=old_ts, signature=sig
        )


def test_verify_missing_headers() -> None:
    with pytest.raises(SlackSignatureError):
        verify_slack_signature(
            signing_secret=SECRET, request_body=b"", timestamp=None, signature=None
        )


def test_verify_no_secret() -> None:
    with pytest.raises(SlackSignatureError):
        verify_slack_signature(
            signing_secret="", request_body=b"", timestamp="1", signature="v0=x"
        )
