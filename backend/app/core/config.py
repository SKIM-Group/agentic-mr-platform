from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str
    app_api_key: str = "dev-key"
    skills_dir: str = "./skills_data"
    allowed_origins: str = "http://localhost:3000"
    chat_model: str = "claude-opus-4-6"
    routing_model: str = "claude-haiku-4-5"

    @property
    def origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",")]


settings = Settings()
