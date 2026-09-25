"""Общий ответ-ошибка API камер ``{detail, code}``."""

from rest_framework.response import Response


def error_response(detail, code: str, response_status: int) -> Response:
    return Response({"detail": detail, "code": code}, status=response_status)

