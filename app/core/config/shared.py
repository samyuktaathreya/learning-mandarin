from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_DIR = Path(__file__).resolve().parent.parent.parent
ROOT_DIR = APP_DIR.parent
PIPELINE_DIR = ROOT_DIR / "data_pipelines"
DATA_DIR = ROOT_DIR / "data"

class Settings(BaseSettings):
    # Base Paths
    root_dir: Path = ROOT_DIR
    pipeline_dir: Path = ROOT_DIR / "data_pipelines"

    # Required Environment Variables
    environment: str
    AZURE_SPEECH_KEY: str
    AZURE_SPEECH_REGION: str
    OPENAI_API_KEY: str
    CLAUDE_API_KEY: str
    SESSION_SECRET: str
    CLERK_SECRET_KEY: str
    CLERK_JWT_KEY: str
    CLERK_WEBHOOK_SECRET: str

    # Optional Environment Variables
    debug: bool = False
    api_key: str | None = None

    model_config = SettingsConfigDict(
        env_file=APP_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @property
    def allowed_origins(self) -> list[str]:
        """Returns allowed origins based on environment."""
        if self.environment == "prod":
            return ["http://146.190.220.182", "https://146.190.220.182"]
        else:
            return ["*"]

# Instantiate once for use throughout your app
settings = Settings()