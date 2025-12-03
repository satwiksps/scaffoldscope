"""Provider-neutral model requests plus a zero-dependency OpenAI-compatible adapter."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from scaffoldscope import __version__
from scaffoldscope.errors import ConfigError, ModelError
from scaffoldscope.jsonutil import strict_json_loads
from scaffoldscope.redact import redact_text
from scaffoldscope.schema import ModelConfig, TaskSpec
from scaffoldscope.tokenization import Char4TokenCounter

if TYPE_CHECKING:
    from scaffoldscope.plugins import PluginRegistry


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    source: str = "provider"

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, int | str]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "total_tokens": self.total_tokens,
            "source": self.source,
        }


@dataclass(frozen=True)
class ModelResponse:
    content: str
    usage: Usage
    request_id: str | None
    provider_model: str
    finish_reason: str | None
    latency_seconds: float
    raw_metadata: dict[str, Any]


class ChatModel(Protocol):
    def complete(
        self,
        messages: Sequence[dict[str, str]],
        *,
        seed: int,
        max_output_tokens: int,
        temperature: float,
    ) -> ModelResponse: ...


def estimate_cost(config: ModelConfig, usage: Usage) -> float | None:
    if config.input_price_per_million is None or config.output_price_per_million is None:
        return None
    cache_read_price = (
        config.cache_read_price_per_million
        if config.cache_read_price_per_million is not None
        else config.input_price_per_million
    )
    cache_write_price = (
        config.cache_write_price_per_million
        if config.cache_write_price_per_million is not None
        else config.input_price_per_million
    )
    uncached_input = max(0, usage.input_tokens - usage.cache_read_tokens - usage.cache_write_tokens)
    return (
        uncached_input * config.input_price_per_million
        + usage.cache_read_tokens * cache_read_price
        + usage.cache_write_tokens * cache_write_price
        + usage.output_tokens * config.output_price_per_million
    ) / 1_000_000


class ScriptedModel:
    """Deterministic engine smoke-test provider; it is not an intelligence benchmark."""

    def __init__(self, config: ModelConfig, task: TaskSpec, counter: Char4TokenCounter) -> None:
        if not task.script:
            raise ConfigError(f"Task {task.id} has no script for the scripted model provider")
        self.config = config
        self.task = task
        self.counter = counter
        self.index = 0

    def complete(
        self,
        messages: Sequence[dict[str, str]],
        *,
        seed: int,
        max_output_tokens: int,
        temperature: float,
    ) -> ModelResponse:
        del seed, max_output_tokens, temperature
        started = time.perf_counter()
        if self.index >= len(self.task.script):
            value: dict[str, Any] = {"final": "Script complete."}
        else:
            value = self.task.script[self.index]
        self.index += 1
        content = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
        usage = Usage(
            input_tokens=self.counter.messages(messages),
            output_tokens=self.counter.text(content),
            source="estimated_char4",
        )
        return ModelResponse(
            content=content,
            usage=usage,
            request_id=f"scripted-{self.task.id}-{self.index:03d}",
            provider_model=self.config.name,
            finish_reason="stop",
            latency_seconds=time.perf_counter() - started,
            raw_metadata={"script_index": self.index - 1},
        )


class OpenAICompatibleModel:
    """Minimal `/chat/completions` adapter for hosted APIs, vLLM, and Ollama."""

    def __init__(
        self,
        config: ModelConfig,
        counter: Char4TokenCounter,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        if config.base_url is None:
            raise ConfigError("model.base_url is required for openai_compatible")
        api_key = os.environ.get(config.api_key_env, "")
        if config.requires_api_key and not api_key:
            raise ConfigError(
                f"Environment variable {config.api_key_env} is required by this model config"
            )
        self.config = config
        self.counter = counter
        self.api_key = api_key
        self.event_callback = event_callback

    @property
    def endpoint(self) -> str:
        base = self.config.base_url or ""
        if base.endswith("/chat/completions"):
            return base
        return base.rstrip("/") + "/chat/completions"

    def _safe_error(self, error: object) -> str:
        message = str(error)
        if self.api_key:
            message = message.replace(self.api_key, "[REDACTED]")
        return redact_text(message)

    def complete(
        self,
        messages: Sequence[dict[str, str]],
        *,
        seed: int,
        max_output_tokens: int,
        temperature: float,
    ) -> ModelResponse:
        payload: dict[str, Any] = {
            "model": self.config.name,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": max_output_tokens,
        }
        if self.config.supports_seed:
            payload["seed"] = seed
        if self.config.json_mode:
            payload["response_format"] = {"type": "json_object"}
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        last_error: BaseException | None = None
        attempts_made = 0
        for attempt in range(self.config.retries + 1):
            attempts_made = attempt + 1
            retryable_error = True
            started = time.perf_counter()
            headers = {
                "Content-Type": "application/json",
                "User-Agent": f"scaffoldscope/{__version__}",
            }
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            request = urllib.request.Request(
                self.endpoint,
                data=encoded,
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=self.config.timeout_seconds
                ) as response:
                    body = response.read().decode("utf-8")
                    request_id = response.headers.get("x-request-id")
                parsed = strict_json_loads(body)
                return self._parse_response(
                    parsed,
                    messages=messages,
                    request_id=request_id,
                    latency_seconds=time.perf_counter() - started,
                    attempt_count=attempt + 1,
                )
            except urllib.error.HTTPError as exc:
                detail = self._safe_error(exc.read(4096).decode("utf-8", errors="replace"))
                last_error = ModelError(f"Provider returned HTTP {exc.code}: {detail}")
                if exc.code < 500 and exc.code != 429:
                    retryable_error = False
            except (
                ModelError,
                urllib.error.URLError,
                TimeoutError,
                json.JSONDecodeError,
                KeyError,
                TypeError,
                ValueError,
                IndexError,
                AttributeError,
            ) as exc:
                last_error = exc
            will_retry = retryable_error and attempt < self.config.retries
            if self.event_callback is not None:
                self.event_callback(
                    {
                        "attempt": attempt + 1,
                        "maximum_attempts": self.config.retries + 1,
                        "retrying": will_retry,
                        "latency_seconds": time.perf_counter() - started,
                        "error_type": type(last_error).__name__ if last_error else "unknown",
                        "error": self._safe_error(last_error),
                    }
                )
            if will_retry:
                time.sleep(min(4.0, 0.5 * (2**attempt)))
            else:
                break
        raise ModelError(
            f"Model request failed after {attempts_made} attempt(s): {self._safe_error(last_error)}"
        )

    def _parse_response(
        self,
        value: dict[str, Any],
        *,
        messages: Sequence[dict[str, str]],
        request_id: str | None,
        latency_seconds: float,
        attempt_count: int,
    ) -> ModelResponse:
        if not isinstance(value, dict):
            raise ModelError("Provider response must be a JSON object")
        choices = value.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ModelError("Provider response has no valid choices[0]")
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ModelError("Provider response choice has no message object")
        content_value = message.get("content", "")
        if isinstance(content_value, list):
            content = "".join(
                str(item.get("text", "")) if isinstance(item, dict) else str(item)
                for item in content_value
            )
        elif content_value is None:
            content = ""
        else:
            content = str(content_value)
        usage_value = value.get("usage") or {}
        if not isinstance(usage_value, dict):
            raise ModelError("Provider usage must be a JSON object")
        details = usage_value.get("prompt_tokens_details", {}) or {}
        completion_details = usage_value.get("completion_tokens_details", {}) or {}
        if not isinstance(details, dict) or not isinstance(completion_details, dict):
            raise ModelError("Provider token detail fields must be JSON objects")
        provider_usage = "prompt_tokens" in usage_value and "completion_tokens" in usage_value

        def token_count(raw: Any, label: str) -> int:
            if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
                raise ModelError(f"Provider {label} must be a non-negative integer")
            return raw

        input_tokens = token_count(
            usage_value.get("prompt_tokens", self.counter.messages(messages)),
            "prompt_tokens",
        )
        output_tokens = token_count(
            usage_value.get("completion_tokens", self.counter.text(content)),
            "completion_tokens",
        )
        cache_read_tokens = token_count(
            details.get("cached_tokens", usage_value.get("cache_read_input_tokens", 0)) or 0,
            "cache_read_tokens",
        )
        cache_write_tokens = token_count(
            details.get(
                "cache_write_tokens",
                usage_value.get("cache_creation_input_tokens", 0),
            )
            or 0,
            "cache_write_tokens",
        )
        reasoning_tokens = token_count(
            completion_details.get("reasoning_tokens", 0) or 0,
            "reasoning_tokens",
        )
        if cache_read_tokens + cache_write_tokens > input_tokens:
            raise ModelError("Provider cache token counts exceed prompt_tokens")
        usage = Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            reasoning_tokens=reasoning_tokens,
            source="provider" if provider_usage else "estimated_char4",
        )
        return ModelResponse(
            content=content,
            usage=usage,
            request_id=(
                str(request_id or value.get("id"))
                if request_id is not None or value.get("id") is not None
                else None
            ),
            provider_model=str(value.get("model", self.config.name)),
            finish_reason=choice.get("finish_reason"),
            latency_seconds=latency_seconds,
            raw_metadata={
                "system_fingerprint": value.get("system_fingerprint"),
                "created": value.get("created"),
                "attempt_count": attempt_count,
                "raw_usage": usage_value,
            },
        )


def make_model(
    config: ModelConfig,
    task: TaskSpec,
    counter: Char4TokenCounter,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
    registry: PluginRegistry | None = None,
) -> ChatModel:
    if config.provider == "scripted":
        return ScriptedModel(config, task, counter)
    if config.provider == "openai_compatible":
        return OpenAICompatibleModel(config, counter, event_callback)
    from scaffoldscope.plugins import ModelProviderRequest, PluginLoadError, PluginRegistry

    selected_registry = registry or PluginRegistry.discover()
    loaded = selected_registry.load_model_provider(config.provider)
    model = loaded.factory(
        ModelProviderRequest(
            config=config,
            task=task,
            counter=counter,
            event_callback=event_callback,
            options=config.plugin_options,
        )
    )
    if not callable(getattr(model, "complete", None)):
        raise PluginLoadError(
            f"Model-provider plugin {loaded.info.name!r} returned {type(model).__name__}, "
            "which has no callable complete method",
            hint="Return an object implementing the ChatModel protocol from the factory.",
        )
    return model
