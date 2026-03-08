from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_ENV: str = "development"
    SECRET_KEY: str = "dev_secret_change_in_production"

    ANTHROPIC_API_KEY: str = ""
    VAPI_WEBHOOK_SECRET: str = ""
    VAPI_API_KEY: str = ""
    BACKEND_URL: str = "http://localhost:8000"

    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""

    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/auth/google/callback"

    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_KEY: str = ""

    REDIS_URL: str = "redis://localhost:6379"
    TOKEN_ENCRYPTION_KEY: str = "0" * 64

    FRONTEND_URL: str = "http://localhost:3000"

    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASS: str = ""
    SMTP_FROM: str = "noreply@secretaria.app"

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"


settings = Settings()  # type: ignore[call-arg]
