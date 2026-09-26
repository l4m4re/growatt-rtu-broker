"""Growatt Modbus simulator utilities."""

__all__ = ["SimulatorEndpoint", "start_simulator"]


def __getattr__(name: str):
    if name in __all__:
        from .modbus_simulator import SimulatorEndpoint, start_simulator

        return {
            "SimulatorEndpoint": SimulatorEndpoint,
            "start_simulator": start_simulator,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
