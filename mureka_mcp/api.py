# api.py
import asyncio
import os
import re
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

import httpx
import requests
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent
from pydantic import Field
from typing_extensions import TypedDict

from mureka_mcp.client import MurekaClient

mcp = FastMCP("Mureka")
# setup API key，for calling mureka API，please refer to the method for obtaining：https://platform.mureka.ai/apiKeys
# os.environ["MUREKA_API_KEY"] = "<MUREKA_API_KEY>"
api_key = os.getenv('MUREKA_API_KEY')
global_base_path = os.getenv("MUREKA_MCP_BASE_PATH")
api_url = os.getenv('MUREKA_API_URL')
if api_url is None:
    api_url = "https://api.mureka.ai"

default_time_out = 60.0  # seconds
time_out_env = os.getenv('TIME_OUT_SECONDS')
if time_out_env is not None:
    default_time_out = float(time_out_env)

TERMINAL_STATUSES = {"succeeded", "failed", "timeouted", "cancelled"}
MODEL_VALUES = {"auto", "mureka-7.6", "mureka-8", "mureka-9"}
FORMAT_URL_KEYS = {"mp3": "url", "flac": "flac_url", "wav": "wav_url"}
monotonic = time.monotonic
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class InstrumentalURLs(TypedDict):
    mp3: str | None
    flac: str | None
    wav: str | None
    stream: str | None


class InstrumentalOutput(TypedDict):
    index: int | None
    id: str | None
    duration_ms: int | None
    urls: InstrumentalURLs


class Provenance(TypedDict):
    source: str | None
    license: str | None
    author: str | None
    attribution: str | None


class CostMetadata(TypedDict):
    amount: float | None
    currency: str | None
    source: str


class InstrumentalTaskResult(TypedDict):
    task_id: str | None
    model: str | None
    status: str | None
    created_at: int | None
    finished_at: int | None
    failure_reason: str | None
    outputs: list[InstrumentalOutput]
    provenance: Provenance
    cost: CostMetadata


class InstrumentalUploadResult(TypedDict):
    file_id: str | None
    filename: str | None
    bytes: int | None
    created_at: int | None
    purpose: str
    provenance: Provenance


class InstrumentalDownloadResult(TypedDict):
    task_id: str
    choice_index: int
    choice_id: str | None
    model: str | None
    format: str
    duration_ms: int | None
    output_path: str
    source_url: str


def is_file_writeable(path: Path) -> bool:
    if path.exists():
        return os.access(path, os.W_OK)
    parent_dir = path.parent
    return os.access(parent_dir, os.W_OK)


def make_output_path(
        output_directory: str | None, base_path: str | None = None
) -> Path:
    output_path = None
    if output_directory is None:
        output_path = Path.home() / "Desktop"
    elif not os.path.isabs(output_directory) and base_path:
        resolved_base = Path(os.path.expanduser(base_path)).resolve()
        output_path = (resolved_base / output_directory).resolve()
        if not output_path.is_relative_to(resolved_base):
            raise ValueError("Output directory is outside MUREKA_MCP_BASE_PATH")
    else:
        output_path = Path(os.path.expanduser(output_directory))
    if not is_file_writeable(output_path):
        raise Exception(f"Directory ({output_path}) is not writeable")
    output_path.mkdir(parents=True, exist_ok=True)
    return output_path


def extract_filename_from_url(url):
    # 解析URL
    parsed_url = urlparse(url)
    # 获取路径的最后一个部分，即文件名
    filename = parsed_url.path.split('/')[-1]
    return filename


def check_audio_file(path: Path) -> bool:
    audio_extensions = {
        ".wav",
        ".mp3",
        ".m4a",
        ".aac",
        ".ogg",
        ".flac",
        ".mp4",
        ".avi",
        ".mov",
        ".wmv",
    }
    return path.suffix.lower() in audio_extensions


def handle_input_file(file_path: str, audio_content_check: bool = True) -> Path:
    if not os.path.isabs(file_path):
        if not global_base_path:
            raise Exception(
                "File path must be absolute if MUREKA_MCP_BASE_PATH is not set"
            )
        resolved_base = Path(os.path.expanduser(global_base_path)).resolve()
        path = (resolved_base / file_path).resolve()
        if not path.is_relative_to(resolved_base):
            raise ValueError("Input file is outside MUREKA_MCP_BASE_PATH")
    else:
        path = Path(file_path)
    if not path.exists() and path.parent.exists():
        raise Exception(f"File ({path}) does not exist")
    elif not path.exists():
        raise Exception(f"File ({path}) does not exist")
    elif not path.is_file():
        raise Exception(f"File ({path}) is not a file")

    if audio_content_check and not check_audio_file(path):
        raise Exception(f"File ({path}) is not an audio or video file")
    return path


def get_client() -> MurekaClient:
    key = os.getenv("MUREKA_API_KEY") or api_key
    return MurekaClient(key or "", api_url, request_timeout=30.0)


def _provenance(
    source: str | None,
    license: str | None,
    author: str | None,
    attribution: str | None,
) -> Provenance:
    return {
        "source": source,
        "license": license,
        "author": author,
        "attribution": attribution,
    }


def _instrumental_result(
    task: dict[str, Any],
    provenance: Provenance | None = None,
) -> InstrumentalTaskResult:
    outputs = []
    for choice in task.get("choices", []):
        outputs.append(
            {
                "index": choice.get("index"),
                "id": choice.get("id"),
                "duration_ms": choice.get("duration"),
                "urls": {
                    "mp3": choice.get("url"),
                    "flac": choice.get("flac_url"),
                    "wav": choice.get("wav_url"),
                    "stream": choice.get("stream_url"),
                },
            }
        )
    return {
        "task_id": task.get("id"),
        "model": task.get("model"),
        "status": task.get("status"),
        "created_at": task.get("created_at"),
        "finished_at": task.get("finished_at"),
        "failure_reason": task.get("failed_reason"),
        "outputs": outputs,
        "provenance": provenance or _provenance(None, None, None, None),
        "cost": {
            "amount": None,
            "currency": None,
            "source": "not_provided_by_api",
        },
    }


def _validate_generation_controls(
    prompt: str | None,
    instrumental_id: str | None,
    model: str,
    n: int,
) -> tuple[str | None, str | None]:
    prompt = prompt.strip() if prompt else None
    instrumental_id = instrumental_id.strip() if instrumental_id else None
    if (prompt is None) == (instrumental_id is None):
        raise ValueError("Provide exactly one of prompt or instrumental_id")
    if model not in MODEL_VALUES:
        raise ValueError(
            f"Unsupported model {model!r}; choose one of {sorted(MODEL_VALUES)}"
        )
    if not 1 <= n <= 3:
        raise ValueError("n must be between 1 and 3")
    return prompt, instrumental_id


def _validate_task_id(task_id: str) -> str:
    task_id = task_id.strip()
    if not task_id:
        raise ValueError("task_id is required")
    if not IDENTIFIER_PATTERN.fullmatch(task_id):
        raise ValueError("task_id contains unsupported characters")
    return task_id


@mcp.tool(
    description="""Submit one bounded instrumental-generation request.

    This cost-bearing operation creates 1-3 candidates. It requires exactly one
    of prompt or instrumental_id and never retries the non-idempotent request.
    The provenance fields are returned for the caller's asset ledger; Mureka
    does not validate licenses or return per-generation cost.
    """,
    annotations={
        "title": "Submit Instrumental Generation",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def submit_instrumental_generation(
    prompt: str | None = None,
    instrumental_id: str | None = None,
    model: Literal["auto", "mureka-7.6", "mureka-8", "mureka-9"] = "auto",
    n: Annotated[
        int,
        Field(
            ge=1,
            le=3,
            description="Number of candidates; each candidate incurs a charge",
        ),
    ] = 1,
    source: str | None = None,
    license: str | None = None,
    author: str | None = None,
    attribution: str | None = None,
) -> InstrumentalTaskResult:
    prompt, instrumental_id = _validate_generation_controls(
        prompt, instrumental_id, model, n
    )
    task = await get_client().submit_instrumental(
        prompt=prompt,
        instrumental_id=instrumental_id,
        model=model,
        n=n,
    )
    if not task.get("id"):
        raise RuntimeError("Mureka did not return an instrumental task ID")
    task.setdefault("model", model)
    return _instrumental_result(
        task, _provenance(source, license, author, attribution)
    )


@mcp.tool(
    description="Query an instrumental task once without creating or modifying it.",
    annotations={
        "title": "Get Instrumental Task",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def get_instrumental_task(task_id: str) -> InstrumentalTaskResult:
    task_id = _validate_task_id(task_id)
    task = await get_client().get_instrumental_task(task_id)
    return _instrumental_result(task)


@mcp.tool(
    description="""Poll an instrumental task until it reaches a terminal state.

    Polling is read-only and bounded by timeout_seconds. Timeout and failure
    errors include the task ID and last observed state so callers can resume
    later with get_instrumental_task.
    """,
    annotations={
        "title": "Wait For Instrumental Task",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def wait_for_instrumental_task(
    task_id: str,
    timeout_seconds: Annotated[float, Field(gt=0, le=3600)] = 300,
    poll_interval_seconds: Annotated[float, Field(gt=0, le=30)] = 5,
) -> InstrumentalTaskResult:
    task_id = _validate_task_id(task_id)
    if not 0 < timeout_seconds <= 3600:
        raise ValueError("timeout_seconds must be between 0 and 3600")
    if not 0 < poll_interval_seconds <= 30:
        raise ValueError("poll_interval_seconds must be between 0 and 30")

    deadline = monotonic() + timeout_seconds
    last_status = "unknown"
    while True:
        task = await get_client().get_instrumental_task(task_id)
        last_status = task.get("status", "unknown")
        if last_status in TERMINAL_STATUSES:
            if last_status != "succeeded":
                reason = task.get("failed_reason") or "No failure reason provided"
                raise RuntimeError(
                    f"Instrumental task {task_id} ended with status "
                    f"{last_status}: {reason}"
                )
            return _instrumental_result(task)
        if monotonic() >= deadline:
            raise TimeoutError(
                f"Instrumental task {task_id} timed out after "
                f"{timeout_seconds:g} seconds (last status: {last_status})"
            )
        await asyncio.sleep(poll_interval_seconds)


@mcp.tool(
    description="""Upload a licensed 30-second MP3 or M4A instrumental reference.

    This mutating operation returns the Mureka file ID plus caller-supplied
    provenance. It never logs or returns the reference-audio contents.
    """,
    annotations={
        "title": "Upload Instrumental Reference",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def upload_instrumental_reference(
    input_file_path: str,
    source: str | None = None,
    license: str | None = None,
    author: str | None = None,
    attribution: str | None = None,
) -> InstrumentalUploadResult:
    input_path = handle_input_file(input_file_path)
    if input_path.suffix.lower() not in {".mp3", ".m4a"}:
        raise ValueError("Instrumental references must be MP3 or M4A files")
    if input_path.stat().st_size > 10 * 1024 * 1024:
        raise ValueError("Instrumental references must not exceed 10 MB")

    uploaded = await get_client().upload_instrumental_reference(input_path)
    if not uploaded.get("id"):
        raise RuntimeError("Mureka did not return an uploaded file ID")
    return {
        "file_id": uploaded.get("id"),
        "filename": uploaded.get("filename"),
        "bytes": uploaded.get("bytes"),
        "created_at": uploaded.get("created_at"),
        "purpose": uploaded.get("purpose", "instrumental"),
        "provenance": _provenance(source, license, author, attribution),
    }


@mcp.tool(
    description="""Download one completed instrumental choice as MP3, FLAC, or WAV.

    The requested format must be present in Mureka's task response. The tool
    never substitutes another codec and refuses to overwrite an existing file
    unless overwrite is explicitly true.
    """,
    annotations={
        "title": "Download Instrumental",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def download_instrumental(
    task_id: str,
    choice_index: Annotated[int, Field(ge=0)] = 0,
    output_format: Literal["mp3", "flac", "wav"] = "mp3",
    output_directory: str | None = None,
    overwrite: bool = False,
) -> InstrumentalDownloadResult:
    task_id = _validate_task_id(task_id)
    if choice_index < 0:
        raise ValueError("choice_index must not be negative")
    task = await get_client().get_instrumental_task(task_id)
    if task.get("status") != "succeeded":
        raise ValueError(
            f"Instrumental task {task_id} is {task.get('status', 'unknown')}, "
            "not succeeded"
        )

    choice = next(
        (
            item
            for item in task.get("choices", [])
            if item.get("index") == choice_index
        ),
        None,
    )
    if choice is None:
        raise ValueError(
            f"Choice index {choice_index} is unavailable for task {task_id}"
        )
    url = choice.get(FORMAT_URL_KEYS[output_format])
    if not url:
        raise ValueError(
            f"{output_format.upper()} output is unavailable for choice "
            f"{choice_index}; choose a format listed by get_instrumental_task"
        )

    output_dir = make_output_path(output_directory, global_base_path)
    output_path = output_dir / f"mureka-{task_id}-{choice_index}.{output_format}"
    if output_path.exists() and not overwrite:
        raise FileExistsError(
            f"File {output_path} already exists and overwrite=False"
        )
    await get_client().download_file(url, output_path)
    return {
        "task_id": task_id,
        "choice_index": choice_index,
        "choice_id": choice.get("id"),
        "model": task.get("model"),
        "format": output_format,
        "duration_ms": choice.get("duration"),
        "output_path": str(output_path),
        "source_url": url,
    }


@mcp.tool(
    description="""Generate lyrics with a given prompt then return the title and lyrics text to the client directly.
    
 ⚠️ COST WARNING: This tool makes an API call to mureka.ai which may incur costs. Only use when explicitly requested by the user.
 
    Args:
        prompt (str): The prompt to generate lyrics for song
        
    Returns:
        The title and lyrics of song.
    """,
    annotations={
        "title": "Generate Lyrics",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def generate_lyrics(prompt: str) -> dict:
    try:
        if not api_key:
            raise Exception("Can not found API key.")
        if prompt == "":
            raise Exception("Prompt text is required.")
        # call mureka API
        url = f"{api_url}/v1/lyrics/generate"

        # set request parameters
        # for more parameter information, please refer to:https://platform.mureka.cn/docs/api/operations/post-v1-lyrics-generate.html
        headers = {'Authorization': 'Bearer {}'.format(api_key),
                   'Content-Type': 'application/json'}
        params = {'prompt': prompt}

        async with httpx.AsyncClient(timeout=None) as client:
            response = await client.post(url, json=params, headers=headers)
            response.raise_for_status()
            result = response.json()

        result_lyrics = result.get("lyrics", "")
        result_title = result.get("title", "")
        return {"lyrics": result_lyrics, "title": result_title}
    except httpx.HTTPError as e:
        raise Exception(f"HTTP request failed: {str(e)}") from e
    except KeyError as e:
        raise Exception(f"Failed to parse response: {str(e)}") from e


@mcp.tool(
    description="""Generate song based on the lyrics text and save the output audio file to a given directory.
    Directory is optional, if not provided, the output file will be saved to $HOME/Desktop.
    
    ⚠️ COST WARNING: This tool makes an API call to mureka.ai which may incur costs. Only use when explicitly requested by the user.
    
    Args:
        lyrics (str): The lyrics to generate song
        prompt (str, optional): Control song generation by inputting a prompt.For example:r&b, slow, passionate, male vocal.
        output_directory (str, optional): Directory where files should be saved.
            Defaults to $HOME/Desktop if not provided.
    
    Returns:
        The output file and name of song generated.
    """,
    annotations={
        "title": "Generate Song",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def generate_song(lyrics: str, prompt: str = "", output_directory: str | None = None) -> \
        list[TextContent]:
    try:
        if not api_key:
            raise Exception("Can not found API key.")
        if lyrics == "":
            raise Exception("lyrics text is required.")
        model: str = "auto"
        output_path = make_output_path(output_directory, global_base_path)
        # call mureka API
        url = f"{api_url}/v1/song/generate"

        # set request parameters
        # for more parameter information, please refer to:https://platform.mureka.ai/docs/api/operations/post-v1-song-generate.html
        headers = {'Authorization': 'Bearer {}'.format(api_key),
                   'Content-Type': 'application/json'}
        params = {'lyrics': lyrics,
                  'model': model,
                  'prompt': prompt}

        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=params, headers=headers)
            response.raise_for_status()
            result = response.json()

        # parse result, pick up task id to query task result
        task_id = result.get("id", "")
        if len(task_id) == 0:
            raise Exception("generate song failed")

        current_timestamp = datetime.now().timestamp()
        while True:
            if (datetime.now().timestamp() - current_timestamp) > default_time_out:
                raise Exception(f"generate song time out {default_time_out} seconds")
            song_urls, status = await query_song_task(task_id)
            if status == "failed" or status == "cancelled" or status == "timeouted":
                raise Exception(f"generate song:{status}")
            elif status == "succeeded":
                break
            else:
                time.sleep(1)
        # downloads songs
        output_path.parent.mkdir(parents=True, exist_ok=True)
        save_path_group = []
        for song_url_item in song_urls:
            filename = extract_filename_from_url(song_url_item)
            response = requests.get(song_url_item)
            if response.status_code == 200:
                song_bytes = response.content
            else:
                raise Exception("generate song failed! Can't download songs")
            save_path = output_path / filename
            with open(save_path, "wb") as f:
                f.write(song_bytes)
            save_path_group.append(save_path)

        return [TextContent(
            type="text",
            text=f"Success. File saved as: {save_path}",
        ) for save_path in save_path_group]
    except httpx.HTTPError as e:
        raise Exception(f"HTTP request failed: {str(e)}") from e
    except KeyError as e:
        raise Exception(f"Failed to parse response: {str(e)}") from e


async def query_song_task(task_id: str) -> ([], str):
    try:
        url = f"{api_url}/v1/song/query/{task_id}"
        headers = {'Authorization': 'Bearer {}'.format(api_key)}
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            result = response.json()

        status = result.get("status", "failed")
        if status == "succeeded":
            # pick songs url
            ret_songs_list = []
            choices_list = result.get("choices", [])
            for choice in choices_list:
                song_url = choice.get("url", "")
                if len(song_url) > 0:
                    ret_songs_list.append(song_url)
            if len(ret_songs_list) == 0:
                return [], "failed"
            return ret_songs_list, status
        else:
            return [], status
    except httpx.HTTPError as e:
        raise Exception(f"HTTP request failed: {str(e)}") from e
    except KeyError as e:
        raise Exception(f"Failed to parse response: {str(e)}") from e


async def query_instrumental_task(task_id: str) -> tuple[list[str], str]:
    """Keep the original Python helper contract for existing callers."""
    task_id = _validate_task_id(task_id)
    task = await get_client().get_instrumental_task(task_id)
    status = task.get("status", "failed")
    if status != "succeeded":
        return [], status
    urls = [
        choice["url"]
        for choice in task.get("choices", [])
        if choice.get("url")
    ]
    return (urls, status) if urls else ([], "failed")


@mcp.tool(
    description="""Generate background music(instrumental) based on the prompt text and save the output audio file to a given directory.
    Directory is optional, if not provided, the output file will be saved to $HOME/Desktop.
    
    ⚠️ COST WARNING: This tool makes an API call to mureka.ai which may incur costs. Only use when explicitly requested by the user.
    
    Args:
        prompt (str, optional): Control music generation by inputting a prompt.For example:r&b, slow, passionate, male vocal.
        output_directory (str, optional): Directory where files should be saved.
            Defaults to $HOME/Desktop if not provided.
            
    Returns:
        The output file and name of background music(instrumental) generated.
    """,
    annotations={
        "title": "Generate Instrumental And Download MP3",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def generate_instrumental(prompt: str = "", output_directory: str | None = None) -> list[TextContent]:
    prompt = prompt.strip()
    if not prompt:
        raise ValueError(
            "Prompt is required, for example: ambient, slow, soft pads"
        )

    client = get_client()
    task = await client.submit_instrumental(
        prompt=prompt,
        instrumental_id=None,
        model="auto",
        n=None,
    )
    task_id = task.get("id")
    if not task_id:
        raise RuntimeError("Mureka did not return an instrumental task ID")

    deadline = monotonic() + default_time_out
    while True:
        task = await client.get_instrumental_task(task_id)
        status = task.get("status", "unknown")
        if status in TERMINAL_STATUSES:
            if status != "succeeded":
                reason = task.get("failed_reason") or "No failure reason provided"
                raise RuntimeError(
                    f"Instrumental task {task_id} ended with status "
                    f"{status}: {reason}"
                )
            break
        if monotonic() >= deadline:
            raise TimeoutError(
                f"Instrumental task {task_id} timed out after "
                f"{default_time_out:g} seconds (last status: {status})"
            )
        await asyncio.sleep(1)

    output_path = make_output_path(output_directory, global_base_path)
    save_paths = []
    for choice in task.get("choices", []):
        song_url = choice.get("url")
        if not song_url:
            continue
        filename = extract_filename_from_url(song_url)
        save_path = output_path / filename
        await client.download_file(song_url, save_path)
        save_paths.append(save_path)
    if not save_paths:
        raise RuntimeError(
            f"Instrumental task {task_id} succeeded without MP3 outputs"
        )

    return [
        TextContent(type="text", text=f"Success. File saved as: {save_path}")
        for save_path in save_paths
    ]


def play(
        audio: bytes | Iterator[bytes]
) -> None:
    if isinstance(audio, Iterator):
        audio = b"".join(audio)

    try:
        import io

        import sounddevice as sd  # type: ignore
        import soundfile as sf  # type: ignore
    except ModuleNotFoundError:
        message = (
            "`pip install sounddevice soundfile` required when `use_ffmpeg=False` "
        )
        raise ValueError(message)
    sd.play(*sf.read(io.BytesIO(audio)))
    sd.wait()


@mcp.tool(description="Play an audio file. Supports WAV and MP3 formats.")
def play_audio(input_file_path: str) -> TextContent:
    file_path = handle_input_file(input_file_path)
    play(open(file_path, "rb").read())
    return TextContent(type="text", text=f"Successfully played audio file: {file_path}")


def main():
    print("Starting MCP server")
    """Run the MCP server"""
    mcp.run()


if __name__ == "__main__":
    main()
