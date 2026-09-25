"""Read a small register range from a PiITM Modbus TCP listener."""

import argparse
import asyncio
import os

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.getenv("BROKER_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port", type=int, default=int(os.getenv("BROKER_PORT", "5021"))
    )
    parser.add_argument("--unit", type=int, default=1)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--count", type=int, default=16)
    parser.add_argument("--timeout", type=float, default=3.0)
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    client = AsyncModbusTcpClient(
        host=args.host, port=args.port, timeout=args.timeout, retries=1
    )
    try:
        connected = await client.connect()
        if not connected or not client.connected:
            raise RuntimeError(f"Unable to connect to {args.host}:{args.port}")
        response = await client.read_holding_registers(
            args.start, count=args.count, device_id=args.unit
        )
        if response.isError():
            raise ModbusException(response)
        print(f"Read {args.count} registers starting at {args.start}:")
        for offset, value in enumerate(response.registers, args.start):
            print(f"  {offset:04d}: {value}")
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
