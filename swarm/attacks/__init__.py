from .base import Attack
from .birthday import BirthdayAttack
from .bruteforce import BruteForceDES
from .classical import ClassicalCiphers
from .mitm import MeetInTheMiddle
from .padding_oracle import PaddingOracle
from .passwords import PasswordCracking

# Register new attacks here.
ATTACKS: dict[str, Attack] = {
    a.name: a
    for a in [
        BruteForceDES(),
        MeetInTheMiddle(),
        BirthdayAttack(),
        PasswordCracking(),
        PaddingOracle(),
        ClassicalCiphers(),
    ]
}
