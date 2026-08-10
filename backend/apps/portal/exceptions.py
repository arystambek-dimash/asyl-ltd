from rest_framework.exceptions import APIException


class Conflict(APIException):
    status_code = 409
    default_code = "conflict"


class PaymentProviderError(APIException):
    status_code = 502
    default_code = "payment_provider_error"
