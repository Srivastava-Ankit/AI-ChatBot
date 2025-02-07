from typing import List, Optional

class MentorSearchRequest:
    """
    Model representing the content search request parameters.
    """
    def __init__(self, terms: str, count: int, filters: Optional[dict] = None, skip: int = 0, organization_id: Optional[int] = None, sort: Optional[int] = None, sort_descending: bool = False):
        self.terms = terms
        self.count = count
        self.filters = filters if filters is not None else []
        self.skip = skip
        self.organization_id = organization_id
        self.sort = sort
        self.sort_descending = sort_descending

    def __repr__(self):
        return f"MentorSearchRequest(terms={self.terms}, count={self.count}, filters={self.filters}, skip={self.skip}, organization_id={self.organization_id}, sort={self.sort}, sort_descending={self.sort_descending})"

    class Builder:
        """
        Builder class for creating MentorSearchRequest instances.
        """
        def __init__(self):
            self._terms = ""
            self._count = 1
            self._filters = {}
            self._skip = 0
            self._organization_id = None
            self._sort = 0
            self._sort_descending = False

        def set_terms(self, terms: str):
            self._terms = terms
            return self

        def set_count(self, count: int):
            self._count = count
            return self

        def set_filters(self, filters: dict):
            self._filters = filters
            return self

        def set_skip(self, skip: int):
            self._skip = skip
            return self

        def set_organization_id(self, organization_id: int):
            self._organization_id = organization_id
            return self

        def set_sort(self, sort: Optional[int]):
            self._sort = sort
            return self

        def set_sort_descending(self, sort_descending: bool):
            self._sort_descending = sort_descending
            return self

        def build(self):
            return MentorSearchRequest(
                terms=self._terms,
                count=self._count,
                filters=self._filters,
                skip=self._skip,
                organization_id=self._organization_id,
                sort=self._sort,
                sort_descending=self._sort_descending
            )
