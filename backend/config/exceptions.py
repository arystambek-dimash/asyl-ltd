from rest_framework.views import exception_handler


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is not None and isinstance(response.data, dict):
        explicit_code = response.data.get("code")
        if isinstance(explicit_code, (list, tuple)) and len(explicit_code) == 1:
            explicit_code = explicit_code[0]
        detail = response.data.get("detail", response.data)
        code = (str(explicit_code) if explicit_code is not None
                                      and not isinstance(explicit_code, (dict, list))
                else getattr(exc, "default_code", "error"))
        response.data = {"detail": detail, "code": code}
    return response
