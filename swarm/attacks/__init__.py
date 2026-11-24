from .base import Attack
from .birthday import BirthdayAttack
from .bruteforce import BruteForceDES
from .mitm import MeetInTheMiddle
from .padding_oracle import PaddingOracle
from .passwords import PasswordCracking

# Register new attacks here (classical ciphers, ...).
ATTACKS: dict[str, Attack] = {
    a.name: a
    for a in [BruteForceDES(), MeetInTheMiddle(), BirthdayAttack(), PasswordCracking(), PaddingOracle()]
}
