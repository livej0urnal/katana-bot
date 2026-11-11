import hashlib
import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError


logger = logging.getLogger(__name__)

RECORD = """
local now = redis.call('TIME')
local millis = tonumber(now[1]) * 1000 + math.floor(tonumber(now[2]) / 1000)
local window = tonumber(ARGV[1])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', millis - window)
if redis.call('ZADD', KEYS[1], 'NX', millis, ARGV[3]) == 0 then return 0 end
redis.call('PEXPIRE', KEYS[1], window)
local flooded = redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[2])
if ARGV[4] == '1' then
    redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', millis - window)
    redis.call('ZADD', KEYS[2], millis, ARGV[3])
    redis.call('PEXPIRE', KEYS[2], window)
    flooded = flooded or redis.call('ZCARD', KEYS[2]) >= 3
end
return flooded and 1 or 0
"""


class RedisTraffic:
    def __init__(self, url: str):
        self.client = Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
        self.record_script = self.client.register_script(RECORD)

    async def close(self):
        await self.client.aclose()

    async def record(self, namespace, fallback, key, event_id, text="") -> bool:
        normalized = " ".join(text.casefold().split())[:500]
        digest = hashlib.sha256(normalized.encode()).hexdigest()
        prefix = f"katana:{namespace}:{{{key}}}"
        try:
            return bool(await self.record_script(
                keys=[prefix, f"{prefix}:{digest}"],
                args=[fallback.window * 1000, fallback.limit, event_id, int(bool(normalized))],
            ))
        except RedisError as error:
            logger.warning("Redis traffic window unavailable: %s", type(error).__name__)
            return fallback.record(key, event_id, text)
