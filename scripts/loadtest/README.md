# Data Workshop load tests

Baseline the Workshop's infrastructure and concurrency: imports (`landing`), agent turns and
cell runs (`interactive`), the read path and live updates (SSE). Traffic looks like the real
clients: MCP `tools/call` (Claude Code, Codex), chunked uploads (`overmind dataset upload --json`) and the Console's REST and SSE calls. Every request sends
`User-Agent: overmind-loadtest/1`.

## Local stack

```sh
# 1. Fake LLM on the host: every agent turn is one status call plus a reply, 2 s per round.
PYTHONPATH=. FAKE_LLM_LATENCY=2 uv run python scripts/loadtest/fake_llm.py &

# 2. Stack with the overlay: LLM calls go to the fake, placeholder platform keys,
#    API as in production (gunicorn, two uvicorn workers), landing at 4 vCPU / 8 GiB.
docker compose -f docker-compose.yml -f scripts/loadtest/compose.loadtest.yml up -d --force-recreate \
  api celery-control-worker celery-io-worker celery-batch-worker celery-landing-worker \
  celery-interactive-worker celery-beat

# 3. Test user, project and project key (written to a git-ignored file).
docker compose exec -T api python - < scripts/loadtest/bootstrap_local.py > .env.loadtest.local

# 4. Fixtures (chat-format JSONL).
uv run python scripts/loadtest/fixtures.py --out scripts/loadtest/.fixtures --sizes 1,10,100,500
```

Return to the normal stack with `docker compose up -d --force-recreate` from a shell that
exports the platform keys.

The local API runs on one host with every other container, so its numbers check the scripts
and show contention; they are not the production baseline. Landing, interactive and sandbox
numbers are closer, because those workers keep their production process counts.

## Running

```sh
uv run python scripts/loadtest/workshop_load.py <experiment> [options] [--local]
```

`--local` adds a server sampler (Postgres, Redis queue depth, docker stats) every 2 s. It reads
the local database directly and refuses any other host. Production runs record the client view;
the server view comes from CloudWatch for the same window.

Each run writes `scripts/loadtest/runs/<experiment>-<UTC time>/result.json` (command, git SHA,
environment, every request, server samples) and `summary.md`.

## Experiments

| ID  | Command                                                                                     | Load                                                      | Question                                                          |
| --- | ------------------------------------------------------------------------------------------- | --------------------------------------------------------- | ----------------------------------------------------------------- |
| E0  | `calibrate --repeats 5 --size 10`                                                           | One user, upload → ready → reads → chat turn → cell run   | Time per stage; the reference for everything else                 |
| E1  | `reads --rps 1,5,10,25,50 --step-seconds 180`                                               | MCP `list`/`inspect`/`query` and REST `rows`/`columns`    | Latency and errors as read rate rises                             |
| E2  | `sse --streams 10,50,100,200 --hold 600`                                                    | Open `/events` streams, with a read probe every 2 s       | Time to first event, ping gaps, drops; does the API still answer? |
| E3  | `burst --parallel 1,2,4,8,16 --size 10`                                                     | Parallel uploads, each followed to ready                  | Landing wait and time; automatic-turn wait and time               |
| E3b | `burst --parallel 1,4 --size 100` (then 500)                                                | Large files                                               | Landing time and memory per MB                                    |
| E4  | `burst --parallel 4,8,16,24,32 --size 1`                                                    | Uploads whose automatic turns fill the interactive slots  | When turns start to queue, and for how long                       |
| E5a | `chat --parallel 4,8,16`                                                                    | One message to each of N idle datasets                    | Turn wait and time under concurrent chat                          |
| E5b | `collide --tries 10`                                                                        | MCP `message_dataset_agent` while the automatic turn runs | Confirms that `dataset_busy` is the per-dataset lock              |
| E6  | `sandbox --parallel 4,8,16 --size 100`                                                      | The same heavy cell run on N datasets                     | Sandbox CPU and memory per slot, rlimit failures                  |
| E7  | `mix --multiplier 10,50,100 --step-seconds 900`                                             | Hackathon-week ratios (`MIX_PER_HOUR`) scaled up          | The highest multiple that meets the SLOs                          |
| E8  | `mix --multiplier 10 --step-seconds 3600`                                                   | Soak                                                      | Memory growth, stuck runs, queue-age trend                        |
| E9  | Manual, local only: `docker compose kill celery-interactive-worker` during E3, then `up -d` | Worker loss                                               | Recovery time of turns and imports                                |

Run order: E0, E1, E2, E3, E6, E4, E5, E7, E8. Read-only experiments come before the ones that
start agent turns. `burst` waits for every load-test dataset to be idle between steps.

## SLOs and stop rules

Baseline SLOs: 5xx below 0.5 %, reads p95 below 1 s, first SSE event below 2 s, a 10 MB upload
ready-to-use (landed) p95 below 60 s, agent-turn queue wait p95 below 30 s.

A step stops early when more than 2 % of requests in the last minute fail, or (with `--local`)
when an import or agent turn has waited more than 5 minutes. In production a person watches
CloudWatch queue age and ECS task counts as well, and stops when a service sits at its maximum
for more than 5 minutes.

## Production

Only with AWS read access, at a quiet time, with a person watching. Use a dedicated project and
a project API key in `.env.loadtest.prod.local` (`LOADTEST_BASE_URL`, `LOADTEST_PROJECT_ID`,
`LOADTEST_API_KEY`; git-ignored), pass `--prod` and leave out `--local`. Agent turns use the
real engine and spend credits; an automatic turn after an upload takes 10-25 minutes, so keep
the upload experiments small.

After each run, pull the server view for its time window (read-only CloudWatch calls):

```sh
uv run python scripts/loadtest/cloudwatch.py scripts/loadtest/runs/<run> --profile <aws profile>
```

It writes `cloudwatch.json` (one-minute series for ECS services, `Overmind/Queues`, RDS and
RDS Proxy, ElastiCache and the ALB) and appends a peak table to `summary.md`. Run folders stay
out of git: they hold production resource names.
