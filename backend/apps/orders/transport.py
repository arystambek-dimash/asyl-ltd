import re

from rest_framework.exceptions import ValidationError


def validate_transport_number(value: str, transport_type: str) -> None:
    """Keep the legacy truck_number field; rail identifiers are eight digits."""
    if transport_type == "train" and value and not re.fullmatch(r"[0-9]{8}", value):
        raise ValidationError(
            {
                "truck_number": "Номер вагона должен содержать 8 цифр.",
            }
        )
