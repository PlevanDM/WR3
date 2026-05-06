from __future__ import annotations
from functools import cached_property
from pathlib import Path
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve `.env` from project root (`WR3/app/config.py` → `WR3/`), not from CWD.
# Otherwise Telegram / IDE / systemd can start `python -m app.main` from another
# directory and silently pick another (or missing) `.env`.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_PATH = _PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_PATH),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str = Field(alias="BOT_TOKEN")
    admin_tg_ids: str = Field(default="", alias="ADMIN_TG_IDS")

    remonline_api_key: str = Field(alias="REMONLINE_API_KEY")
    remonline_base_url: str = Field(default="https://api.roapp.io", alias="REMONLINE_BASE_URL")
    remonline_order_url: str = Field(default="https://web.roapp.io/orders/{id}", alias="REMONLINE_ORDER_URL")

    gallery_chat_id: int | None = Field(default=None, alias="GALLERY_CHAT_ID")

    @field_validator("gallery_chat_id", mode="before")
    @classmethod
    def _empty_to_none(cls, v):
        if v in ("", None):
            return None
        return v

    db_url: str = Field(default="sqlite+aiosqlite:///./data/warranty.db", alias="DB_URL")
    use_redis: bool = Field(default=False, alias="USE_REDIS")
    redis_host: str = Field(default="localhost", alias="REDIS_HOST")
    redis_port: int = Field(default=6379, alias="REDIS_PORT")

    postgres_user: str = Field(default="", alias="POSTGRES_USER")
    postgres_password: str = Field(default="", alias="POSTGRES_PASSWORD")
    postgres_db: str = Field(default="", alias="POSTGRES_DB")
    postgres_host: str = Field(default="", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")

    poll_interval_sec: int = Field(default=60, alias="POLL_INTERVAL_SEC")
    sla_stale_days: int = Field(default=7, alias="SLA_STALE_DAYS")
    fresh_window_days: int = Field(default=30, alias="FRESH_WINDOW_DAYS")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    tz: str = Field(default="Europe/Kyiv", alias="TZ")

    @cached_property
    def admin_ids(self) -> List[int]:
        return [int(x) for x in self.admin_tg_ids.replace(" ", "").split(",") if x]

    @property
    def database_url(self) -> str:
        if self.postgres_user and self.postgres_host and self.postgres_db:
            return (
                f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            )
        # SQLite: paths like `./data/warranty.db` are resolved by the driver relative
        # to OS CWD, not project root — easy to accidentally use an old DB. Anchor
        # relative paths under the repo (`WR3/`).
        url = self.db_url.strip()
        if url.startswith("sqlite") and ":///" in url:
            scheme, tail = url.split(":///", 1)
            if tail != ":memory:" and tail != "":
                p = Path(tail)
                if not p.is_absolute():
                    p = (_PROJECT_ROOT / p).resolve()
                else:
                    p = p.resolve()
                return f"{scheme}:///{p.as_posix()}"
        return url

    @property
    def sqlite_path_for_logging(self) -> str | None:
        """Diagnostics only — absolute DB path when using SQLite."""
        u = self.database_url
        if not u.startswith("sqlite") or ":///" not in u:
            return None
        return u.split(":///", 1)[-1].split("?")[0]

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/0"

    def order_url(self, order_id: int | str) -> str:
        return self.remonline_order_url.format(id=order_id)


settings = Settings()  # type: ignore[call-arg]
