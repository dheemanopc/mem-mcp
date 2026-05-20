# Embedding Backfill Runbook

**Runbook:** Embedding backfill worker troubleshooting and manual invocation.

**References:**
- Systemd timer: `/etc/systemd/system/mem-mcp-embedding-backfill.timer`
- Service unit: `/etc/systemd/system/mem-mcp-embedding-backfill.service`
- Code: `src/mem_mcp/jobs/embedding_backfill.py`
- GUIDELINES.md §6.7 (embedding status lifecycle)

---

## How it works

The embedding backfill worker runs **hourly** at `:15` (e.g., 1:15am, 2:15am, ...) via a systemd timer. Each run:

1. Fetches up to 50 candidates (configurable) with `embedding_status IN ('failed_throttled', 'failed_unavailable', 'failed_unknown')`
2. Attempts to embed each with Bedrock (up to 10 concurrent calls)
3. On success: updates row with embedding vector and `embedding_status='ok'`
4. On transient failure (throttle/unavailable): leaves `embedding_status` unchanged (will retry next hour)
5. On validation error (bad input): marks `embedding_status='failed_validation'` and stops (will not retry)

**Goal:** recover failed embeddings due to rate limits or temporary unavailability, without re-attempting invalid input.

---

## Check status

### Is the timer active?

```bash
systemctl status mem-mcp-embedding-backfill.timer
systemctl list-timers mem-mcp-embedding-backfill.timer
```

### Did the last run succeed?

```bash
journalctl -u mem-mcp-embedding-backfill.service -n 50
```

Look for a log line like:
```
embedding_backfill_complete processed=42 succeeded=40 failed_throttled=1 failed_unavailable=0 failed_validation=0 failed_unknown=1
```

### How many candidates are waiting?

```bash
sudo -u memmcp psql mem_mcp -c "
SELECT COUNT(*) as pending_candidates
FROM memories
WHERE embedding IS NULL
  AND embedding_status IN ('failed_throttled', 'failed_unavailable', 'failed_unknown')
  AND deleted_at IS NULL
  AND (expires_at IS NULL OR expires_at > NOW());
"
```

---

## Manual run

To trigger a backfill immediately (e.g., after a Bedrock outage):

```bash
# One-shot
sudo systemctl start mem-mcp-embedding-backfill.service

# Watch output
journalctl -u mem-mcp-embedding-backfill.service -f
```

### With custom settings

```bash
# Set batch size to 100, concurrency to 5
sudo -E bash -c 'set -a && source /etc/mem-mcp/env && set +a && \
  MEM_MCP_EMBEDDING_BACKFILL_BATCH_SIZE=100 \
  MEM_MCP_EMBEDDING_BACKFILL_CONCURRENCY=5 \
  /opt/mem-mcp/venv/bin/mem-mcp-embedding-backfill'
```

---

## Troubleshooting

### Timer not running

```bash
# Check if timer is enabled
systemctl is-enabled mem-mcp-embedding-backfill.timer
# If not:
sudo systemctl enable mem-mcp-embedding-backfill.timer
sudo systemctl start mem-mcp-embedding-backfill.timer
```

### Service exits with error

Check the journal:
```bash
journalctl -u mem-mcp-embedding-backfill.service -n 100 --no-pager
```

Common issues:

- **`DATABASE_URL not set`** — environment file missing or not sourced. Check `/etc/mem-mcp/env`.
- **Connection timeout** — DB is down. Check `systemctl status postgres` and DB connectivity.
- **Bedrock API error** — Bedrock is down or rate-limiting. Backoff is automatic; next run will retry.

### Too many failed_validation rows

If a data corruption or encoding bug is writing invalid UTF-8, you'll see a spike in `failed_validation` during backfill. These rows will NOT retry automatically. Options:

1. **Fix the bug** producing bad input, then revert `embedding_status` manually:
   ```bash
   sudo -u memmcp psql mem_mcp -c "
   UPDATE memories
   SET embedding_status = 'failed_unknown'
   WHERE embedding_status = 'failed_validation'
     AND created_at > NOW() - INTERVAL '1 hour';
   "
   ```

2. **Or delete the bad rows** if they're unrecoverable:
   ```bash
   sudo -u memmcp psql mem_mcp -c "
   DELETE FROM memories
   WHERE embedding_status = 'failed_validation'
     AND created_at > NOW() - INTERVAL '1 day';
   "
   ```

Then re-run backfill.

### Backfill is slow (< 5 rows/sec)

Increase concurrency:
```bash
sudo systemctl set-property mem-mcp-embedding-backfill.service Environment='MEM_MCP_EMBEDDING_BACKFILL_CONCURRENCY=20'
sudo systemctl start mem-mcp-embedding-backfill.service
```

Monitor Bedrock throttle responses. If they increase, reduce concurrency.

---

## Metrics to monitor

(Dashboard / CloudWatch dashboard entry, v2)

- `embedding_backfill_complete` → `succeeded` (target: > 95% of processed)
- `embedding_backfill_complete` → `failed_validation` (target: near zero; spike = bug)
- `embedding_backfill_complete` → `failed_throttled` (target: < 5%; rising = capacity issue)
- Pending candidates count (query above) — should stay < 100 in steady state

---

## Related

- [GUIDELINES.md § 6.7](../GUIDELINES.md#67-embedding-status-lifecycle) — embedding status definitions
- [memory_write tool spec](../MEMORY_MCP_LLD_V1.md#memory.write) — how embedding_status is set on write
- [Bedrock integration spec](../MEMORY_MCP_LLD_V1.md#44-embeddings-via-bedrock-titan-embed-v2) — rate limits and retry strategy
