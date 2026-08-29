from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    aava_api_base: str = "https://int-ai.aava.ai"
    aava_api_token: str = ""
    aava_workflow_id: int = 21427               # Reconcile (Phase 1) — signed PSA + Amendment extraction
    aava_negotiate_workflow_id: int = 21656      # Negotiate (Phase 2) — draft contract extraction
    aava_poll_interval_sec: float = 2.0
    aava_poll_timeout_sec: float = 90.0
    upload_dir: str = "./data/uploads"
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
