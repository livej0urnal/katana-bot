import re
import time
from collections import OrderedDict, deque
from urllib.parse import urlsplit


DOMAIN = re.compile(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")


def domain_of(value: str) -> str | None:
    try:
        parsed = urlsplit(value if "://" in value else "//" + value)
        if parsed.scheme not in {"", "http", "https"} or not parsed.hostname:
            return None
        hostname = parsed.hostname.rstrip(".").encode("idna").decode().lower()
        return hostname if DOMAIN.fullmatch(hostname) else None
    except (ValueError, UnicodeError):
        return None


def has_untrusted_link(text, entities, domains: set[str]) -> bool:
    for entity in entities:
        if entity.type not in {"url", "text_link"}:
            continue
        url = entity.url if entity.type == "text_link" else entity.extract_from(text)
        hostname = domain_of(url or "")
        if not hostname or not any(
            hostname == domain or hostname.endswith("." + domain) for domain in domains
        ):
            return True
    return False


class TrafficWindow:
    """Bounded process-local rolling windows for a single polling worker."""

    def __init__(self, window: int, limit: int, *, max_keys: int = 10_000):
        self.window = window
        self.limit = limit
        self.max_keys = max_keys
        self.history = OrderedDict()

    def record(self, key, event_id: int, text: str = "") -> bool:
        now = time.monotonic()
        events = self.history.setdefault(key, deque(maxlen=self.limit))
        self.history.move_to_end(key)
        while len(self.history) > self.max_keys:
            self.history.popitem(last=False)
        while events and now - events[0][0] >= self.window:
            events.popleft()
        if any(previous_id == event_id for _, previous_id, _ in events):
            return False
        normalized = " ".join(text.casefold().split())[:500]
        events.append((now, event_id, normalized))
        repeats = sum(value == normalized for _, _, value in events) if normalized else 0
        return len(events) >= self.limit or repeats >= 3
