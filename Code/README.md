# Network Telemetry & Real-Time Network Performance Monitoring System

> **Status: feature-complete for an academic/demo deployment.**
> 112 automated tests passing (0 failed, 0 skipped) at time of writing.
>
> **This project targets real network telemetry from physical routers/switches (SNMP is the PRIMARY data source).**
> The simulator exists only as a secondary source for development, testing, and demos when authorized
> physical device access isn't available — see [§9 SNMP Mode](#9-snmp-mode--real-devices-primary) and
> [§17 Honesty Statement](#17-honesty-statement--what-is-and-isnt-verified).

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture](#2-architecture)
3. [Technologies Used](#3-technologies-used)
4. [Project Structure](#4-project-structure)
5. [Installation (Windows PowerShell)](#5-installation-windows-powershell)
6. [Configuration](#6-configuration)
7. [Running with Docker](#7-running-with-docker)
8. [Running without Docker](#8-running-without-docker)
9. [SNMP Mode — Real Devices (PRIMARY)](#9-snmp-mode--real-devices-primary)
10. [Simulation Mode — Secondary/Demo](#10-simulation-mode--secondarydemo)
11. [Metric Calculation Engine](#11-metric-calculation-engine)
12. [REST API](#12-rest-api)
13. [Alert Engine](#13-alert-engine)
14. [Grafana Dashboards](#14-grafana-dashboards)
15. [Testing](#15-testing)
16. [RFC 9232 Alignment](#16-rfc-9232-alignment)
17. [Honesty Statement — What Is and Isn't Verified](#17-honesty-statement--what-is-and-isnt-verified)
18. [Troubleshooting](#18-troubleshooting)
19. [Final Demonstration Procedure](#19-final-demonstration-procedure)
20. [Future Enhancements](#20-future-enhancements)

---

## 1. Project Overview

A modular system that collects network performance telemetry (bandwidth, latency, packet
stats, device availability) from routers/switches over **SNMP** and **ICMP**, transports it to
a central backend over **gRPC**, stores it in **PostgreSQL**, visualizes it in **Grafana** in
near real time, and raises **threshold-based alerts** with deduplication and recovery.

Conceptually aligned with **RFC 9232 (Network Telemetry)** — see §16. This is *not* a claim of
formal RFC compliance.

## 2. Architecture

```
Physical Router/Switch  (PRIMARY, authorized only)  ─┐
                                                       ├─ MetricSource (pluggable)
Simulator (SECONDARY: dev/test/demo)  ────────────────┘        +
                                                        LatencySource (pluggable: ICMP or simulated)
                        │
                        ▼
              Telemetry Collector (Python, collector/main.py)
                        │  Phase 3 metric calculation (bandwidth/utilization from consecutive polls)
                        ▼
                gRPC + Protobuf  (proto/telemetry.proto, TelemetryBatch, batched per poll)
                        │
                        ▼
              Central Backend (backend/main.py: FastAPI + gRPC server, one process)
                        │
           ┌────────────┼───────────────┐
           ▼            ▼               ▼
      PostgreSQL   Alert Engine     REST API (FastAPI)
     (persistence)  (threshold/           │
                     dedup/recovery)      ▼
           │                          External clients /
           ▼                          CSV export
        Grafana (reads PostgreSQL directly, no custom plugin)
```

**Key design decision (carried through every phase):** the collector never talks to SNMP or
the simulator directly — both are `MetricSource` implementations behind one interface
(`collector/sources/base.py`), and both real ICMP and simulated latency are `LatencySource`
implementations behind another (`collector/ping/base.py`). This is what makes the system fully
testable without a live device and lets real hardware be enabled via config, not a rewrite.

## 3. Technologies Used

| Layer | Technology | Why |
|---|---|---|
| Metric collection | SNMPv2c (`pysnmp`) | Standard, vendor-agnostic device polling — **primary data source** |
| Latency | ICMP ping (OS `ping` via subprocess) | Direct round-trip measurement, independent of SNMP; no raw-socket admin privileges needed on Windows or Linux |
| Secondary source | Custom simulator | Deterministic, scenario-driven counters for dev/test/demo without hardware |
| Transport | gRPC + Protobuf | Efficient, typed, streaming-capable collector→backend link with batching |
| Backend API | FastAPI | Async, auto-validated, auto-documented REST (`/docs`) |
| Storage | PostgreSQL (SQLAlchemy ORM) | Reliable, indexable, well-supported in Docker; tests use SQLite in-memory |
| Visualization | Grafana | Native Postgres datasource, dashboard templating, no custom plugin |
| App observability | OpenTelemetry (optional) | Traces for the monitoring app itself, not network metrics |
| Orchestration | Docker Compose | One-command reproducible startup |
| Testing | pytest | 112 tests: unit, API, and real-socket integration |

## 4. Project Structure

```
network-telemetry/
├── collector/
│   ├── sources/base.py       # MetricSource ABC + RawDeviceSample/InterfaceSample contract
│   ├── snmp/                  # Real SNMPv2c polling (PRIMARY): oids.py, transport.py, credentials.py, snmp_source.py
│   ├── simulator/               # Secondary source: scenarios.py, scenario_controller.py, simulator_source.py, latency_source.py
│   ├── ping/                     # LatencySource ABC + real ICMP (icmp_source.py, Windows+Linux)
│   ├── metrics/                    # Pure bandwidth/utilization engine + composition layer
│   ├── grpc_client/                  # gRPC client (retry/backoff) + protobuf batch builder
│   ├── config/                        # devices.yaml loader
│   └── main.py                         # Collector entrypoint / poll loop
├── backend/
│   ├── api/                    # FastAPI app + routers (health/devices/metrics/alerts) + repository layer
│   ├── grpc_server/              # gRPC ingestion server + servicer (thin, delegates to services/)
│   ├── models/                     # SQLAlchemy ORM (devices/interfaces/telemetry_metrics/alerts) + Pydantic schemas
│   ├── services/                     # ingestion_service.py (business logic), query_service.py (API/CSV logic)
│   ├── alerts/                         # Threshold engine with dedup + recovery
│   ├── database/                        # Engine/session factory (env-var driven, Postgres or SQLite)
│   ├── telemetry/                         # Optional OpenTelemetry setup
│   └── main.py                             # Backend entrypoint (FastAPI + gRPC server, one process)
├── common/                    # Generated protobuf/gRPC stubs (telemetry_pb2*.py) — never hand-edited
├── proto/telemetry.proto      # Protobuf source of truth
├── grafana/
│   ├── dashboards/              # 4 dashboard JSON files (overview, bandwidth, latency, alerts)
│   └── provisioning/              # datasource + dashboard-provider YAML
├── tests/
│   ├── collector/                # SNMP, simulator, ICMP, bandwidth engine, config
│   ├── backend/                    # ingestion service, alert engine, gRPC servicer, FastAPI routes
│   ├── grpc/                         # gRPC client (serialization, retry, backoff)
│   └── integration/                    # real-socket end-to-end pipeline tests
├── config/                    # collector.yaml, devices.yaml, thresholds.yaml
├── docker/                    # backend.Dockerfile, collector.Dockerfile
├── docker-compose.yml
├── .env.example
├── requirements.txt
└── README.md
```

## 5. Installation (Windows PowerShell)

```powershell
Expand-Archive network-telemetry.zip -DestinationPath . -Force
cd network-telemetry
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pytest tests\ -v
```

Expected: `112 passed`.

## 6. Configuration

Three YAML files under `config/` (never contain secrets):

| File | Purpose |
|---|---|
| `collector.yaml` | `mode: SNMP` (primary) or `mode: SIMULATION` (secondary/default); poll interval; gRPC target; latency mode |
| `devices.yaml` | Device inventory; **`authorized: true` required per-device** before SNMP mode will poll it |
| `thresholds.yaml` | Alert thresholds consumed by the backend alert engine |

Secrets — DB password, SNMP community strings, Grafana admin password — go in `.env` (copy
from `.env.example`). **Never hardcoded, never in YAML, never committed.**

```powershell
Copy-Item .env.example .env
notepad .env   # fill in POSTGRES_PASSWORD, GRAFANA_ADMIN_PASSWORD, etc.
```

## 7. Running with Docker

```powershell
docker compose up --build
```

This starts, in dependency order (`postgres` → healthy → `backend` → healthy → `collector`;
`postgres` → healthy → `grafana`):

| Service | Port | Purpose |
|---|---|---|
| `postgres` | 5432 | Persistent storage |
| `backend` | 8000 (REST + Swagger `/docs`), 50051 (gRPC) | FastAPI + gRPC ingestion server, one process |
| `collector` | — | Polls devices per `config/collector.yaml`, ships telemetry to `backend:50051` |
| `grafana` | 3000 | Dashboards, auto-provisioned Postgres datasource + 4 dashboards |

Open `http://localhost:3000` (Grafana), `http://localhost:8000/docs` (Swagger UI).

To stop: `docker compose down` (add `-v` to also drop the Postgres/Grafana volumes).

## 8. Running without Docker

Requires a running PostgreSQL instance reachable via the `POSTGRES_*` env vars (or set
`DATABASE_URL` directly).

```powershell
# Terminal 1: backend (FastAPI + gRPC server)
.venv\Scripts\Activate.ps1
$env:POSTGRES_HOST="localhost"; $env:POSTGRES_PASSWORD="your_password"
python -m backend.main

# Terminal 2: collector (defaults to SIMULATION mode — see config/collector.yaml)
.venv\Scripts\Activate.ps1
python -m collector.main
```

## 9. SNMP Mode — Real Devices (PRIMARY)

`collector/snmp/` implements real SNMPv2c polling via `SNMPSource`.

**Data collected per device:** `sysUpTime` (also the liveness check), and per discovered
interface: name, operational status, speed, 64-bit HC byte counters in/out, packet counters
in/out, error counters in/out. See `collector/snmp/oids.py` for the exact IF-MIB/ifXTable OIDs
and why each was chosen (e.g. `ifHCInOctets` over `ifInOctets` to avoid 32-bit wraparound).

**Interface discovery is dynamic** — every poll walks `ifName`, so added/removed interfaces
need no config change.

**To configure a real, authorized device:**

1. In `config/devices.yaml`, set `authorized: true` for that device and its correct `ip`.
2. In `.env`, set `SNMP_COMMUNITY_<DEVICE_ID>` (e.g. `SNMP_COMMUNITY_ROUTER_01=public`),
   using the device's actual read-only community string. **Never write this in YAML.**
3. In `config/collector.yaml`, set `mode: SNMP`.
4. Restart the collector.

Security gates, enforced in layers:
- `SNMPSource.poll_device()` raises `PermissionError` and refuses to send a single packet if
  `authorized` is not `true` for that device — this cannot be bypassed by mode alone.
- Community strings are read exclusively from environment variables (`collector/snmp/credentials.py`).
- SNMPv3 fields are already modeled in `SNMPCredentials`; `load_snmp_credentials()` raises a
  clear `NotImplementedError` for `SNMP_VERSION=v3` today rather than silently proceeding.

**Testability without a live device:** `SNMPSource` depends on the `SnmpTransport` abstraction,
not on `pysnmp` directly — tests inject a `FakeSnmpTransport` with canned responses
(`tests/collector/test_snmp_source.py`), so discovery, parsing, and error handling are fully
verified with zero network access.

> **No physical device (including any IITGN device) has been tested against this
> implementation.** No IP address or credential for any real device is hardcoded anywhere in
> this repository. See §17 for the full honesty statement.

## 10. Simulation Mode — Secondary/Demo

`collector/simulator/` implements the same `MetricSource`/`LatencySource` contracts as the real
sources, producing **raw cumulative counters** (not precomputed bandwidth) so the Phase 3
calculation engine is genuinely exercised, not bypassed.

Six deterministic scenarios (`collector/simulator/scenarios.py`), settable via
`ScenarioController.set_scenario()`:

| Scenario | Effect |
|---|---|
| `NORMAL` | ~10-20% utilization, low latency |
| `HIGH_BANDWIDTH` | ~80-90% utilization — crosses the default 80% threshold |
| `HIGH_LATENCY` | ~150ms avg latency — crosses the default 100ms threshold |
| `PACKET_LOSS` | ~15% loss — crosses the default 5% threshold |
| `DEVICE_DOWN` | Device reported unreachable; counters frozen |
| `RECOVERY` | Returns to normal traffic/latency levels |

This is the default (`mode: SIMULATION` in `config/collector.yaml`) so the whole system is
demoable with zero hardware.

## 11. Metric Calculation Engine

`collector/metrics/bandwidth.py` — pure math, no SNMP/telemetry types, independently
unit-tested with plain numbers:

```
bandwidth_bps   = (delta_bytes * 8) / elapsed_seconds
utilization_pct = (bandwidth_bps / interface_speed_bps) * 100
```

- `elapsed_seconds` is always the *actual measured* time between two samples' timestamps,
  never the configured `collection_interval`.
- **Reset vs. wraparound**: when `current < previous`, the engine computes the
  wraparound-corrected delta and checks whether the *implied bandwidth* is plausible (default
  ceiling 200 Gbps). Plausible → genuine wraparound. Implausible → counter reset (device
  reboot) — returned as invalid, never a fabricated spike.
- Invalid inputs (negative counters, non-positive elapsed time, zero/negative interface speed)
  are rejected with a specific reason string.

`collector/metrics/interface_metrics.py` composes this with `InterfaceSample`/`RawDeviceSample`:
matches interfaces between two polls by `if_index`, computes elapsed time from real timestamps.

## 12. REST API

FastAPI, auto-documented at `http://localhost:8000/docs`.

| Endpoint | Purpose |
|---|---|
| `GET /health` | Liveness check |
| `GET /devices` | List all known devices |
| `GET /devices/{device_id}` | One device (404 if unknown) |
| `GET /metrics` | Historical query — filters: `device_id`, `interface`, `metric_type`, `start`, `end`, `limit`, `offset` |
| `GET /metrics/{device_id}` | Same, pre-scoped to one device |
| `GET /metrics/{device_id}/bandwidth` | Bandwidth/utilization metric types only |
| `GET /metrics/{device_id}/latency` | Latency/packet-loss metric types only |
| `GET /metrics/export` | Streamed CSV, same filters, proper `Content-Disposition` header |
| `GET /alerts` | All alerts (most recent first) |
| `GET /alerts/active` | Only `ACTIVE` alerts |

Layering: **router → service layer → repository layer** (`backend/api/*_router.py` →
`backend/services/query_service.py` → `backend/api/repository.py`) — no raw SQL in route
functions.

## 13. Alert Engine

Threshold-based, configured via `config/thresholds.yaml`. Alert types: `HIGH_BANDWIDTH`,
`HIGH_LATENCY`, `PACKET_LOSS`, `DEVICE_DOWN`.

**Deduplication**: a dedup key of `(device_id, interface_name, alert_type, metric_type)` is
checked before creating a new alert row. A metric breaching threshold for 20 consecutive polls
produces exactly **one** `ACTIVE` alert row, not 20 — verified by
`test_sustained_breach_over_20_polls_produces_exactly_one_active_alert`.

> **Note on `metric_type` in the dedup key**: `utilization_in` and `utilization_out` both map
> to `HIGH_BANDWIDTH`. Early in this implementation, dedup was keyed only on
> `(device_id, interface_name, alert_type)`, which meant evaluating a non-breaching outbound
> reading right after a breaching inbound one on the same interface would incorrectly *resolve*
> the alert the inbound reading had just created. This was caught by the real-socket
> integration test (`tests/integration/test_end_to_end.py`) using two live directions on the
> same simulated interface, and fixed by adding `metric_type` to the key — see
> `test_in_and_out_utilization_breaches_are_tracked_independently` for the regression test.

**Recovery**: the next reading below threshold transitions the matching `ACTIVE` alert to
`RESOLVED` with a `resolved_at` timestamp.

**Device availability rule** (exact, documented): the collector sends a `device_reachable`
metric (`1.0` or `0.0`) every poll, derived directly from whether the configured
`MetricSource.poll_device()` call succeeded (SNMP timeout / simulated `DEVICE_DOWN` →
`0.0`). The alert engine treats `device_reachable == 0.0` as a `DEVICE_DOWN` breach and
`!= 0.0` as recovery, using the same dedup/resolve mechanism as threshold alerts — a device
flapping between reachable/unreachable every poll still produces one alert per outage, not one
per poll.

## 14. Grafana Dashboards

Auto-provisioned (`grafana/provisioning/`) against a PostgreSQL datasource pointed at the same
database the backend writes to — no custom plugin, Grafana's built-in Postgres datasource
queries `devices`/`telemetry_metrics`/`alerts` directly.

| Dashboard | Contents |
|---|---|
| **Network Overview** | Total/online/offline devices, active alert count, current network load gauge, device table |
| **Bandwidth** | RX/TX bandwidth & utilization over time, top bandwidth-consuming interfaces; device/interface template variables |
| **Latency** | Avg/min/max latency over time, packet loss, devices with highest latency; device template variable |
| **Alerts** | Active alerts table, alert history, breakdowns by type and severity |

All dashboards refresh every 5-10s for near-real-time monitoring.

## 15. Testing

```powershell
python -m pytest tests\ -v
```

**112 passed, 0 failed, 0 skipped** (verified in this implementation pass).

| Area | Test file(s) | What's covered |
|---|---|---|
| Contracts | `test_phase1_contracts.py` | `MetricSource`/`LatencySource` ABCs |
| SNMP | `test_snmp_oids.py`, `test_snmp_credentials.py`, `test_snmp_source.py` | OID math, env-var credentials, mocked discovery/polling, authorization gate |
| Bandwidth engine | `test_bandwidth.py`, `test_interface_metrics.py` | Normal/zero/high traffic, intervals, reset, wraparound, invalid inputs, utilization edge cases |
| ICMP latency | `test_icmp_source.py` | Windows & Linux ping parsing, timeout, partial loss, unreachable, malformed output, missing binary, permission error |
| Simulator | `test_simulator.py` | All 6 scenarios, monotonic counters, per-device overrides, contract conformance |
| gRPC client | `test_grpc_client.py` | Batch building, retry/backoff, backend-unavailable handling |
| gRPC server | `test_grpc_servicer.py` | Ingestion, invalid records, empty batch |
| Database/ingestion | `test_ingestion_service.py` | Device/interface upsert, metric storage, alert triggering |
| Alert engine | `test_alert_engine.py` | Breach, **20-poll dedup**, recovery, device down/recovery, **in/out direction independence (regression)** |
| REST API | `test_api.py` | All endpoints, filtering, 404s, CSV export content/headers |
| **Integration** | `test_end_to_end.py` | **Real gRPC socket**: simulator → collector → gRPC → DB → alert ACTIVE → RESOLVED; device down → recovery |

## 16. RFC 9232 Alignment

Conceptually aligned with RFC 9232's Network Telemetry architecture — **not a claim of formal
compliance**:

| RFC 9232 concept | This project |
|---|---|
| Network Device / Telemetry Source | Physical router/switch (SNMP), or the simulator |
| Telemetry Collector | `collector/` — polls sources, standardizes, forwards |
| Transport/Encoding | gRPC + Protobuf (`proto/telemetry.proto`) |
| Consumer/Analytics | `backend/` — FastAPI + PostgreSQL |
| Historical storage | PostgreSQL |
| Visualization | Grafana |

## 17. Honesty Statement — What Is and Isn't Verified

**Verified locally in this sandbox (no Docker available in this environment):**
- All 112 pytest tests pass, including a real-socket gRPC integration test (loopback TCP, not
  in-process function calls) covering simulator → collector → gRPC → SQLite → alert
  create/resolve.
- The collector starts in SIMULATION mode, loads all 4 configured devices, and handles a
  completely unreachable backend gracefully (no crash, logged retries exhausted).
- The backend's FastAPI app and gRPC server both start and respond correctly (verified against
  SQLite standing in for PostgreSQL, since no Docker/PostgreSQL instance was available here).
- `docker-compose.yml` is syntactically valid YAML and passes structural checks (all 4 required
  services present, Dockerfiles referenced exist, dependency/healthcheck ordering correct,
  volumes declared).
- All 4 Grafana dashboard JSON files and both provisioning YAML files are syntactically valid.

**NOT verified (requires infrastructure unavailable in this sandbox):**
- `docker compose up --build` has **not** been run end-to-end (no Docker daemon in this
  environment). The compose file, Dockerfiles, and their structure have been validated as
  thoroughly as possible without actually building images.
- A real PostgreSQL instance has not been exercised — SQLAlchemy code paths are identical for
  SQLite and PostgreSQL, and integration tests use SQLite as a stand-in, but Postgres-specific
  behavior (connection pooling under load, exact SQL dialect quirks) is unverified.
- Grafana has not actually been run and the dashboards have not been visually confirmed to
  render correctly against live data — only their JSON/YAML syntax and structure are validated.
- **No physical router or switch (including any IITGN device) has been polled.** SNMP mode is
  built and unit-tested against mocked responses only. No real device IP or credential is
  hardcoded anywhere in this project.

## 18. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `pytest` fails with `no such table` | Only relevant if you construct your own SQLite session factory outside the provided fixtures — use `create_session_factory("sqlite:///:memory:")`, which now uses `StaticPool` to keep the in-memory DB alive across threads |
| Collector logs `Backend unavailable after N attempts` | Backend isn't running yet, or `grpc.backend_host`/`backend_port` in `collector.yaml` don't match. In Docker Compose this is expected briefly at startup (retry/backoff handles it) |
| `SNMPConfigError: Missing environment variable SNMP_COMMUNITY_...` | Set that variable in `.env` — never in `devices.yaml` |
| `PermissionError: ... not marked authorized:true` | Expected safety behavior — set `authorized: true` in `devices.yaml` for that device only once you have permission to poll it |
| Grafana panels show "no data" | Confirm the collector is actually running and sending data (check backend logs), and that the Postgres datasource's `${GF_TELEMETRY_DB_*}` env vars match your `.env` — some Grafana versions require literal values instead of env-var expansion in provisioning files |
| `docker compose up` fails on `POSTGRES_PASSWORD must be set` | Copy `.env.example` to `.env` and fill in real values — compose deliberately fails closed rather than defaulting to an empty password |
| Windows: ICMP latency always shows unreachable | Confirm `ping` is on `PATH` (`ping 8.8.8.8` in PowerShell) and that no firewall rule blocks outbound ICMP from Python |

## 19. Final Demonstration Procedure

**Demo 1 — Simulation mode (no hardware required):**
```powershell
docker compose up --build
```
Open `http://localhost:3000` (Grafana) and `http://localhost:8000/docs` (API). Devices, live
bandwidth, and live latency should populate within ~10-20 seconds (default `collection_interval: 10`).

**Demo 2 — Trigger and resolve an alert:**
The simulator defaults to `NORMAL`. To demonstrate `HIGH_BANDWIDTH` → alert → resolution without
code changes, use a short Python snippet against a running collector process's
`ScenarioController` (or extend `collector/main.py` with a small admin hook — noted as a future
enhancement, not implemented, since the spec did not require a live scenario-switching API).
Programmatically, `tests/integration/test_end_to_end.py` demonstrates this exact flow
end-to-end: `HIGH_BANDWIDTH` scenario → `ACTIVE` alert in Postgres → visible via
`GET /alerts/active` → `NORMAL` scenario → `RESOLVED`.

**Demo 3 — Historical query + CSV export:**
```
GET http://localhost:8000/metrics?device_id=router-01&metric_type=bandwidth_in
GET http://localhost:8000/metrics/export?device_id=router-01
```

**Demo 4 — Real SNMP device (once authorized):** follow §9 exactly, then set
`mode: SNMP` and restart the collector.

## 20. Future Enhancements

- SNMPv3 (fields already modeled in `SNMPCredentials`; wiring is the remaining work).
- A live scenario-switching endpoint/CLI for the simulator during a demo, rather than editing
  test code to reproduce the flow.
- TimescaleDB/InfluxDB as an alternative to plain PostgreSQL at larger scale.
- A durable queue between collector and backend for extended gRPC outages (current retry/backoff
  drops a batch only after exhausting configured retries).
- Alert notification hooks (webhook/email).
- Hysteresis/debounce band on alert recovery to reduce flapping right at the threshold boundary.
