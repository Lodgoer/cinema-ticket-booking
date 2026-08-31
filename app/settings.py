"""
Centralized configuration, loaded from environment variables (and .env
during local development). This replaces hardcoded values that used to
live directly in database.py and auth.py — those values were fine for
a quick prototype, but they end up committed to git, which means
committing your DB password and JWT signing key along with the code.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    secret_key: str
    enviroment: str = "development"

    @property
    def is_production(self) -> bool:
        return self.enviroment == "production"


settings = Settings()