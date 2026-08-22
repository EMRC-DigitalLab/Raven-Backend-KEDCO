# users/pagination.py
from rest_framework.pagination import PageNumberPagination


class UserPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class RolePermissionPagination(PageNumberPagination):
    """The matrix is inherently small (roles x sections — never more than a
    few hundred rows even with many custom roles) and the frontend needs the
    whole grid at once to render it, not a page at a time. Default page_size
    is set high enough that it's effectively "return everything" in normal
    use, while still keeping the {count, next, previous, results} envelope
    consistent with every other list endpoint."""
    page_size = 100
    page_size_query_param = 'page_size'
    max_page_size = 500
