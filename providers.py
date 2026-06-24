"""AI provider abstraction with automatic failover on rate limits.

Each Provider wraps one external API (Claude, OpenAI, ...). A ProviderChain
holds an ordered, prioritized list of configured providers: when the active
one reports a rate limit / quota error, the chain switches to the next one
and keeps going, instead of failing the whole job. An exhausted provider is
retried again once its quota is expected to reset.
"""

import abc
import json
import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

_log = logging.getLogger("describer.providers")

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


class RateLimitExceeded(Exception):
    """Raised by a Provider when the API reports a rate limit / quota error."""

    def __init__(self, provider_name: str, retry_after: float | None = None):
        super().__init__(f"{provider_name}: rate limit exceeded")
        self.provider_name = provider_name
        self.retry_after = retry_after


class AllProvidersExhausted(Exception):
    """Raised by ProviderChain when every configured provider is rate-limited."""

    def __init__(self, resume_at: datetime):
        super().__init__(f"All providers exhausted, next retry at {resume_at.isoformat()}")
        self.resume_at = resume_at


def parse_description_response(content: str) -> dict[str, str]:
    """Parse model output as JSON; fall back to plain-text in 'beskrivning'."""
    text = (content or "").strip()
    match = _JSON_BLOCK.search(text)
    if match:
        try:
            data = json.loads(match.group(0))
            return {
                "beskrivning": str(data.get("beskrivning", "")).strip(),
                "varför": str(data.get("varför") or data.get("varfor", "")).strip(),
            }
        except json.JSONDecodeError:
            pass
    return {"beskrivning": text, "varför": ""}


class Provider(abc.ABC):
    name: str

    @abc.abstractmethod
    def generate(self, system_prompt: str, user_message: str, model: str) -> str:
        """Return the raw text content of the model's reply.

        Must raise RateLimitExceeded when the API reports a rate limit/quota
        error, so the chain can fail over to the next provider.
        """

    @abc.abstractmethod
    def available_models(self) -> list[str]:
        ...

    def check_connection(self) -> bool:
        try:
            self.available_models()
            return True
        except Exception:
            return False


PROVIDER_LABELS = {
    "anthropic": "Claude (Anthropic)",
    "openai": "ChatGPT (OpenAI)",
    "gemini": "Gemini (Google)",
    "azure_openai": "Azure OpenAI Service",
}


class AnthropicProvider(Provider):
    name = "anthropic"

    DEFAULT_MODELS = ["claude-sonnet-4-6", "claude-haiku-4-5-20251001", "claude-opus-4-8"]

    def __init__(self, api_key: str):
        self._api_key = api_key
        self._client = None

    def _get_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def generate(self, system_prompt: str, user_message: str, model: str) -> str:
        import anthropic
        client = self._get_client()
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=1024,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
        except anthropic.RateLimitError as e:
            raise RateLimitExceeded(self.name, retry_after=_retry_after_seconds(e)) from e
        except anthropic.BadRequestError as e:
            if _is_billing_exhausted(e):
                raise RateLimitExceeded(self.name, retry_after=_BILLING_RETRY_SECONDS) from e
            raise
        return "".join(block.text for block in resp.content if block.type == "text")

    def available_models(self) -> list[str]:
        return list(self.DEFAULT_MODELS)


class OpenAIProvider(Provider):
    name = "openai"

    DEFAULT_MODELS = ["gpt-4.1", "gpt-4.1-mini", "gpt-4o"]

    def __init__(self, api_key: str):
        self._api_key = api_key
        self._client = None

    def _get_client(self):
        if self._client is None:
            import openai
            self._client = openai.OpenAI(api_key=self._api_key)
        return self._client

    def generate(self, system_prompt: str, user_message: str, model: str) -> str:
        import openai
        client = self._get_client()
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
            )
        except openai.RateLimitError as e:
            raise RateLimitExceeded(self.name, retry_after=_retry_after_seconds(e)) from e
        except openai.APIStatusError as e:
            if _is_billing_exhausted(e):
                raise RateLimitExceeded(self.name, retry_after=_BILLING_RETRY_SECONDS) from e
            raise
        return resp.choices[0].message.content or ""

    def available_models(self) -> list[str]:
        return list(self.DEFAULT_MODELS)


class GeminiProvider(Provider):
    name = "gemini"

    DEFAULT_MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro"]

    def __init__(self, api_key: str):
        self._api_key = api_key
        self._client = None

    def _get_client(self):
        if self._client is None:
            from google import genai
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    def generate(self, system_prompt: str, user_message: str, model: str) -> str:
        from google.genai import errors as genai_errors
        client = self._get_client()
        try:
            resp = client.models.generate_content(
                model=model,
                contents=user_message,
                config={"system_instruction": system_prompt},
            )
        except genai_errors.ClientError as e:
            if getattr(e, "code", None) == 429:
                raise RateLimitExceeded(self.name, retry_after=_retry_after_seconds(e)) from e
            if _is_billing_exhausted(e):
                raise RateLimitExceeded(self.name, retry_after=_BILLING_RETRY_SECONDS) from e
            raise
        return resp.text or ""

    def available_models(self) -> list[str]:
        return list(self.DEFAULT_MODELS)


class AzureOpenAIProvider(Provider):
    """OpenAI models hosted on the caller's own Azure subscription.

    Unlike the other providers, the model is selected by the deployment
    name configured in the caller's Azure resource, not a fixed model id —
    so there's no static model list, and the "model" passed to generate()
    is the deployment name set up when the key was saved.
    """

    name = "azure_openai"

    def __init__(self, api_key: str, endpoint: str = "", deployment: str = "", api_version: str = "2024-10-21"):
        self._api_key = api_key
        self._endpoint = endpoint
        self._deployment = deployment
        self._api_version = api_version
        self._client = None

    def _get_client(self):
        if self._client is None:
            import openai
            self._client = openai.AzureOpenAI(
                api_key=self._api_key,
                azure_endpoint=self._endpoint,
                api_version=self._api_version,
            )
        return self._client

    def generate(self, system_prompt: str, user_message: str, model: str) -> str:
        import openai
        client = self._get_client()
        try:
            resp = client.chat.completions.create(
                model=model or self._deployment,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
            )
        except openai.RateLimitError as e:
            raise RateLimitExceeded(self.name, retry_after=_retry_after_seconds(e)) from e
        except openai.APIStatusError as e:
            if _is_billing_exhausted(e):
                raise RateLimitExceeded(self.name, retry_after=_BILLING_RETRY_SECONDS) from e
            raise
        return resp.choices[0].message.content or ""

    def available_models(self) -> list[str]:
        return [self._deployment] if self._deployment else []


def _retry_after_seconds(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) if response is not None else None
    if not headers:
        return None
    value = headers.get("retry-after")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


# En "för låg kreditbalans"/"saknar kvot"-fel kommer inte alltid som ett
# rate-limit-statuskod (Anthropic skickar t.ex. 400, inte 429) — men ska
# behandlas likadant: byt till nästa leverantör i kedjan istället för att
# misslyckas rad för rad mot samma uttömda leverantör. Matchar på text
# eftersom SDK:erna saknar en gemensam exception-typ för detta specifikt.
_BILLING_EXHAUSTED_PHRASES = (
    "credit balance",
    "insufficient_quota",
    "insufficient quota",
    "exceeded your current quota",
    "billing",
)
_BILLING_RETRY_SECONDS = 6 * 3600  # ingen API-ledtråd om när krediter fylls på — gissa 6h


def _is_billing_exhausted(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(phrase in text for phrase in _BILLING_EXHAUSTED_PHRASES)


def _next_reset(retry_after: float | None) -> datetime:
    """When a provider's quota is expected to come back.

    If the API told us how long to wait, honor that. Otherwise assume a
    daily free-tier quota and wait until the next UTC midnight.
    """
    now = datetime.now(timezone.utc)
    if retry_after:
        return now + timedelta(seconds=retry_after)
    tomorrow = (now + timedelta(days=1)).date()
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=timezone.utc)


@dataclass
class ProviderSpec:
    provider: Provider
    model: str


class ProviderChain:
    """An ordered list of providers; falls over to the next one on rate limits."""

    def __init__(self, specs: list[ProviderSpec]):
        if not specs:
            raise ValueError("ProviderChain needs at least one provider")
        self._specs = specs
        self._active_idx = 0
        self._exhausted_until: dict[int, datetime] = {}
        self._lock = threading.RLock()

    def _available_index(self, now: datetime) -> int | None:
        for i in range(len(self._specs)):
            until = self._exhausted_until.get(i)
            if until is None or until <= now:
                return i
        return None

    def current_provider_name(self) -> str | None:
        with self._lock:
            if self._active_idx is None:
                return None
            return self._specs[self._active_idx].provider.name

    def next_resume_at(self) -> datetime:
        with self._lock:
            if not self._exhausted_until:
                return datetime.now(timezone.utc)
            return min(self._exhausted_until.values())

    def call(self, system_prompt: str, user_message: str) -> str:
        """Raw text reply from the active provider, with automatic failover."""
        while True:
            with self._lock:
                now = datetime.now(timezone.utc)
                idx = self._available_index(now)
                if idx is None:
                    raise AllProvidersExhausted(self.next_resume_at())
                self._active_idx = idx
                spec = self._specs[idx]

            try:
                return spec.provider.generate(system_prompt, user_message, spec.model)
            except RateLimitExceeded as e:
                with self._lock:
                    self._exhausted_until[idx] = _next_reset(e.retry_after)
                _log.warning(
                    "provider %s exhausted, resuming at %s",
                    spec.provider.name,
                    self._exhausted_until[idx].isoformat(),
                )
                continue

    def generate(self, system_prompt: str, user_message: str) -> dict[str, str]:
        return parse_description_response(self.call(system_prompt, user_message))
