"""Broker CLI selecting backend implementation.

Subcommands:
  run      -> normal operation (dataset mode for now)
  capture  -> like run but logs every op to JSONL via CaptureBackend

Examples:
  growatt-broker run --mode dataset --dataset datasets/min_6000xh_tl.json
  growatt-broker capture --mode dataset --dataset datasets/min_6000xh_tl.json --out session.jsonl
"""
from __future__ import annotations
import argparse, asyncio, sys
from pathlib import Path
from .backend import DatasetBackend, CaptureBackend, Backend, LiveSerialBackend

# NOTE: LiveSerialBackend is not implemented yet; selecting --mode live will error out.

async def _demo_poll(backend: Backend, duration: int) -> None:
    """Placeholder polling loop (until real Modbus TCP server integration)."""
    for _ in range(duration):
        await backend.read_input(1, 0, 4)
        await backend.read_holding(1, 30, 2)
        await asyncio.sleep(1)


def _add_common_backend_args(ap: argparse.ArgumentParser):
    ap.add_argument("--mode", choices=["dataset", "live"], default="dataset",
                    help="Backend mode (live not yet implemented)")
    ap.add_argument("--dataset", help="Path to dataset JSON (required for dataset mode)")
    ap.add_argument("--mutate", action="store_true", help="Enable simple value mutation in dataset mode")
    ap.add_argument("--duration", type=int, default=5, help="Demo runtime in seconds (until server exists)")


def _build_backend(args) -> Backend:
    if args.mode == "live":
        print("[ERROR] live mode not implemented yet", file=sys.stderr)
        sys.exit(2)
    if args.mode == "dataset":
        if not args.dataset:
            print("[ERROR] --dataset is required for dataset mode", file=sys.stderr)
            sys.exit(2)
        return DatasetBackend(args.dataset, mutate=args.mutate)
    print("[ERROR] unknown mode", file=sys.stderr)
    sys.exit(2)


async def main_async(argv=None):
    parser = argparse.ArgumentParser(prog="growatt-broker", description="Growatt RTU Broker prototype")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # run command
    ap_run = sub.add_parser("run", help="Run broker backend (demo loop)")
    _add_common_backend_args(ap_run)

    # capture command
    ap_cap = sub.add_parser("capture", help="Run backend and capture all ops to JSONL")
    _add_common_backend_args(ap_cap)
    ap_cap.add_argument("--out", default="session.jsonl", help="Capture JSONL output path")

    args = parser.parse_args(argv)

    backend = _build_backend(args)

    if args.cmd == "capture":
        backend = CaptureBackend(backend, args.out)
        print(f"[INFO] Capture enabled -> {args.out}")

    print(f"[INFO] Starting backend mode={args.mode} duration={args.duration}s")
    await _demo_poll(backend, args.duration)
    print("[INFO] Finished demo run")
    return 0


def main():  # pragma: no cover
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":  # pragma: no cover
    main()
