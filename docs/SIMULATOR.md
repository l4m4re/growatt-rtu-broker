# Modbus simulator and dataset tools

The simulator belongs to Modbus Workbench because it models Modbus transports,
datasets, captures and scenarios independently of Home Assistant.

## Run the simulator

From the broker repository:

```bash
pip install -e '.[test]'
python -m growatt_broker.simulator.modbus_simulator
```

The default endpoint is a MIN 6000XH-TL model on TCP port 5020. Use
`--force-deterministic` for stable values, `--mode serial` for a PTY-backed
RTU endpoint, and `--mutator tools.mutators.sample_mutator:EnergyIncrement`
to exercise changing values.

The reusable async fixture is `growatt_broker.simulator.start_simulator`.
Home Assistant integration tests may use it as an external development
fixture; it is not an integration runtime dependency.

## Tools

- `tools/probe_simulator.py` reads representative holding and input ranges.
- `tools/compact_capture.py` converts broker capture JSONL to a dataset.
- `tools/build_dataset_from_scan.py` converts scanner output to a dataset.
- `tools/tcp_read.py` performs a small direct TCP read.
- `tools/mutators/` contains mutation examples.
- `scripts/start-serial-simulator.sh` starts the simulator behind a virtual
  serial pair.
- `scripts/dev-start-simulator.sh` manages a local simulator process.

Datasets are stored under `growatt_broker/simulator/datasets/`. Record the
source and checksum of reviewed datasets in the broker repository before using
them in regression tests.
