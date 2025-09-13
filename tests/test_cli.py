import asyncio
import json
import tempfile
from growatt_broker import cli


def test_main_async_runs_dataset_mode():
    dataset = {"holding": {"30": 100}, "input": {"0": 401}}
    with tempfile.NamedTemporaryFile("w", suffix=".json") as fp:
        json.dump(dataset, fp)
        fp.flush()
        result = asyncio.run(
            cli.main_async(
                ["run", "--mode", "dataset", "--dataset", fp.name, "--duration", "0"]
            )
        )
    assert result == 0
