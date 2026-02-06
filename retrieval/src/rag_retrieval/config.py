import os
from functools import lru_cache
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class Settings(BaseModel):
    database_url: str = Field(..., alias="DATABASE_URL")
    embedding_model: str = Field("sentence-transformers/all-MiniLM-L6-v2", alias="EMBEDDING_MODEL")
    embedding_dim: int = Field(384, alias="EMBEDDING_DIM")
    chunk_size: int = Field(450, alias="CHUNK_SIZE")
    chunk_overlap: int = Field(80, alias="CHUNK_OVERLAP")
    dataset_names: List[str] = Field(default_factory=lambda: ["Baby", "Pet_Supplies", "Video_Games"], alias="DATASET_NAMES")
    dataset_base_url: str = Field(
        "https://snap.stanford.edu/data/amazon/productGraph/categoryFiles", alias="DATASET_BASE_URL"
    )
    local_data_path: Optional[str] = Field(None, alias="LOCAL_DATA_PATH")
    max_reviews_per_dataset: Optional[int] = Field(5000, alias="MAX_REVIEWS_PER_DATASET")
    max_workers: int = Field(4, alias="MAX_WORKERS")
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    request_timeout: int = Field(30, alias="REQUEST_TIMEOUT")
    embed_concurrency: int = Field(2, alias="EMBED_CONCURRENCY", ge=1)

    class Config:
        populate_by_name = True
        str_strip_whitespace = True

    @classmethod
    def from_env(cls) -> "Settings":
        raw = {k: v for k, v in os.environ.items() if k}
        settings = cls.model_validate(raw)
        return settings

    @field_validator("dataset_names", mode="before")
    @classmethod
    def split_dataset_names(cls, v):
        if isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()]
        return v

    @field_validator("local_data_path", mode="before")
    @classmethod
    def empty_to_none(cls, v):
        if v == "" or v is None:
            return None
        return v

    @property
    def dataset_urls(self) -> List[str]:
        urls = []
        for name in self.dataset_names:
            safe = name.strip().replace(" ", "_")
            urls.append(f"{self.dataset_base_url}/reviews_{safe}_5.json.gz")
        return urls


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
