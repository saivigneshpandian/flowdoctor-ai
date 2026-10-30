from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.6-flash"
    groq_api_key: str = ""
    groq_model: str = ""
    n8n_url: str = ""
    n8n_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    # Telegram sends this back on every update. Replies and buttons are off while it's empty.
    telegram_webhook_secret: str = ""
    database_path: str = "./flowdoctor.db"
    secret_key: str = ""
    encryption_key: str = ""
    demo_email: str = ""
    demo_password: str = ""
    # Sign-up is closed while this is empty.
    invite_code: str = ""
    # True in production, where the browser reaches the app over HTTPS.
    cookie_secure: bool = False
    # Base of the webhook URL shown to users. Falls back to the URL of the current request.
    public_api_url: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
