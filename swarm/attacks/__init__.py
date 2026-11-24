from .base import Attack
from .bruteforce import BruteForceDES

# Register new attacks here (MITM, birthday, password, ...).
ATTACKS: dict[str, Attack] = {a.name: a for a in [BruteForceDES()]}
