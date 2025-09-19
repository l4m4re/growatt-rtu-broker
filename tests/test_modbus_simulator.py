import asyncio
import socket
import importlib
import sys
import json
import os

import pytest
from pymodbus.client import AsyncModbusSerialClient, AsyncModbusTcpClient
from pymodbus.framer import FramerType

from growatt_broker.simulator import start_simulator
from .serial_helpers import serial_environment_available, virtual_serial_pair

pytestmark = pytest.mark.enable_socket
SERIAL_AVAILABLE = serial_environment_available()


def test_serial_environment_required():
    """Fail fast if the virtual serial environment is not available.

    This suite expects a working virtual serial pair provider; otherwise
    serial-mode tests are meaningless for CI and local verification.
    """
    assert SERIAL_AVAILABLE, (
        "virtual serial ports unavailable; ensure test serial environment is set up "
        "(e.g. socat/pty or OS-provided PTY pair)"
    )


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


async def _wait_for_line(stream, text: str, timeout: float = 5.0) -> str:
    async def _read():
        while True:
            line = await stream.readline()
            if not line:
                raise RuntimeError("Broker exited before signalling readiness")
            decoded = line.decode().strip()
            if text in decoded:
                return decoded

    return await asyncio.wait_for(_read(), timeout)


@pytest.mark.asyncio
async def test_positional_port_and_custom_host():
    # Find a free port first
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    async with start_simulator(port, host="127.0.0.1") as (host, real_port):
        assert real_port == port
        assert host == "127.0.0.1"
        client = AsyncModbusTcpClient(
            host, port=real_port, framer=FramerType.SOCKET, reconnect_delay=0
        )
        await client.connect()
        # Brief sleep to ensure server task fully entered serve loop
        await asyncio.sleep(0.05)
        rr = await client.read_input_registers(0, count=2)
        assert not rr.isError()
        assert rr.registers == [1, 2]
    client.close()


@pytest.mark.asyncio
async def test_default_dataset_values():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    async with start_simulator(port) as (host, real_port):
        client = AsyncModbusTcpClient(
            host, port=real_port, framer=FramerType.SOCKET, reconnect_delay=0
        )
        await client.connect()
        await asyncio.sleep(0.05)
        rr = await client.read_input_registers(0, count=2)
        assert not rr.isError()
        assert rr.registers == [1, 2]
    client.close()


@pytest.mark.asyncio
@pytest.mark.skipif(not SERIAL_AVAILABLE, reason="virtual serial ports unavailable")
async def test_default_dataset_values_serial():
    async with virtual_serial_pair() as (sim_port, client_port):
        async with start_simulator(
            mode="serial",
            serial_port=sim_port,
            force_deterministic=True,
        ) as endpoint:
            assert endpoint.mode == "serial"
            assert endpoint.serial_port == sim_port
            client = AsyncModbusSerialClient(
                client_port,
                framer=FramerType.RTU,
                baudrate=9600,
                stopbits=1,
                bytesize=8,
                parity="N",
                timeout=1,
                reconnect_delay=0,
            )
            try:
                await client.connect()
                await asyncio.sleep(0.1)
                rr = await client.read_input_registers(0, count=2, device_id=1)
                assert not rr.isError()
                assert rr.registers == [1, 2]
            finally:
                client.close()


@pytest.mark.asyncio
@pytest.mark.skipif(not SERIAL_AVAILABLE, reason="virtual serial ports unavailable")
async def test_broker_tcp_roundtrip_with_serial_simulator(tmp_path):
    tcp_port = _free_port()

    async with virtual_serial_pair() as (sim_port, broker_inverter_port):
        async with virtual_serial_pair() as (shine_port, _):
            async with start_simulator(
                mode="serial",
                serial_port=sim_port,
                force_deterministic=True,
            ):
                env = os.environ.copy()
                env.setdefault("PYTHONUNBUFFERED", "1")
                proc = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "growatt_broker.broker",
                    "--inverter",
                    broker_inverter_port,
                    "--shine",
                    shine_port,
                    "--inv-baud",
                    "9600",
                    "--shine-baud",
                    "9600",
                    "--baud",
                    "9600",
                    "--bytes",
                    "8N1",
                    "--tcp",
                    f"127.0.0.1:{tcp_port}",
                    "--min-period",
                    "0.05",
                    "--rtimeout",
                    "0.5",
                    "--log",
                    "-",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )

                assert proc.stdout is not None
                assert proc.stderr is not None
                try:
                    await _wait_for_line(proc.stdout, "Broker up")

                    client = AsyncModbusTcpClient(
                        "127.0.0.1",
                        port=tcp_port,
                        framer=FramerType.SOCKET,
                        timeout=2,
                        retries=1,
                        reconnect_delay=0,
                    )
                    try:
                        connected = await client.connect()
                        assert connected and client.connected
                        await asyncio.sleep(0.1)
                        rr = await client.read_input_registers(0, count=2, device_id=1)
                        assert not rr.isError()
                        assert rr.registers == [1, 2]
                    finally:
                        client.close()
                finally:
                    proc.terminate()
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        proc.kill()
                        await proc.wait()
                    stdout_left = await proc.stdout.read()
                    stderr_left = await proc.stderr.read()
                    if proc.returncode not in (0, -15, -9, None):
                        pytest.fail(
                            "Broker subprocess exited with code {}.\nSTDOUT:\n{}\nSTDERR:\n{}".format(
                                proc.returncode,
                                stdout_left.decode(),
                                stderr_left.decode(),
                            )
                        )


@pytest.mark.asyncio
async def test_mutation_plugin_application(tmp_path, monkeypatch):
    # Create a temporary module acting as a mutator
    mod_path = tmp_path / "temp_mutator.py"
    mod_path.write_text(
        "tick_values = []\n"
        "def mutate(registers, tick):\n"
        "    # Increment register 1 each tick starting from existing value\n"
        "    registers['input'][1] = registers['input'].get(1, 0) + 10\n"
        "    tick_values.append(registers['input'][1])\n"
    )
    sys.path.insert(0, str(tmp_path))
    try:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        async with start_simulator(port, mutators=["temp_mutator"]) as (
            host,
            real_port,
        ):
            client = AsyncModbusTcpClient(
                host, port=real_port, framer=FramerType.SOCKET, reconnect_delay=0
            )
            await client.connect()
            # Read initial value after first tick sleep (~0.05s in ctx + <1s before first loop)
            await asyncio.sleep(1.2)
            # Address 0 maps to our seeded register 1 which the mutator increments
            rr1 = await client.read_input_registers(0, count=1)
            first = rr1.registers[0]
            await asyncio.sleep(1.1)
            rr2 = await client.read_input_registers(0, count=1)
            second = rr2.registers[0]
            assert second > first >= 10  # mutated at least once
            client.close()
        temp_mod = importlib.import_module("temp_mutator")
        assert len(temp_mod.tick_values) >= 2
    finally:
        if str(tmp_path) in sys.path:
            sys.path.remove(str(tmp_path))


@pytest.mark.asyncio
@pytest.mark.skipif(not SERIAL_AVAILABLE, reason="virtual serial ports unavailable")
async def test_mutation_plugin_application_serial(tmp_path):
    mod_path = tmp_path / "temp_mutator.py"
    mod_path.write_text(
        "tick_values = []\n"
        "def mutate(registers, tick):\n"
        "    registers['input'][1] = registers['input'].get(1, 0) + 5\n"
        "    tick_values.append(registers['input'][1])\n"
    )
    sys.path.insert(0, str(tmp_path))
    try:
        async with virtual_serial_pair() as (sim_port, client_port):
            async with start_simulator(
                mode="serial",
                serial_port=sim_port,
                mutators=["temp_mutator"],
            ):
                client = AsyncModbusSerialClient(
                    client_port,
                    framer=FramerType.RTU,
                    baudrate=9600,
                    stopbits=1,
                    bytesize=8,
                    parity="N",
                    timeout=1,
                    reconnect_delay=0,
                )
                try:
                    await client.connect()
                    await asyncio.sleep(1.2)
                    rr1 = await client.read_input_registers(0, count=1, device_id=1)
                    first = rr1.registers[0]
                    await asyncio.sleep(1.1)
                    rr2 = await client.read_input_registers(0, count=1, device_id=1)
                    second = rr2.registers[0]
                    assert second > first >= 5
                finally:
                    client.close()
        temp_mod = importlib.import_module("temp_mutator")
        assert len(temp_mod.tick_values) >= 2
    finally:
        if str(tmp_path) in sys.path:
            sys.path.remove(str(tmp_path))


@pytest.mark.asyncio
async def test_strict_defs_ignores_extra_dataset(tmp_path):
    # Create a minimal dataset with an extra register not in defs
    dataset = {"input": {"9999": 123}, "holding": {}}
    dataset_path = tmp_path / "ds.json"
    dataset_path.write_text(json.dumps(dataset))
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    async with start_simulator(port, dataset=str(dataset_path), strict_defs=True) as (
        host,
        real_port,
    ):
        client = AsyncModbusTcpClient(
            host, port=real_port, framer=FramerType.SOCKET, reconnect_delay=0
        )
        await client.connect()
        # 9999 should not exist (not in definition); reading should give 0 or error
        rr = await client.read_input_registers(9999, count=1)
        if not rr.isError():
            assert rr.registers[0] == 0
    client.close()


@pytest.mark.asyncio
@pytest.mark.skipif(not SERIAL_AVAILABLE, reason="virtual serial ports unavailable")
async def test_strict_defs_ignores_extra_dataset_serial(tmp_path):
    dataset = {"input": {"9999": 123}, "holding": {}}
    dataset_path = tmp_path / "ds.json"
    dataset_path.write_text(json.dumps(dataset))
    async with virtual_serial_pair() as (sim_port, client_port):
        async with start_simulator(
            mode="serial",
            serial_port=sim_port,
            dataset=str(dataset_path),
            strict_defs=True,
        ):
            client = AsyncModbusSerialClient(
                client_port,
                framer=FramerType.RTU,
                baudrate=9600,
                stopbits=1,
                bytesize=8,
                parity="N",
                timeout=1,
                reconnect_delay=0,
            )
            try:
                await client.connect()
                await asyncio.sleep(0.1)
                rr = await client.read_input_registers(9999, count=1, device_id=1)
                if not rr.isError():
                    assert rr.registers[0] == 0
            finally:
                client.close()
