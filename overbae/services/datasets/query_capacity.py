import functools
import logging
import math
import threading
import uuid
from contextlib import contextmanager

import redis
from django.conf import settings
from redis.backoff import NoBackoff
from redis.retry import Retry

logger = logging.getLogger(__name__)

_RESERVE = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
for _, key in ipairs(KEYS) do
  redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
end
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[3]) or
   redis.call('ZCARD', KEYS[2]) >= tonumber(ARGV[4]) then return 0 end
for _, key in ipairs(KEYS) do
  redis.call('ZADD', key, now + tonumber(ARGV[2]), ARGV[1])
  redis.call('EXPIRE', key, tonumber(ARGV[2]) + 5)
end
return 1
"""
_RENEW = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
for _, key in ipairs(KEYS) do
  local until_at = redis.call('ZSCORE', key, ARGV[1])
  if not until_at or tonumber(until_at) <= now then return 0 end
end
for _, key in ipairs(KEYS) do
  redis.call('ZADD', key, now + tonumber(ARGV[2]), ARGV[1])
  redis.call('EXPIRE', key, tonumber(ARGV[2]) + 5)
end
return 1
"""
_RELEASE = """
for _, key in ipairs(KEYS) do redis.call('ZREM', key, ARGV[1]) end
return 1
"""


class QueryCapacityBusyError(RuntimeError):
    pass


class QueryCapacityUnavailableError(RuntimeError):
    pass


@functools.cache
def _client(url):
    return redis.from_url(
        url,
        socket_connect_timeout=1,
        socket_timeout=1,
        retry=Retry(NoBackoff(), 0),
    )


@contextmanager
def admitted(project_id):
    if settings.CELERY_BROKER_URL == "memory://":
        yield lambda: None
        return
    client = _client(settings.CELERY_BROKER_URL)
    # Both reservations must use one Redis shard and one atomic admission decision.
    keys = ["dataset-query:{admission}:global", f"dataset-query:{{admission}}:project:{project_id}"]
    owner = uuid.uuid4().hex
    seconds = math.ceil(max(30, settings.DATASET_QUERY_TIMEOUT_SECONDS + 10))
    try:
        accepted = client.eval(
            _RESERVE,
            2,
            *keys,
            owner,
            seconds,
            settings.DATASET_QUERY_MAX_IN_FLIGHT,
            settings.DATASET_QUERY_PROJECT_CONCURRENCY,
        )
    except redis.RedisError as exc:
        logger.warning("Query admission unavailable (%s)", type(exc).__name__)
        raise QueryCapacityUnavailableError from exc
    if not accepted:
        raise QueryCapacityBusyError
    stop, lost = threading.Event(), threading.Event()

    def check():
        if lost.is_set():
            raise QueryCapacityUnavailableError
        try:
            renewed = client.eval(_RENEW, 2, *keys, owner, seconds)
        except redis.RedisError as exc:
            lost.set()
            logger.warning("Query admission renewal unavailable (%s)", type(exc).__name__)
            raise QueryCapacityUnavailableError from exc
        if not renewed:
            lost.set()
            raise QueryCapacityUnavailableError

    def renew():
        while not stop.wait(5):
            try:
                check()
            except QueryCapacityUnavailableError:
                return

    keeper = threading.Thread(target=renew, name="dataset-query-capacity", daemon=True)
    keeper.start()
    try:
        yield check
    finally:
        stop.set()
        keeper.join(timeout=2)
        try:
            client.eval(_RELEASE, 2, *keys, owner)
        except redis.RedisError:
            logger.warning("Query capacity release was unavailable; its lease will expire.")
