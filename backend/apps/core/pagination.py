from rest_framework.pagination import PageNumberPagination


class StandardResultsPagination(PageNumberPagination):
    """Default pagination for unbounded list endpoints.

    Returns {count, next, previous, results}. Clients may request a custom size
    via ?page_size=, capped at max_page_size to bound response size.
    """
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 200
