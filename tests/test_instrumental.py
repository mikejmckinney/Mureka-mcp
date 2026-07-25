from pathlib import Path

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from mureka_mcp import api


class FakeClient:
    def __init__(self, task_results=None):
        self.task_results = list(task_results or [])
        self.submission = None
        self.download = None
        self.upload = None

    async def submit_instrumental(self, **kwargs):
        self.submission = kwargs
        return {
            "id": "task-123",
            "created_at": 100,
            "model": kwargs["model"],
            "status": "preparing",
        }

    async def get_instrumental_task(self, task_id):
        if self.task_results:
            return self.task_results.pop(0)
        return {"id": task_id, "model": "mureka-9", "status": "running"}

    async def download_file(self, url, output_path):
        self.download = (url, output_path)
        output_path.write_bytes(b"audio")

    async def upload_instrumental_reference(self, input_path):
        self.upload = input_path
        return {
            "id": "file-123",
            "filename": input_path.name,
            "bytes": input_path.stat().st_size,
            "created_at": 100,
            "purpose": "instrumental",
        }


@pytest.fixture
def provenance():
    return {
        "source": "Original composition",
        "license": "Project-owned",
        "author": "Example Studio",
        "attribution": "None required",
    }


@pytest.mark.asyncio
async def test_submit_instrumental_exposes_model_count_reference_and_metadata(
    monkeypatch, provenance
):
    client = FakeClient()
    monkeypatch.setattr(api, "get_client", lambda: client)

    result = await api.submit_instrumental_generation(
        instrumental_id="file-123",
        model="mureka-9",
        n=1,
        **provenance,
    )

    assert client.submission == {
        "prompt": None,
        "instrumental_id": "file-123",
        "model": "mureka-9",
        "n": 1,
    }
    assert result == {
        "task_id": "task-123",
        "model": "mureka-9",
        "status": "preparing",
        "created_at": 100,
        "finished_at": None,
        "failure_reason": None,
        "outputs": [],
        "provenance": provenance,
        "cost": {
            "amount": None,
            "currency": None,
            "source": "not_provided_by_api",
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prompt", "instrumental_id"),
    [(None, None), ("ambient", "file-123")],
)
async def test_submit_instrumental_requires_exactly_one_control(
    prompt, instrumental_id
):
    with pytest.raises(ValueError, match="exactly one"):
        await api.submit_instrumental_generation(
            prompt=prompt,
            instrumental_id=instrumental_id,
            model="auto",
            n=1,
        )


@pytest.mark.asyncio
async def test_wait_for_instrumental_uses_async_bounded_polling(monkeypatch):
    client = FakeClient(
        [
            {"id": "task-123", "model": "mureka-9", "status": "running"},
            {
                "id": "task-123",
                "model": "mureka-9",
                "status": "succeeded",
                "finished_at": 200,
                "choices": [
                    {
                        "index": 0,
                        "id": "choice-123",
                        "url": "https://cdn.example/result.mp3",
                        "wav_url": "https://cdn.example/result.wav",
                        "duration": 120000,
                    }
                ],
            },
        ]
    )
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(api, "get_client", lambda: client)
    monkeypatch.setattr(api.asyncio, "sleep", fake_sleep)

    result = await api.wait_for_instrumental_task(
        "task-123", timeout_seconds=30, poll_interval_seconds=2
    )

    assert sleeps == [2]
    assert result["status"] == "succeeded"
    assert result["outputs"][0]["urls"]["wav"] == "https://cdn.example/result.wav"


@pytest.mark.asyncio
async def test_wait_for_instrumental_reports_task_and_last_status_on_timeout(
    monkeypatch,
):
    client = FakeClient()
    clock = iter([0.0, 0.5, 1.1])

    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(api, "get_client", lambda: client)
    monkeypatch.setattr(api, "monotonic", lambda: next(clock))
    monkeypatch.setattr(api.asyncio, "sleep", fake_sleep)

    with pytest.raises(
        TimeoutError, match="task-123.*1 seconds.*last status: running"
    ):
        await api.wait_for_instrumental_task(
            "task-123", timeout_seconds=1, poll_interval_seconds=0.1
        )


@pytest.mark.asyncio
async def test_download_wav_fails_instead_of_saving_mp3_with_wav_extension(
    monkeypatch, tmp_path
):
    client = FakeClient(
        [
            {
                "id": "task-123",
                "model": "mureka-9",
                "status": "succeeded",
                "choices": [
                    {
                        "index": 0,
                        "id": "choice-123",
                        "url": "https://cdn.example/result.mp3",
                        "duration": 120000,
                    }
                ],
            }
        ]
    )
    monkeypatch.setattr(api, "get_client", lambda: client)

    with pytest.raises(ValueError, match="WAV output is unavailable"):
        await api.download_instrumental(
            "task-123",
            output_format="wav",
            output_directory=str(tmp_path),
        )

    assert client.download is None


@pytest.mark.asyncio
async def test_download_rejects_task_id_path_traversal(monkeypatch, tmp_path):
    client = FakeClient()
    monkeypatch.setattr(api, "get_client", lambda: client)

    with pytest.raises(ValueError, match="task_id contains unsupported characters"):
        await api.download_instrumental(
            "../../escape",
            output_directory=str(tmp_path),
        )

    assert client.task_results == []


@pytest.mark.asyncio
async def test_download_uses_selected_format_and_refuses_overwrite(
    monkeypatch, tmp_path
):
    task = {
        "id": "task-123",
        "model": "mureka-9",
        "status": "succeeded",
        "choices": [
            {
                "index": 1,
                "id": "choice-456",
                "url": "https://cdn.example/result.mp3",
                "wav_url": "https://cdn.example/result.wav",
                "duration": 120000,
            }
        ],
    }
    client = FakeClient([task, task])
    monkeypatch.setattr(api, "get_client", lambda: client)

    result = await api.download_instrumental(
        "task-123",
        choice_index=1,
        output_format="wav",
        output_directory=str(tmp_path),
    )

    output_path = Path(result["output_path"])
    assert output_path.read_bytes() == b"audio"
    assert client.download == ("https://cdn.example/result.wav", output_path)

    with pytest.raises(FileExistsError, match="overwrite=False"):
        await api.download_instrumental(
            "task-123",
            choice_index=1,
            output_format="wav",
            output_directory=str(tmp_path),
        )


@pytest.mark.asyncio
async def test_upload_reference_returns_provenance_without_file_contents(
    monkeypatch, tmp_path, provenance
):
    reference = tmp_path / "reference.mp3"
    reference.write_bytes(b"licensed audio")
    client = FakeClient()
    monkeypatch.setattr(api, "get_client", lambda: client)

    result = await api.upload_instrumental_reference(
        str(reference), **provenance
    )

    assert client.upload == reference
    assert result["file_id"] == "file-123"
    assert result["purpose"] == "instrumental"
    assert result["provenance"] == provenance
    assert "licensed audio" not in str(result)


@pytest.mark.asyncio
async def test_relative_upload_cannot_escape_configured_base(monkeypatch, tmp_path):
    base_path = tmp_path / "allowed"
    base_path.mkdir()
    outside = tmp_path / "outside.mp3"
    outside.write_bytes(b"private audio")
    client = FakeClient()
    monkeypatch.setattr(api, "global_base_path", str(base_path))
    monkeypatch.setattr(api, "get_client", lambda: client)

    with pytest.raises(ValueError, match="outside MUREKA_MCP_BASE_PATH"):
        await api.upload_instrumental_reference("../outside.mp3")

    assert client.upload is None


@pytest.mark.asyncio
async def test_tools_publish_cost_and_read_only_annotations():
    tools = {tool.name: tool for tool in await api.mcp.list_tools()}

    submit = tools["submit_instrumental_generation"]
    assert submit.annotations.readOnlyHint is False
    assert submit.annotations.idempotentHint is False
    assert submit.inputSchema["properties"]["n"]["minimum"] == 1
    assert submit.inputSchema["properties"]["n"]["maximum"] == 3
    assert {
        "task_id",
        "model",
        "status",
        "outputs",
        "provenance",
        "cost",
    }.issubset(submit.outputSchema["properties"])

    query = tools["get_instrumental_task"]
    assert query.annotations.readOnlyHint is True
    assert query.annotations.idempotentHint is True


@pytest.mark.asyncio
async def test_query_returns_complete_empty_provenance(monkeypatch):
    client = FakeClient(
        [{"id": "task-123", "model": "mureka-9", "status": "running"}]
    )
    monkeypatch.setattr(api, "get_client", lambda: client)

    result = await api.get_instrumental_task("task-123")

    assert result["provenance"] == {
        "source": None,
        "license": None,
        "author": None,
        "attribution": None,
    }


@pytest.mark.asyncio
async def test_legacy_generate_instrumental_keeps_text_file_response(
    monkeypatch, tmp_path
):
    client = FakeClient(
        [
            {
                "id": "task-123",
                "model": "mureka-9",
                "status": "succeeded",
                "choices": [
                    {
                        "index": 0,
                        "id": "choice-123",
                        "url": "https://cdn.example/original-name.mp3",
                        "duration": 120000,
                    }
                ],
            }
        ]
    )
    monkeypatch.setattr(api, "get_client", lambda: client)
    monkeypatch.setattr(api, "api_key", None)

    result = await api.generate_instrumental(
        prompt="ambient game music",
        output_directory=str(tmp_path),
    )

    output_path = tmp_path / "original-name.mp3"
    assert output_path.read_bytes() == b"audio"
    assert result[0].text == f"Success. File saved as: {output_path}"


@pytest.mark.asyncio
async def test_submit_instrumental_through_mcp_protocol(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(api, "get_client", lambda: client)

    async with create_connected_server_and_client_session(
        api.mcp, raise_exceptions=True
    ) as session:
        result = await session.call_tool(
            "submit_instrumental_generation",
            {
                "prompt": "ambient game music",
                "model": "mureka-9",
                "n": 1,
            },
        )

    assert result.isError is False
    assert result.structuredContent["task_id"] == "task-123"
    assert result.structuredContent["model"] == "mureka-9"
    assert result.structuredContent["cost"]["source"] == "not_provided_by_api"
