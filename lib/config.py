from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_ENV: str = "development"
    SECRET_KEY: str

    ANTHROPIC_API_KEY: str
    VAPI_WEBHOOK_SECRET: str

    GOOGLE_CLIENT_ID: str
    GOOGLE_CLIENT_SECRET: str
    GOOGLE_REDIRECT_URI: str

    SUPABASE_URL: str
    SUPABASE_SERVICE_KEY: str

    REDIS_URL: str = "redis://localhost:6379"
    TOKEN_ENCRYPTION_KEY: str  # 64-char hex = 32 bytes

    FRONTEND_URL: str = "http://localhost:3000"

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"


settings = Settings()  # type: ignore[call-arg]
