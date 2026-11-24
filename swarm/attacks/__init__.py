from .base import Attack
from .bruteforce import BruteForceDES
from .mitm import MeetInTheMiddle

# Register new attacks here (birthday, password, ...).
ATTACKS: dict[str, Attack] = {a.name: a for a in [BruteForceDES(), MeetInTheMiddle()]}
