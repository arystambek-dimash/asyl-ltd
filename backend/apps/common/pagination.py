"""Opt-in пагинация: страница включается только явным параметром запроса.

Множество старых потребителей (дашборд, посты погрузки, портал) читают
списки как плоский массив. Глобальный PAGE_SIZE сломал бы их все разом,
поэтому без ?page/?page_size ответ остаётся прежним, а новые экраны
запрашивают страницы сами.
"""
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
