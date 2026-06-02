from functools import lru_cache
from pathlib import Path
import sys

from dotenv import load_dotenv
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


if getattr(sys, "frozen", False):
    PROJECT_ROOT = Path(sys.executable).resolve().parent
else:
    PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env"
API_KEY_PLACEHOLDER = "your_gemini_api_key_here"


class MissingApiKeyError(ValueError):
    """Raised when the required Gemini API key is not configured."""


class Settings(BaseSettings):
    gemini_api_key: SecretStr | None = Field(default=None, alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-2.5-flash", alias="GEMINI_MODEL")
    gemini_fallback_models: str = Field(default="gemini-2.0-flash", alias="GEMINI_FALLBACK_MODELS")
    app_display_name: str = Field(default="AI 影音分析專業版 V1", alias="APP_DISPLAY_NAME")
    app_audio_only: bool = Field(default=False, alias="APP_AUDIO_ONLY")
    outputs_dir: Path = PROJECT_ROOT / "outputs"
    temp_dir: Path = PROJECT_ROOT / "temp"
    videos_dir: Path = PROJECT_ROOT / "videos"

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @field_validator("gemini_model")
    @classmethod
    def validate_gemini_model(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("GEMINI_MODEL 不可為空。")
        return value

    def ensure_directories(self) -> None:
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.videos_dir.mkdir(parents=True, exist_ok=True)
        (self.outputs_dir / "cache").mkdir(parents=True, exist_ok=True)

    def require_api_key(self) -> str:
        key = self.gemini_api_key.get_secret_value().strip() if self.gemini_api_key else ""
        if not key or key == API_KEY_PLACEHOLDER:
            raise MissingApiKeyError(
                "找不到有效的 GEMINI_API_KEY。\n"
                f"請在 {ENV_FILE} 建立設定，內容可從 .env.example 複製，"
                "並把 GEMINI_API_KEY 換成 Google AI Studio 產生的 Gemini API Key。"
            )
        return key

    def fallback_models_list(self) -> list[str]:
        return [model.strip() for model in self.gemini_fallback_models.split(",") if model.strip()]


@lru_cache
def get_settings() -> Settings:
    load_dotenv(ENV_FILE, override=True)
    settings = Settings()
    settings.ensure_directories()
    return settings
