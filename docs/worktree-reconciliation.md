# Reconciliation with the old standalone broker worktree

This note records the read-only comparison between:

- `/home/arend/work/growatt-rtu-broker`
- `/home/arend/work/HA-core/external/growatt-rtu-broker`

The repository under `HA-core/external/growatt-rtu-broker` is the new canonical worktree. This note preserves the relevant context before the old standalone worktree is archived or removed.

## Repository state at comparison time

| Copy | HEAD | HEAD date | Working-tree state |
| --- | --- | --- | --- |
| Standalone | `b36456692540f0e95e1ffca3382aa525ae27e7a6` | 2025-08-29 | 5 modified tracked files |
| `HA-core/external` | `a877ba7f5ccd1b58da3008cc01acc824aafd48fd` | 2025-09-26 | 2 modified tracked files plus research/log artefacts |

The `HA-core/external` worktree is the newer development line and is now canonical. Its checked-out broker implementation contains the standalone broker's core functionality plus later work on framing, Shine reconnects, event routing, alternate TCP access, sniffing, and analysis tooling.

## Standalone changes and disposition

### Incorporated or superseded by the new `external` worktree

- `growatt_broker/broker.py`: the external version supersedes the standalone version. It retains optional Shine handling and hot-plug/reconnect behaviour, and adds improved RTU frame extraction, event-based logging, sniff streaming, and additional TCP listeners.
- The standalone README content is superseded by the broader Modbus Workbench documentation in the external copy.
- The standalone `docker-compose.yml` deletion does not represent functionality to preserve; the external copy contains the newer compose deployment.
- The external copy additionally contains `backend.py`, `cli.py`, the simulator package, tests, register datasets, and analysis tools that are absent from the standalone copy.

### Not identical and still worth remembering

- `.env.example`: the standalone example made Shine optional and used `9600 8E1`; the external example requires Shine and defaults to `115200 8N1`, while allowing per-side overrides.
- `scripts/run-ha.sh`: the standalone local change made Shine optional, conditionally mounted its device, and failed early when the inverter device could not be resolved. The external helper still requires `SHINE_DEV`, mounts it unconditionally, and falls back to the supplied path when resolution fails.

The optional Shine behaviour is present in the external broker's Python entrypoint, but not consistently represented in its deployment helper and example environment. Before the standalone worktree is removed, either port that helper behaviour to the external copy or deliberately choose and document the mandatory-Shine deployment.

## Deletion checklist

The standalone worktree contains no broker source file that was found to be newer or unique compared with the new canonical worktree. It can be archived/removed after:

1. the `.env.example` and `scripts/run-ha.sh` choice above has been made;
2. any hardware-specific serial settings from the standalone copy have been recorded in the live deployment notes; and
3. this reconciliation note is retained in the external repository history.

This note does not authorise deletion by itself; removal remains a separate, deliberate filesystem action.
