import json
import asyncio
import pytest
import growatt_broker.backend as backend_module


@pytest.mark.asyncio
async def test_dataset_backend_basic(tmp_path):
    dataset = {"holding": {"30": 100}, "input": {"0": 1}}
    dataset_path = tmp_path / "data.json"
    dataset_path.write_text(json.dumps(dataset), encoding="utf-8")
    backend = backend_module.DatasetBackend(dataset_path)

    assert await backend.read_input(1, 0, 1) == [1]
    assert await backend.read_holding(1, 30, 1) == [100]

    await backend.write_single(1, 30, 55)
    await backend.write_multiple(1, 31, [2, 3])
    assert await backend.read_holding(1, 30, 3) == [55, 2, 3]


@pytest.mark.asyncio
async def test_dataset_backend_mutation(tmp_path, monkeypatch):
    dataset = {"holding": {"150": 1}}
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(json.dumps(dataset), encoding="utf-8")

    # Freeze time at creation
    monkeypatch.setattr(backend_module.time, "time", lambda: 1000.0)
    backend = backend_module.DatasetBackend(dataset_path, mutate=True)

    # Advance time by 5 seconds and read mutated value
    monkeypatch.setattr(backend_module.time, "time", lambda: 1005.0)
    assert await backend.read_holding(1, 150, 1) == [6]


@pytest.mark.asyncio
async def test_capture_backend_logs(tmp_path):
    dataset_path = tmp_path / "data.json"
    dataset_path.write_text(json.dumps({"holding": {"30": 100}}), encoding="utf-8")
    inner = backend_module.DatasetBackend(dataset_path)
    log_path = tmp_path / "log.jsonl"
    backend = backend_module.CaptureBackend(inner, log_path)

    await backend.read_holding(1, 30, 1)
    await backend.write_single(1, 31, 7)

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    evt1 = json.loads(lines[0])
    evt2 = json.loads(lines[1])
    assert evt1["op"] == "read_holding"
    assert evt1["regs"] == [100]
    assert evt2["op"] == "write_single"
    assert evt2["value"] == 7
