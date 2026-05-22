from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

MODEL_METADATA_TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class ModelResponse:
    text: str
    model_name: str
    model_digest: str | None
    runtime_version: str | None


class ModelClient(Protocol):
    def generate(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        timeout_seconds: int,
    ) -> ModelResponse: ...

    def list_models(
        self,
        *,
        timeout_seconds: int = MODEL_METADATA_TIMEOUT_SECONDS,
    ) -> list[dict[str, Any]]: ...


class OllamaModelClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def generate(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        timeout_seconds: int,
    ) -> ModelResponse:
        with httpx.Client(timeout=timeout_seconds) as client:
            response = client.post(
                f"{self._base_url}/api/generate",
                json={
                    "model": model,
                    "system": system_prompt,
                    "prompt": user_prompt,
                    "stream": False,
                    "options": {"temperature": temperature},
                },
            )
            response.raise_for_status()
            payload = response.json()

        return ModelResponse(
            text=str(payload.get("response", "")),
            model_name=str(payload.get("model", model)),
            model_digest=self._resolve_digest(
                model,
                timeout_seconds=_metadata_timeout(timeout_seconds),
            ),
            runtime_version=self._runtime_version(
                timeout_seconds=_metadata_timeout(timeout_seconds),
            ),
        )

    def list_models(
        self,
        *,
        timeout_seconds: int = MODEL_METADATA_TIMEOUT_SECONDS,
    ) -> list[dict[str, Any]]:
        with httpx.Client(timeout=timeout_seconds) as client:
            response = client.get(f"{self._base_url}/api/tags")
            response.raise_for_status()
            payload = response.json()
        models = payload.get("models", [])
        return models if isinstance(models, list) else []

    def _resolve_digest(self, model: str, *, timeout_seconds: int) -> str | None:
        try:
            for item in self.list_models(timeout_seconds=timeout_seconds):
                if item.get("name") == model:
                    digest = item.get("digest")
                    return str(digest) if digest else None
        except Exception:
            return None
        return None

    def _runtime_version(self, *, timeout_seconds: int) -> str | None:
        try:
            with httpx.Client(timeout=timeout_seconds) as client:
                response = client.get(f"{self._base_url}/api/version")
                response.raise_for_status()
                version = response.json().get("version")
                return f"ollama {version}" if version else "ollama"
        except Exception:
            return None


def _metadata_timeout(generation_timeout_seconds: int) -> int:
    return max(
        MODEL_METADATA_TIMEOUT_SECONDS,
        min(generation_timeout_seconds, 60),
    )
