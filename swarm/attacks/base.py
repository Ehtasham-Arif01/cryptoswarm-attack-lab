"""Attack plugin interface.

Every attack splits a large search space into chunks. The coordinator calls
`plan()` once per job and the workers call `run_chunk()` once per chunk.
Adding a new attack means writing one subclass and registering it in
`swarm/attacks/__init__.py`. Nothing else in the platform has to change.

Multi-phase attacks (e.g. meet-in-the-middle: build table, then look up)
return several phases. Phase i+1 is only queued after every chunk of phase i
is finished.
"""

from abc import ABC, abstractmethod
from typing import Any, Protocol


class ChunkContext(Protocol):
    r: Any  # sync Redis client, for attacks that use shared tables
    job_id: str

    def progress(self, n: int) -> bool:
        """Report `n` more units of work. Returns False when the worker should stop."""
        ...


class Plan:
    def __init__(
        self,
        meta: dict,
        phases: list[list[dict]],
        total: int,
        secret: dict,
        summary: str,
        phase_names: list[str] | None = None,
    ):
        self.meta = meta  # given to workers
        self.phases = phases  # list of phases; each is a list of chunks with globally unique int "id"
        self.total = total  # total units of work across all phases, used for progress
        self.secret = secret  # the real answer, never sent to workers
        self.summary = summary  # one line for the event log
        self.phase_names = phase_names or [f"phase {i + 1}" for i in range(len(phases))]

    @property
    def chunks(self) -> list[dict]:
        return [c for phase in self.phases for c in phase]

    @property
    def phase_ends(self) -> list[int]:
        """Cumulative chunk counts: chunk ids < phase_ends[i] belong to phases 0..i."""
        ends, n = [], 0
        for phase in self.phases:
            n += len(phase)
            ends.append(n)
        return ends


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
            elif spec["type"] == "choice" and value not in spec["options"]:
                value = spec["default"]
            out[key] = value
        return out

    @abstractmethod
    def plan(self, params: dict) -> Plan: ...

    @abstractmethod
    def run_chunk(self, meta: dict, chunk: dict, ctx: ChunkContext) -> dict:
        """Return {"found": True, ...} on success, {"aborted": True} if stopped early, else {}."""
