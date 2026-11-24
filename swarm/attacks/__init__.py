from .base import Attack
from .birthday import BirthdayAttack
from .bruteforce import BruteForceDES
from .mitm import MeetInTheMiddle

# Register new attacks here (passwords, padding oracle, ...).
ATTACKS: dict[str, Attack] = {a.name: a for a in [BruteForceDES(), MeetInTheMiddle(), BirthdayAttack()]}
