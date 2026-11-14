"""Demo principals, bounded request rate and explicit destructive confirmation."""

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time


class AccessDenied(PermissionError):
    pass


class RateLimited(AccessDenied):
    pass


class TokenBucket:
    def __init__(self, capacity=30, refill_per_second=0.5, clock=time.monotonic):
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self.clock = clock
        self.buckets = {}
        self.lock = threading.Lock()

    def consume(self, principal):
        with self.lock:
            now = self.clock()
            tokens, then = self.buckets.get(principal, (float(self.capacity), now))
            tokens = min(self.capacity, tokens + (now - then) * self.refill_per_second)
            if tokens < 1:
                self.buckets[principal] = (tokens, now)
                raise RateLimited(
                    "Request limit reached; retry after the bucket refills"
                )
            self.buckets[principal] = (tokens - 1, now)


class ConfirmationTokens:
    """Process-local confirmation, bound to one plan/principal and short expiry.

    Identity remains an explicit demo stub; this is not authentication.
    The UI obtains this token only after an explicit confirmation action.
    """

    def __init__(self, ttl=300, clock=time.time):
        self.secret = secrets.token_bytes(32)
        self.ttl = ttl
        self.clock = clock

    def issue(self, plan_key, principal):
        payload = json.dumps(
            [plan_key, principal, self.clock() + self.ttl], separators=(",", ":")
        )
        body = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
        signature = hmac.new(self.secret, body.encode(), hashlib.sha256).hexdigest()
        return body + "." + signature

    def verify(self, token, plan_key, principal):
        if not isinstance(token, str) or len(token) > 2048:
            return False
        try:
            body, signature = token.split(".")
            expected = hmac.new(self.secret, body.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                return False
            values = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
            return (
                values[0] == plan_key
                and values[1] == principal
                and values[2] > self.clock()
            )
        except (ValueError, TypeError, IndexError, UnicodeError):
            return False


def check_grant(tool, granted, confirmation=False):
    if tool["key"] not in granted:
        raise AccessDenied("Agent is not granted this tool")
    if tool.get("risk", "read") == "destructive" and not confirmation:
        raise AccessDenied(
            "Destructive action needs an unexpired confirmation for this plan"
        )
