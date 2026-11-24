"""Attack plugin interface.

Every attack splits a large search space into chunks. The coordinator calls
`plan()` once per job and the workers call `run_chunk()` once per chunk.
Adding a new attack means writing one subclass and registering it in
`swarm/attacks/__init__.py`. Nothing else in the platform has to change.
"""

from abc import ABC, abstractmethod
from typing import Any, Protocol


class ChunkContext(Protocol):
    def progress(self, n: int) -> bool:
        """Report `n` more units of work. Returns False when the worker should stop."""
        ...


class Plan:
    def __init__(self, meta: dict, chunks: list[dict], total: int, secret: dict, summary: str):
        self.meta = meta  # given to workers
        self.chunks = chunks  # each has a unique int "id"
        self.total = total  # total units of work, used for progress
        self.secret = secret  # the real answer, never sent to workers
        self.summary = summary  # one line for the event log


class Attack(ABC):
    name: str = ""
    title: str = ""
    unit: str = "keys"  # what one unit of work is called on the dashboard
    params_schema: dict[str, dict[str, Any]] = {}

    def describe(self) -> dict:
        return {"name": self.name, "title": self.title, "unit": self.unit, "params": self.params_schema}

    def clean_params(self, params: dict) -> dict:
        """Clamp user params to the schema's limits and fill in defaults."""
        out = {}
        for key, spec in self.params_schema.items():
            value = params.get(key, spec["default"])
            if spec["type"] == "int":
                value = max(spec["min"], min(spec["max"], int(value)))
            out[key] = value
        return out

    @abstractmethod
    def plan(self, params: dict) -> Plan: ...

    @abstractmethod
    def run_chunk(self, meta: dict, chunk: dict, ctx: ChunkContext) -> dict:
        """Return {"found": True, ...} on success, {"aborted": True} if stopped early, else {}."""
