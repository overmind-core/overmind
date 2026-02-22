from typing import Optional, List, Sequence
from glide import (
    ExpirySet,
    ExpiryType,
    GlideClient,
    GlideClientConfiguration,
    NodeAddress,
    ServerCredentials,
)
from overmind_core.config import settings


_client: Optional[GlideClient] = None


async def get_valkey_client() -> GlideClient:
    """
    Get or create a Valkey client instance.
    Returns a singleton client to reuse connections.
    """
    global _client

    if _client is None:
        config = GlideClientConfiguration(
            addresses=[NodeAddress(settings.valkey_host, settings.valkey_port)],
            database_id=settings.valkey_db,
        )

        if settings.valkey_auth_token:
            config.credentials = ServerCredentials(password=settings.valkey_auth_token)
            config.use_tls = True

        _client = await GlideClient.create(config)

    return _client


async def get_key(key: str) -> Optional[str]:
    """
    Get a value from Valkey by key.

    Args:
        key: The key to retrieve

    Returns:
        The value as a string, or None if the key doesn't exist
    """
    client = await get_valkey_client()
    value = await client.get(key)
    return value.decode("utf-8") if value else None


async def set_key(key: str, value: str, ttl: Optional[int] = None) -> bool:
    """
    Set a key-value pair in Valkey.

    Args:
        key: The key to set
        value: The value to store
        ttl: Optional time-to-live in seconds

    Returns:
        True if successful
    """
    client = await get_valkey_client()

    if ttl:
        expiry_set = ExpirySet(
            expiry_type=ExpiryType.SEC,
            value=ttl,
        )
    else:
        expiry_set = None

    await client.set(key=key, value=value, expiry=expiry_set)

    return True


async def delete_key(key: str) -> bool:
    """
    Delete a key from Valkey.

    Args:
        key: The key to delete

    Returns:
        True if the key was deleted, False if it didn't exist
    """
    client = await get_valkey_client()
    result = await client.delete([key])
    return result > 0


async def delete_keys(keys: Sequence[str]) -> int:
    """
    Delete multiple keys from Valkey in a single operation.

    Args:
        keys: List of keys to delete

    Returns:
        Number of keys deleted
    """
    if not keys:
        return 0

    client = await get_valkey_client()
    result = await client.delete(list(keys))
    return result


async def delete_keys_by_pattern(pattern: str) -> int:
    """
    Delete all keys matching a pattern from Valkey.
    Uses SCAN to safely iterate through keys matching the pattern.

    Args:
        pattern: The pattern to match (e.g., "permissions:user:123:*")

    Returns:
        Number of keys deleted
    """
    client = await get_valkey_client()
    deleted_count = 0
    cursor = "0"

    # Use SCAN to iterate through matching keys
    while True:
        # SCAN returns (cursor, [keys])
        result = await client.scan(cursor, match=pattern, count=100)
        cursor_bytes = result[0]
        keys_list = result[1]

        # Convert cursor to string for comparison
        cursor = (
            cursor_bytes.decode("utf-8")
            if isinstance(cursor_bytes, bytes)
            else str(cursor_bytes)
        )

        # Delete keys if any were found
        if keys_list and isinstance(keys_list, list) and len(keys_list) > 0:
            # Keys are returned as bytes from scan, decode them to strings
            string_keys: List[str] = [
                k.decode("utf-8") if isinstance(k, bytes) else str(k) for k in keys_list
            ]
            deleted = await client.delete(string_keys)  # type: ignore[arg-type]
            deleted_count += deleted

        # cursor is "0" when iteration is complete
        if cursor == "0":
            break

    return deleted_count


async def close_valkey_client():
    """
    Close the Valkey client connection.
    Should be called on application shutdown.
    """
    global _client

    if _client:
        await _client.close()
        _client = None
