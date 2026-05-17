import json
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = Field(default="development", alias="APP_ENV")
    database_url: str = Field(
        default="sqlite+aiosqlite:///./cipher_collab.db",
        alias="DATABASE_URL",
    )
    jwt_access_secret: str = Field(default="dev-access-secret-change-me", alias="JWT_ACCESS_SECRET")
    jwt_refresh_secret: str = Field(default="dev-refresh-secret-change-me", alias="JWT_REFRESH_SECRET")
    aes_gcm_key: str = Field(
        default="L9bbQxAM0OaeU39b-P_yL7hDbyQazdUTJxXqdQ9uXyA=",
        alias="AES_GCM_KEY",
    )
    cors_origins: list[str] = Field(default=["*"], alias="CORS_ORIGINS")
    frontend_url: str = Field(default="http://localhost:5000", alias="FRONTEND_URL")
    access_token_minutes: int = 30
    refresh_token_days: int = 14

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            if value.strip().startswith("["):
                return list(json.loads(value))
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
