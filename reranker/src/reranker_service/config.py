from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        populate_by_name=True,
        extra="ignore",
    )

    # Reranker model
    reranker_model: str = Field(
        default="cross-encoder/ms-marco-MiniLM-L-6-v2",
        alias="RERANKER_MODEL",
    )
    model_local_path: Optional[str] = Field(
        default=None,
        alias="MODEL_LOCAL_PATH",
    )

    # Service settings
    port: int = Field(default=8080, alias="PORT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Concurrency: limit simultaneous reranking calls to avoid CPU saturation
    rerank_concurrency: int = Field(default=2, ge=1, alias="RERANK_CONCURRENCY")

    # Maximum number of documents accepted per request
    max_docs_per_request: int = Field(default=100, ge=1, alias="MAX_DOCS_PER_REQUEST")

    @field_validator("log_level", mode="before")
    @classmethod
    def upper_log_level(cls, v: str) -> str:
        return v.upper()

    @field_validator("model_local_path", mode="before")
    @classmethod
    def empty_to_none(cls, v: Optional[str]) -> Optional[str]:
        if v == "":
            return None
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
