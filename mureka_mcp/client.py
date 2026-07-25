from pathlib import Path
from typing import Any

import httpx


class MurekaAPIError(RuntimeError):
    """An actionable Mureka API failure that excludes sensitive request data."""


def _http_error_message(method: str, path: str, status_code: int) -> str:
    prefix = f"Mureka {method} {path} failed with HTTP {status_code}"
    if status_code == 400:
        return f"{prefix}; check the request parameters"
    if status_code == 401:
        return f"{prefix}; check MUREKA_API_KEY"
    if status_code == 402:
        return f"{prefix}; the account has insufficient balance"
    if status_code == 429:
        return f"{prefix}; the account is rate limited, wait before retrying"
    if status_code >= 500:
        return f"{prefix}; Mureka reported a service error, retry later"
    return prefix


class MurekaClient:
    def __init__(
        self,
        api_key: str,
        api_url: str = "https://api.mureka.ai",
        request_timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("MUREKA_API_KEY is not set")
        self.api_key = api_key
        self.api_url = api_url.rstrip("/")
        self.request_timeout = request_timeout
        self.transport = transport

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    async def _request_json(
        self, method: str, path: str, **kwargs: Any
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                timeout=self.request_timeout, transport=self.transport
            ) as client:
                response = await client.request(
                    method,
                    f"{self.api_url}{path}",
                    headers=self.headers,
                    **kwargs,
                )
                response.raise_for_status()
                return response.json()
        except httpx.TimeoutException as exc:
            raise MurekaAPIError(
                f"Mureka {method} {path} timed out after "
                f"{self.request_timeout} seconds"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise MurekaAPIError(
                _http_error_message(method, path, exc.response.status_code)
            ) from exc
        except (httpx.RequestError, ValueError) as exc:
            raise MurekaAPIError(
                f"Mureka {method} {path} failed: {type(exc).__name__}"
            ) from exc

    async def submit_instrumental(
        self,
        *,
        prompt: str | None,
        instrumental_id: str | None,
        model: str,
        n: int | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": model}
        if prompt is not None:
            payload["prompt"] = prompt
        if instrumental_id is not None:
            payload["instrumental_id"] = instrumental_id
        if n is not None:
            payload["n"] = n
        return await self._request_json(
            "POST", "/v1/instrumental/generate", json=payload
        )

    async def get_instrumental_task(self, task_id: str) -> dict[str, Any]:
        return await self._request_json(
            "GET", f"/v1/instrumental/query/{task_id}"
        )

    async def upload_instrumental_reference(
        self, input_path: Path
    ) -> dict[str, Any]:
        with input_path.open("rb") as audio_file:
            return await self._request_json(
                "POST",
                "/v1/files/upload",
                data={"purpose": "instrumental"},
                files={"file": (input_path.name, audio_file)},
            )

    async def download_file(self, url: str, output_path: Path) -> None:
        partial_path = output_path.with_suffix(f"{output_path.suffix}.part")
        try:
            partial_path.unlink(missing_ok=True)
            async with httpx.AsyncClient(
                timeout=self.request_timeout, transport=self.transport
            ) as client:
                async with client.stream("GET", url) as response:
                    response.raise_for_status()
                    with partial_path.open("wb") as output_file:
                        async for chunk in response.aiter_bytes():
                            output_file.write(chunk)
            partial_path.replace(output_path)
        except httpx.TimeoutException as exc:
            raise MurekaAPIError(
                f"Audio download timed out after {self.request_timeout} seconds"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise MurekaAPIError(
                f"Audio download failed with HTTP {exc.response.status_code}"
            ) from exc
        except httpx.RequestError as exc:
            raise MurekaAPIError(
                f"Audio download failed: {type(exc).__name__}"
            ) from exc
        except OSError as exc:
            raise MurekaAPIError(
                f"Audio file write failed: {type(exc).__name__}"
            ) from exc
        finally:
            partial_path.unlink(missing_ok=True)
