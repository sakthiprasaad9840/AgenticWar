from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    aava_api_base: str = "https://int-ai.aava.ai"
    aava_api_token: str = ""
    aava_workflow_id: int = 21427
    aava_poll_interval_sec: float = 2.0
    aava_poll_timeout_sec: float = 90.0
    upload_dir: str = "./data/uploads"
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
