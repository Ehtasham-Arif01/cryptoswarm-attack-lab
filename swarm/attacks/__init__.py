from .base import Attack
from .birthday import BirthdayAttack
from .bruteforce import BruteForceDES
from .mitm import MeetInTheMiddle
from .passwords import PasswordCracking

# Register new attacks here (padding oracle, classical ciphers, ...).
ATTACKS: dict[str, Attack] = {
    a.name: a for a in [BruteForceDES(), MeetInTheMiddle(), BirthdayAttack(), PasswordCracking()]
}
