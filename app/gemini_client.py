import json
import shutil
import time
from pathlib import Path
from typing import Any, Callable, TypeVar

from google import genai
from pydantic import BaseModel, ValidationError


T = TypeVar("T", bound=BaseModel)
UPLOAD_STAGING_DIR = Path("C:/tmp/ai-video-news-analyzer-gemini-uploads")


class GeminiClientError(RuntimeError):
    pass


class GeminiClient:
    def __init__(self, api_key: str, model: str, fallback_models: list[str] | None = None) -> None:
        self.model = model
        self.models = self._unique_models([model, *(fallback_models or [])])
        self.last_model_used: str | None = None
        self.client = genai.Client(api_key=api_key)

    def analyze_video_file(
        self,
        video_path: Path,
        prompt: str,
        response_model: type[T],
        max_wait_seconds: int = 300,
        retries: int = 3,
    ) -> T:
        last_exc: Exception | None = None
        for model in self.models:
            try:
                return self._with_retry(
                    lambda model=model: self._analyze_video_file_once(
                        video_path,
                        prompt,
                        response_model,
                        max_wait_seconds,
                        model,
                    ),
                    retries=retries,
                    action=f"影片分析 ({model})",
                )
            except (ValidationError, json.JSONDecodeError):
                raise
            except Exception as exc:
                last_exc = exc
                if not self._is_transient_error(exc):
                    raise GeminiClientError(f"Gemini API 影片分析失敗：{exc}") from exc
        raise GeminiClientError(f"Gemini API 影片分析失敗：{last_exc}")

    def _analyze_video_file_once(
        self,
        video_path: Path,
        prompt: str,
        response_model: type[T],
        max_wait_seconds: int,
        model: str,
    ) -> T:
        upload_path: Path | None = None
        try:
            upload_path = self._staged_ascii_upload_path(video_path)
            uploaded = self.client.files.upload(file=str(upload_path))
            self._wait_until_active(uploaded, max_wait_seconds=max_wait_seconds)
            return self._generate_json(
                contents=[uploaded, prompt],
                response_model=response_model,
                model=model,
            )
        finally:
            if upload_path and upload_path != video_path:
                try:
                    upload_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def generate_json_from_text(self, prompt: str, response_model: type[T]) -> T:
        last_exc: Exception | None = None
        for model in self.models:
            try:
                return self._with_retry(
                    lambda model=model: self._generate_json(contents=prompt, response_model=response_model, model=model),
                    retries=3,
                    action=f"彙整分析 ({model})",
                )
            except (ValidationError, json.JSONDecodeError):
                raise
            except Exception as exc:
                last_exc = exc
                if not self._is_transient_error(exc):
                    raise GeminiClientError(f"Gemini API 彙整分析失敗：{exc}") from exc
        raise GeminiClientError(f"Gemini API 彙整分析失敗：{last_exc}")

    def _generate_json(self, contents: Any, response_model: type[T], model: str) -> T:
        response = self.client.models.generate_content(
            model=model,
            contents=contents,
            config={
                "response_mime_type": "application/json",
                "response_json_schema": response_model.model_json_schema(),
            },
        )
        text = getattr(response, "text", None)
        if not text:
            raise GeminiClientError("Gemini 回應為空。")
        self.last_model_used = model
        return response_model.model_validate_json(text)

    def _unique_models(self, models: list[str]) -> list[str]:
        seen: set[str] = set()
        unique: list[str] = []
        for model in models:
            cleaned = model.strip()
            if cleaned and cleaned not in seen:
                unique.append(cleaned)
                seen.add(cleaned)
        return unique

    def _staged_ascii_upload_path(self, video_path: Path) -> Path:
        try:
            str(video_path).encode("ascii")
            video_path.name.encode("ascii")
            return video_path
        except UnicodeEncodeError:
            UPLOAD_STAGING_DIR.mkdir(parents=True, exist_ok=True)
            staged = UPLOAD_STAGING_DIR / f"segment_{int(time.time() * 1000)}_{video_path.stat().st_size}{video_path.suffix.lower()}"
            shutil.copy2(video_path, staged)
            return staged

    def _with_retry(self, operation: Callable[[], T], retries: int, action: str) -> T:
        last_exc: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                return operation()
            except Exception as exc:
                last_exc = exc
                if attempt >= retries or not self._is_transient_error(exc):
                    raise
                time.sleep(min(30, 2**attempt))
        raise GeminiClientError(f"{action}重試失敗：{last_exc}")

    def _is_transient_error(self, exc: Exception) -> bool:
        message = str(exc).upper()
        transient_markers = (
            "503",
            "UNAVAILABLE",
            "429",
            "RESOURCE_EXHAUSTED",
            "RATE_LIMIT",
            "DEADLINE_EXCEEDED",
            "TIMEOUT",
            "TEMPORAR",
        )
        return any(marker in message for marker in transient_markers)

    def _wait_until_active(self, uploaded_file: Any, max_wait_seconds: int) -> None:
        name = getattr(uploaded_file, "name", None)
        if not name:
            return

        deadline = time.monotonic() + max_wait_seconds
        current = uploaded_file
        while time.monotonic() < deadline:
            state = getattr(getattr(current, "state", None), "name", None) or str(getattr(current, "state", ""))
            if state.upper().endswith("ACTIVE") or state.upper() == "ACTIVE":
                return
            if state.upper().endswith("FAILED") or state.upper() == "FAILED":
                raise GeminiClientError(f"Gemini File API 處理檔案失敗：{name}")
            time.sleep(5)
            current = self.client.files.get(name=name)

        raise GeminiClientError(f"等待 Gemini File API 處理逾時：{name}")
