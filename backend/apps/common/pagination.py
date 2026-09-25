"""Opt-in пагинация: страница включается только явным параметром запроса.

Старые потребители (дашборд, портал) читают списки как плоский массив.
Глобальный PAGE_SIZE сломал бы их все разом, поэтому без ?page/?page_size
ответ остаётся прежним, а новые экраны запрашивают страницы сами.
"""
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100


class OptInPageNumberPagination(PageNumberPagination):
    page_size_query_param = "page_size"
    max_page_size = MAX_PAGE_SIZE

    def get_page_size(self, request):
        params = request.query_params
        if "page" not in params and self.page_size_query_param not in params:
            return None  # пагинация не запрошена — плоский список
        try:
            requested = int(params.get(self.page_size_query_param,
                                       DEFAULT_PAGE_SIZE))
        except (TypeError, ValueError):
            requested = DEFAULT_PAGE_SIZE
        return max(1, min(requested, MAX_PAGE_SIZE))


KEYSET_PAGE_SIZE = 50


def parse_pk_cursor(raw: str | None) -> int | None:
    """Курсор ``?before=<pk>``: только положительное целое из ASCII-цифр."""
    if not raw:
        return None
    # isdigit() без isascii() пропустил бы «²», и int() упал бы в 500.
    if not (raw.isascii() and raw.isdigit()) or int(raw) <= 0:
        raise ValidationError("Некорректный номер страницы")
    return int(raw)


def keyset_page(rows, before: str | None, size: int = KEYSET_PAGE_SIZE):
    """Страница строк по убыванию pk: ``(строки, next_cursor)``.

    ``rows`` уже упорядочены по ``-id``; следующая страница — ``?before=<next_cursor>``.
    """
    cursor = parse_pk_cursor(before)
    if cursor is not None:
        rows = rows.filter(pk__lt=cursor)
    page = list(rows[: size + 1])
    next_cursor = page[size - 1].pk if len(page) > size else None
    return page[:size], next_cursor
