from rest_framework.views import exception_handler


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is not None and isinstance(response.data, dict):
        # Many domain errors carry a stable machine-readable code alongside
        # their human message.  DRF wraps both values in ErrorDetail objects,
        # so preserve the explicit payload value instead of replacing every
        # ValidationError with its generic ``invalid`` default code.
        explicit_code = response.data.get("code")
        detail = response.data.get("detail", response.data)
        code = (str(explicit_code) if explicit_code is not None
                and not isinstance(explicit_code, (dict, list))
                else getattr(exc, "default_code", "error"))
        response.data = {"detail": detail, "code": code}
    return response
