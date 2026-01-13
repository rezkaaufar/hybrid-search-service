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
    dataset_ids: List[int] = Field(default_factory=lambda: [1342, 1661, 98], alias="DATASET_IDS")
    ingest_mode: str = Field("ids", alias="INGEST_MODE")  # ids | mirror
    mirror_path: Optional[str] = Field(None, alias="GUTENBERG_MIRROR_PATH")
    ingest_limit: Optional[int] = Field(None, alias="INGEST_LIMIT")
    max_workers: int = Field(4, alias="MAX_WORKERS")
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    request_timeout: int = Field(30, alias="REQUEST_TIMEOUT")

    class Config:
        populate_by_name = True
        str_strip_whitespace = True

    @classmethod
    def from_env(cls) -> "Settings":
        raw = {k: v for k, v in os.environ.items() if k}
        settings = cls.model_validate(raw)
        return settings

    @field_validator("dataset_ids", mode="before")
    @classmethod
    def split_dataset_ids(cls, v):
        if isinstance(v, str):
            return [int(x) for x in v.split(",") if x.strip()]
        return v

    @field_validator("mirror_path", "ingest_limit", mode="before")
    @classmethod
    def empty_to_none(cls, v):
        if v == "" or v is None:
            return None
        return v

    @property
    def dataset_urls(self) -> List[str]:
        urls = []
        for gid in self.dataset_ids:
            urls.append(f"https://www.gutenberg.org/cache/epub/{gid}/pg{gid}.txt")
            urls.append(f"https://www.gutenberg.org/files/{gid}/{gid}-0.txt")
        return urls

    @property
    def is_mirror_mode(self) -> bool:
        return self.ingest_mode.lower() == "mirror"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
