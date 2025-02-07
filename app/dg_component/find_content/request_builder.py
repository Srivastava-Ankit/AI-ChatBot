from pydantic import BaseModel, Field
from typing import Any, Dict, List

class ContentSearchRequest(BaseModel):
    terms: str = Field(default="")
    filters: dict = Field(default_factory=dict)
    count: int = Field(default=20)
    includesProviders: bool = Field(default=True)
    boostRecent: bool = Field(default=False)
    boostPopular: bool = Field(default=False)
    useResourceImages: bool = Field(default=True)
    exclusionList: List[Any] = Field(default_factory=list)
    external: bool = Field(default=False)
    inputsOnly: bool = Field(default=False)
    skip: int = Field(default=0)
    persistFilter: bool = Field(default=False)

    class Builder:
        def __init__(self):
            self.terms = ""
            self.filters = {}
            self.count = 20
            self.includesProviders = True
            self.boostRecent = False
            self.boostPopular = False
            self.useResourceImages = True
            self.exclusionList = []
            self.external = False
            self.inputsOnly = False
            self.skip = 0
            self.persistFilter = False

        def set_terms(self, terms: str):
            self.terms = terms
            return self

        def set_filters(self, filters: dict):
            self.filters = filters
            return self

        def set_count(self, count: int):
            self.count = count
            return self

        def set_includes_providers(self, includesProviders: bool):
            self.includesProviders = includesProviders
            return self

        def set_boost_recent(self, boostRecent: bool):
            self.boostRecent = boostRecent
            return self

        def set_boost_popular(self, boostPopular: bool):
            self.boostPopular = boostPopular
            return self

        def set_use_resource_images(self, useResourceImages: bool):
            self.useResourceImages = useResourceImages
            return self

        def set_exclusion_list(self, exclusionList: list):
            self.exclusionList = exclusionList
            return self

        def set_external(self, external: bool):
            self.external = external
            return self

        def set_inputs_only(self, inputsOnly: bool):
            self.inputsOnly = inputsOnly
            return self

        def set_skip(self, skip: int):
            self.skip = skip
            return self

        def set_persist_filter(self, persistFilter: bool):
            self.persistFilter = persistFilter
            return self

        def build(self):
            return ContentSearchRequest(
                terms=self.terms,
                filters=self.filters,
                count=self.count,
                includesProviders=self.includesProviders,
                boostRecent=self.boostRecent,
                boostPopular=self.boostPopular,
                useResourceImages=self.useResourceImages,
                exclusionList=self.exclusionList,
                external=self.external,
                inputsOnly=self.inputsOnly,
                skip=self.skip,
                persistFilter=self.persistFilter
            )
