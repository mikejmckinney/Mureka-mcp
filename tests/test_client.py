import json

import httpx
import pytest

from mureka_mcp.client import MurekaAPIError, MurekaClient


class FailingStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"partial audio"
        raise httpx.ReadError("download interrupted")


@pytest.mark.asyncio
async def test_submit_instrumental_sends_current_contract_without_retry():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={"id": "task-123", "model": "mureka-9", "status": "preparing"},
        )

    client = MurekaClient(
        "secret-key",
        transport=httpx.MockTransport(handler),
    )

    result = await client.submit_instrumental(
        prompt=None,
        instrumental_id="file-123",
        model="mureka-9",
        n=1,
    )

    assert result["id"] == "task-123"
    assert len(requests) == 1
    assert requests[0].url.path == "/v1/instrumental/generate"
    assert requests[0].headers["Authorization"] == "Bearer secret-key"
    assert json.loads(requests[0].content) == {
        "model": "mureka-9",
        "instrumental_id": "file-123",
        "n": 1,
    }


@pytest.mark.asyncio
async def test_api_errors_do_not_expose_api_key():
    def handler(request):
        raise httpx.ConnectTimeout("network timeout", request=request)

    client = MurekaClient(
        "do-not-leak-this-key",
        request_timeout=3,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(MurekaAPIError) as error:
        await client.get_instrumental_task("task-123")

    assert "do-not-leak-this-key" not in str(error.value)
    assert "GET /v1/instrumental/query/task-123 timed out after 3 seconds" in str(
        error.value
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "message"),
    [
        (401, "check MUREKA_API_KEY"),
        (402, "insufficient balance"),
        (429, "rate limited"),
        (503, "service error"),
    ],
)
async def test_http_errors_are_actionable_without_response_body(
    status_code, message
):
    def handler(request):
        return httpx.Response(
            status_code,
            text="sensitive upstream details",
            request=request,
        )

    client = MurekaClient(
        "secret-key",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(MurekaAPIError, match=message) as error:
        await client.get_instrumental_task("task-123")

    assert "secret-key" not in str(error.value)
    assert "sensitive upstream details" not in str(error.value)


@pytest.mark.asyncio
async def test_upload_uses_instrumental_purpose(monkeypatch, tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={"id": "file-123", "purpose": "instrumental"},
        )

    reference = tmp_path / "reference.mp3"
    reference.write_bytes(b"private reference contents")
    client = MurekaClient(
        "secret-key",
        transport=httpx.MockTransport(handler),
    )

    result = await client.upload_instrumental_reference(reference)

    assert result == {"id": "file-123", "purpose": "instrumental"}
    assert len(requests) == 1
    assert requests[0].url.path == "/v1/files/upload"
    assert b'name="purpose"' in requests[0].content
    assert b"instrumental" in requests[0].content


@pytest.mark.asyncio
async def test_failed_download_does_not_leave_partial_output(tmp_path):
    def handler(request):
        return httpx.Response(200, stream=FailingStream(), request=request)

    client = MurekaClient(
        "secret-key",
        transport=httpx.MockTransport(handler),
    )
    output_path = tmp_path / "result.wav"

    with pytest.raises(MurekaAPIError, match="Audio download failed"):
        await client.download_file("https://cdn.example/result.wav", output_path)

    assert not output_path.exists()
    assert not (tmp_path / "result.wav.part").exists()
