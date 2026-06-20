from rest_framework.views import exception_handler


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is not None and isinstance(response.data, dict):
        detail = response.data.get("detail", response.data)
        code = getattr(exc, "default_code", "error")
        response.data = {"detail": detail, "code": code}
    return response
