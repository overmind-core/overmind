"""Atomic Redis relocation of one validated Celery envelope; never a resend."""

# Push first: even a destination allocation/type error must leave the source
# untouched. All validation runs before either write; Lua excludes consumers
# between the final unacked check and list mutation.
MOVE_EXACT_ENVELOPE = r"""
local count = tonumber(ARGV[1])
local task_id = ARGV[2]
local original = ARGV[3]
local maximum = tonumber(ARGV[4])
local require_absent = ARGV[5] == "absent"
local source_matches = 0
local destination_matches = 0
local source_key = nil
local total = 0
local function message_id(raw)
    local ok, value = pcall(cjson.decode, raw)
    if not ok or type(value) ~= 'table' then return nil end
    if value.headers and value.headers.id then return value.headers.id end
    if value.properties then return value.properties.correlation_id end
    return nil
end
for index = 1, #KEYS - 1 do
    local kind = redis.call('TYPE', KEYS[index]).ok
    if kind ~= 'none' and kind ~= 'list' then
        return redis.error_reply('queue key has unexpected type')
    end
    total = total + redis.call('LLEN', KEYS[index])
end
if total > maximum then return redis.error_reply('queue scan bound exceeded') end
for index = 1, #KEYS - 1 do
    for _, raw in ipairs(redis.call('LRANGE', KEYS[index], 0, -1)) do
        local id = message_id(raw)
        if not id then return redis.error_reply('unrecognised queue envelope') end
        if id == task_id then
            if index <= count then
                source_matches = source_matches + 1
                source_key = KEYS[index]
                if raw ~= original then return redis.error_reply('original envelope changed') end
            else
                destination_matches = destination_matches + 1
            end
        end
    end
end
if require_absent then
    if source_matches + destination_matches ~= 0 then return redis.error_reply('task is still queued') end
else
    if source_matches ~= 1 then return redis.error_reply('expected exactly one original queued task') end
    if destination_matches ~= 0 then return redis.error_reply('task already present in destination') end
end
local unacked = KEYS[#KEYS]
if redis.call('HLEN', unacked) > maximum then return redis.error_reply('unacked scan bound exceeded') end
for _, raw in ipairs(redis.call('HVALS', unacked)) do
    local ok, value = pcall(cjson.decode, raw)
    if not ok or type(value) ~= 'table' then return redis.error_reply('unrecognised unacked envelope') end
    local message = value[1]
    if type(message) == 'string' then
        local parsed, decoded = pcall(cjson.decode, message)
        if not parsed then return redis.error_reply('unrecognised unacked message') end
        message = decoded
    end
    if type(message) ~= 'table' then return redis.error_reply('unrecognised unacked message') end
    local id = message.headers and message.headers.id
    if not id and message.properties then id = message.properties.correlation_id end
    if not id then return redis.error_reply('unrecognised unacked identity') end
    if id == task_id then return redis.error_reply('task is reserved or active') end
end
if require_absent then return 1 end
local pushed = redis.pcall('LPUSH', KEYS[count + 1], original)
if type(pushed) == 'table' and pushed.err then return pushed end
if redis.call('LREM', source_key, 1, original) ~= 1 then
    redis.call('LREM', KEYS[count + 1], 1, original)
    return redis.error_reply('source changed; destination rolled back')
end
return 1
"""


def move_exact_envelope(
    client,
    *,
    source_keys,
    destination_keys,
    unacked_key,
    task_id,
    raw,
    max_scan=100_000,
):
    if not source_keys or not destination_keys or set(source_keys) & set(destination_keys):
        raise ValueError("Source and destination priority queues must be distinct")
    keys = [*source_keys, *destination_keys, unacked_key]
    return client.eval(
        MOVE_EXACT_ENVELOPE,
        len(keys),
        *keys,
        len(source_keys),
        str(task_id),
        raw,
        max_scan,
    )


def assert_task_absent(client, *, queue_keys, unacked_key, task_id, max_scan=100_000):
    if not queue_keys:
        raise ValueError("Every routed queue must be checked")
    keys = [*queue_keys, unacked_key]
    return client.eval(
        MOVE_EXACT_ENVELOPE, len(keys), *keys, 0, str(task_id), "", max_scan, "absent"
    )
