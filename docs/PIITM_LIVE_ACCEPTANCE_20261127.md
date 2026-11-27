# PiITM live deployment — 2026-11-27

Evidence label: `PROVEN_LIVE` for the initial read-only acceptance window on
this installation. Home Assistant and Growatt portal observation by the user
remains in progress; this is not a claim of full post-deployment acceptance.

## Candidate

- source branch: `codex/fix-rtu-frame-scan-latency`
- source commit: `f01d7288ef8e86e6b3ba5c356e9e9d2966788fc8`
- pushed release tag: `live-broker-20261127-f01d728`
- Docker image tag: `growatt-rtu-broker:live-f01d728`
- image ID: `sha256:ef8b73753be5d34bf91c2575468779b801fb42c8b36e3b516858ef5ff5a07f74`
- previous live image retained for rollback: `growatt-rtu-broker:live-0bb7e65`
- previous live image ID: `sha256:187bc30f810394f129970afb47ea9ce58999236fa63662ec6ec2225e18fdd6bc`
- installation config SHA-256: `49510df7d430faf4123bcc5df1d784807249de6643698b293d00e97b639c080c`
- mode and write policies: preserved from the prior `cache+shine` installation
- live listeners: TCP `5020`, development TCP `5021`, sniffer `5700`

The config remains under `/share` and is mounted read-only from the host-side
path `/mnt/data/supervisor/share/growatt-broker-live-config-f01d728/installation.json`.
The container owns `/dev` and uses host networking, matching the previous
runtime settings.

## Offline and target checks

- `pytest`: 141 passed; 136 `pytest-asyncio` deprecation warnings under Python
  3.14.
- `ruff check .`: passed.
- Black checks passed for the changed Python files. Four unrelated baseline
  files still fail `black --check .`.
- The RPi Docker build and CLI startup smoke test succeeded.
- Read-only FC03, holding registers 0–1, returned `[1, 401]` through both
  production port 5020 and development port 5021.
- The broker reported zero restarts and no traceback, critical error, or
  unhandled exception in the first three-minute log check.
- The production read was served from cache; background RTU refresh activity
  remained visible in the broker log.

The acceptance probes sent no Modbus writes. Existing write policies were
preserved, so this does not validate write behavior. The first bind-mount
attempt used `/share/...` as a Docker source and failed because the Docker
daemon resolves source paths in the host namespace. The prior container was
restored immediately; the candidate was then started using the host path above.

## Rollback

The previous live container is retained as
`growatt-broker-ha3d-cache-shine-rollback-0bb7e65-20261127`. To restore it:

```bash
docker stop growatt-broker-ha3d-cache-shine
docker rename growatt-broker-ha3d-cache-shine growatt-broker-ha3d-cache-shine-failed-f01d728
docker rename growatt-broker-ha3d-cache-shine-rollback-0bb7e65-20261127 growatt-broker-ha3d-cache-shine
docker start growatt-broker-ha3d-cache-shine
```

The previous image tag and configuration are retained. The new image is
running as `growatt-broker-ha3d-cache-shine` with Docker image ID
`sha256:ef8b73753be5d34bf91c2575468779b801fb42c8b36e3b516858ef5ff5a07f74`.

## Image cleanup

After the candidate passed the initial read-only checks, 64 stopped broker
containers, 71 older unique broker image IDs and one untagged historical broker
image were removed. The candidate image, the previous live image/container,
unrelated Docker images, and Docker build cache were retained. Docker reported
total image storage decreasing from 7.752 GB to 6.558 GB; this total includes
images unrelated to the broker.
