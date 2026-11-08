import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

# Map Persian (U+06F0..) and Arabic-Indic (U+0660..) digits to ASCII digits.
_DIGIT_MAP = {}
for _i in range(10):
    _DIGIT_MAP[0x06F0 + _i] = str(_i)
    _DIGIT_MAP[0x0660 + _i] = str(_i)


def normalize_phone(raw):
    """Convert any common Iranian mobile format to 09XXXXXXXXX."""
    phone = (raw or "").translate(_DIGIT_MAP)
    phone = re.sub(r"[\s\-()]", "", phone)
    if phone.startswith("+98"):
        phone = "0" + phone[3:]
    elif phone.startswith("0098"):
        phone = "0" + phone[4:]
    elif phone.startswith("98") and len(phone) == 12:
        phone = "0" + phone[2:]
    elif len(phone) == 10 and phone.startswith("9"):
        phone = "0" + phone
    return phone


def validate_phone(value):
    if not re.fullmatch(r"09\d{9}", value):
        raise ValidationError(_("Enter a valid mobile number, e.g. 09123456789."))