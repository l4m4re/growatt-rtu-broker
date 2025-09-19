import asyncio
import os
import sys
import socket
import pytest
from pymodbus.client import AsyncModbusTcpClient
from pymodbus.framer import FramerType

from .serial_helpers import virtual_serial_pair

pytestmark = pytest.mark.enable_socket


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


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.mark.asyncio
async def test_downstream_timeout_logged(tmp_path):
    # Create a virtual pair for the broker inverter side but DO NOT attach a simulator
    async with virtual_serial_pair() as (inverter_port, _ignored_client_side):
        # Also create a ShineWiFi virtual port for broker to read requests; we won't send anything there.
        async with virtual_serial_pair() as (shine_port, _):
            tcp_port = _free_port()
            env = os.environ.copy()
            env.setdefault("PYTHONUNBUFFERED", "1")
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "growatt_broker.broker",
                "--inverter",
                inverter_port,
                "--shine",
                shine_port,
                "--baud",
                "9600",
                "--bytes",
                "8N1",
                "--tcp",
                f"127.0.0.1:{tcp_port}",
                "--min-period",
                "0.05",
                "--rtimeout",
                "0.2",
                "--log",
                "-",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            assert proc.stdout is not None
            assert proc.stderr is not None
            try:
                # Broker up
                await _wait_for_line(proc.stdout, "Broker up", timeout=5)
                # Connect TCP client and issue a read (with some unit and 2 registers)
                client = AsyncModbusTcpClient(
                    "127.0.0.1",
                    port=tcp_port,
                    framer=FramerType.SOCKET,
                    timeout=1,
                    retries=0,
                    reconnect_delay=0,
                )
                await client.connect()
                # This will time out downstream since no simulator responds on inverter serial port
                try:
                    await client.read_input_registers(0, count=2, device_id=1)
                except Exception:
                    # Expected: pymodbus raises when no response
                    pass
                # Now verify the broker logged the downstream timeout
                await _wait_for_line(proc.stdout, "downstream_timeout", timeout=3)
            finally:
                client.close()
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
