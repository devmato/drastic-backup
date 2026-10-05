# Settings and Debug MCP

Open **User menu > Settings** for account password, Recovery Export, and **Debug & MCP**.
Switch between **Dark mode** and **Light mode** directly in the user menu; the choice is
saved in this browser.

## Enable diagnostic access

1. Update the backend first, then the agents. The backend startup runs the `debug_access`
   database migration. Historical recording requires protocol **5**; direct live reads
   and local diagnostic logs require protocol **6**. Agent versions and the latest
   samples are available through MCP.
2. Turn on **Enable diagnostic recording and MCP access** in Settings. This single
   account-wide switch applies to all your agents.
3. Copy the token from its one-time dialog into your MCP client's secret storage.
4. Connect to the displayed `/mcp/debug` URL using **Streamable HTTP** and
   `Authorization: Bearer <token>`. Configure `DRASTIC_PUBLIC_URL` when the backend's
   externally reachable URL differs from its request URL.
5. Allow roughly 20 seconds for connected agents to start recording and send their
   first sample, then reproduce the problem. Refresh an already-open
   operation page if recording was enabled in another browser tab.

The token is stored only as a SHA-256 hash. **Renew token** invalidates the previous
token. Disabling access or changing the account password revokes it immediately.
Agents stop additional collection within 60 seconds even if the control connection
is lost. Protocol 6 agents also receive the account-wide setting during sync and
through the existing command connection, independently of successful report uploads.
Normal operation logs are independent of this setting.

For example, an OpenCode remote MCP configuration can reference an environment secret:

```json
{
  "mcp": {
    "drastic-debug": {
      "type": "remote",
      "url": "https://backup.example.com/mcp/debug",
      "headers": {
        "Authorization": "Bearer {env:DRASTIC_DEBUG_TOKEN}"
      }
    }
  }
}
```

## Available information

Start with `debug_context`, then `list_operations` and `inspect_operation`.

| Tool | Content |
| --- | --- |
| `debug_context` | Backend version/source fingerprint and current system counters, protocol, agents, repositories, recording coverage and retention |
| `inspect_agent` | Latest agent/runtime sample, version/source fingerprint, process counters, admitted work and configured jobs |
| `inspect_configuration` | Owned job, repository, schedule or retention; explicit fields, without credential settings |
| `list_operations` | Owned operations, optionally filtered by agent/job, with a pagination cursor |
| `inspect_operation` | State, progress and up to 100 artifacts |
| `get_operation_logs` | Existing operation logs in sequence order, bounded per entry |
| `get_diagnostics` | Recorded events filtered by agent, operation or component; cursor pagination |
| `get_diagnostic_event` | One complete event with its bounded payload (up to 16 KiB) |
| `get_agent_debug` | Direct read from an online agent: `runtime`, `logs` or `threads` |

### When progress or reports stop arriving

Use `get_agent_debug(agent_id, section)`:

- **`runtime`** reads the current scheduler/report-delivery phase, last send error,
  queued reports, running operations and existing process/I/O counters.
- **`logs`** reads recent, redacted local application and process-error logs.
- **`threads`** shows thread stack locations without arguments or variable contents.

The command runs outside the report queue and backup executor. Busy operation locks
return `lock_busy` rather than blocking the read. Responses identify live data and its
capture time. A three-second command timeout, an offline agent or an older protocol
returns an explicitly labelled cached sample, when available. Use the existing
configuration and operation tools for the remaining context.

Local logs use Python's rotating file handler (`diagnostics.log` and `.1` in the agent
data directory), approximately 1 MiB per file, mode `0600`. They are written only while
diagnostics is active, are redacted before storage, and remain readable after a restart.
Unlike server event history, these files are bounded by rotation rather than age.
Reads return at most 200 entries and 48 KiB of recent log data; live responses are
limited to 64 KiB and explicitly mark truncation. No arbitrary paths, shell commands,
SQL queries or stack locals can be requested.

Additional events include effective backup job/run/retention configuration, phase
changes, command receipt, process start/exit and elapsed time, Proxmox disk selection
and `vzdump` output. Every 10 seconds, active operation reports are marked for a
diagnostic snapshot and agent counters are sampled. The backend stores the selected
reports instead of receiving a second copy of their state. Events carry source and
server receipt times; process durations use a
monotonic clock and do not depend on matching clocks between machines.

Linux samples contain cumulative host CPU, memory, disk and network counters, plus
`/proc` counters for the agent and directly managed Restic/producer processes. Other
platforms or restricted containers may return missing counters. These are **host or
process measurements**, not an attribution of host activity to an individual VM.
Source fingerprints identify differing installed Python code even when development
builds share the same package version; they are not Git commit IDs.

The managed repository proxy aggregates completed requests, error counts, total/max
duration, declared upload lengths and streamed response bytes per agent/repository.
Concurrent request durations may overlap. Custom repositories bypass this proxy.
An enabled browser records bounded socket/operation-update and reload events, including
its frontend version/build timestamp, at most once per event type every 10 seconds.

## Limits and access boundaries

- Read-only access is restricted to the token owner's data. MCP cannot run commands,
  start backups, restore files, change settings or perform arbitrary SQL/file reads.
- Structured secret fields are masked, URLs and recognizable credentials are redacted,
  and the agent replaces known credentials in captured output. Diagnostic text can still
  contain sensitive filenames, hostnames and application output; it is not a public log.
- Events are limited to 16 KiB. Oversized payloads retain a redacted preview and are
  explicitly marked truncated. Some system counters are sampled as bounded text.
- Retention keeps up to **14 days or 5,000 events per user**, whichever is smaller.
  Cleanup runs every 10 seconds after the backend's first HTTP request, including while
  access is disabled. It resumes when the backend serves requests after a restart.
- Additional events use an internal report with at most **256 log entries in memory**.
  The existing report queue submits up to four bounded entries per message (below
  256 KiB), acknowledges their sequence numbers and retries failed sends. Historical
  diagnostics reuse this queue. Internal reports do not appear as backup
  operations. Normal operation logs are read from their existing history, not copied
  into the diagnostic history. Overflow is reported in `dropped_events`; a restart
  loses unsent diagnostic entries. `boot_id` helps identify
  host restarts. A stale or absent sample is not proof of a hung backup.
- Recording is not retroactive. Configuration events exist only for runs started while
  enabled. Data removed by retention or lost during an outage cannot be reconstructed.
- A complete backend/database outage requires external service logs; MCP depends on
  that backend and database being reachable.

## Verification

The endpoint uses Flask's existing HTTP service and the JSON-only, stateless subset of
MCP Streamable HTTP. It needs no additional runtime service or dependency. Optional
compatibility testing with the official SDK:

```sh
# From apps/backend
uv run --with mcp --with httpx python -m pytest tests/test_debug_access.py
```
