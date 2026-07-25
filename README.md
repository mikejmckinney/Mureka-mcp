

<div class="title-block" style="text-align: center;" align="center">

![export](https://raw.githubusercontent.com/SkyworkAI/Mureka-mcp/refs/heads/master/assets/mureka_mcp.png)

[![Discord Community](https://raw.githubusercontent.com/SkyworkAI/Mureka-mcp/refs/heads/master/assets/discord_mureka.svg)](https://discord.com/invite/nwu9ANqAf5)
[![Twitter](https://raw.githubusercontent.com/SkyworkAI/Mureka-mcp/refs/heads/master/assets/x_mureka.svg)](https://x.com/Mureka_AI)
[![PyPI](https://raw.githubusercontent.com/SkyworkAI/Mureka-mcp/refs/heads/master/assets/pypi_mureka.svg)](https://pypi.org/project/mureka-mcp)

</div>
<p align="center">
  Official Mureka Model Context Protocol (MCP) server that enables interaction with powerful lyrics, song and bgm generating APIs. This server allows MCP clients like <a href="https://www.anthropic.com/claude">Claude Desktop</a>, <a href="https://github.com/openai/openai-agents-python">OpenAI Agents</a> and others to generate lyrics, song and background music(instrumental).
</p>

## Quickstart with Claude Desktop

1. Get your API key from [Mureka](https://platform.mureka.ai/apiKeys).
2. Install `uv` (Python package manager), install with `curl -LsSf https://astral.sh/uv/install.sh | sh` or see the `uv` [repo](https://github.com/astral-sh/uv) for additional install methods.
3. Go to Claude > Settings > Developer > Edit Config > claude_desktop_config.json to include the following:

```
{
    "mcpServers": {
        "Mureka": {
            "command": "uvx",
            "args": [
                "mureka-mcp"
            ],
            "env": {
                "MUREKA_API_KEY": "<insert-your-api-key-here>",
                "MUREKA_API_URL": "https://api.mureka.ai",
                "TIME_OUT_SECONDS":"300"
            }
        }
    }
}
```

Then restart the Claude app. The original four tools remain available, with
additional composable tools for production instrumental workflows.
<div class="title-block" style="text-align: left;">
<img src="https://raw.githubusercontent.com/SkyworkAI/Mureka-mcp/refs/heads/master/assets/img.png" width="400">
</div>

## Optional features
You can add the `TIME_OUT_SECONDS` environment variable to the `claude_desktop_config.json` to set the timeout period for song or bgm generation waiting(Default 60s).

`MUREKA_MCP_BASE_PATH` may be set to an allowed local media directory. Relative
upload and output paths resolve beneath this directory; without it, input paths
must be absolute.

## Instrumental production workflow

The newer instrumental tools keep paid generation separate from read-only
polling and local downloads:

1. `upload_instrumental_reference` uploads an original or properly licensed
   30-second MP3/M4A reference and returns its file ID plus caller-supplied
   provenance.
2. `submit_instrumental_generation` creates 1-3 candidates using an explicit
   model and either a prompt or uploaded reference. This is a cost-bearing,
   non-idempotent operation and is never retried automatically.
3. `get_instrumental_task` performs one read-only status query.
4. `wait_for_instrumental_task` performs bounded, asynchronous polling and
   reports the task ID and last status on timeout.
5. `download_instrumental` downloads an available MP3, FLAC, or WAV without
   substituting another codec. Existing files are not overwritten by default.

Structured generation and query responses include task ID, model, status,
timestamps, output IDs, durations, URLs, provenance, and a cost field. Mureka
does not currently return per-generation cost, so the cost value is explicitly
reported as unavailable rather than inferred from account-wide billing.

Example requests to an MCP client:

- "Submit one mureka-9 instrumental candidate from this ambient game-music
  prompt, then return the task metadata."
- "Wait up to 300 seconds for instrumental task task-123."
- "Download choice 0 from task-123 as WAV into /path/to/audio without
  overwriting an existing file."

### Security and reliability

- `MUREKA_API_KEY` is read only from the environment.
- API keys, authorization headers, and reference-audio contents are never
  included in tool results or errors.
- Generation and upload POST requests are not retried because repeating them
  can create additional resources or charges.
- Polling and downloads use finite timeouts. Generated URLs remain governed by
  Mureka's documented expiration period.
- Reference provenance is caller-supplied metadata. The server cannot verify
  ownership or license terms.

### Known API gaps

- Mureka's song extension and region-edit APIs require lyrics and are not
  documented for instrumental editing, so this server does not expose them as
  instrumental operations.
- Stem separation is intentionally not included in this focused workflow.
- Mureka exposes account-level billing totals but no reliable per-task charge.

### Development install and rollback

Install the tested dependency set from a checkout with `uv sync --all-groups`.
Run mocked contract tests with `uv run pytest`. To roll back to the last
published behavior, restore the MCP launcher to `mureka-mcp==0.0.13`; existing
tool names and signatures remain supported by this change.

## Example usage

⚠️ Warning: Mureka credits are needed to use these tools.
Try asking Claude:
- "Please create a song for my daughter Jessica to wish her a happy birthday and play it"
<div class="title-block" style="text-align: left;">
<img src="https://github.com/SkyworkAI/Mureka-mcp/blob/master/assets/demo.jpeg?raw=true" width="400">
</div>

- "Please generate lyrics about Christmas"
- "Please generate a song based on the lyrics just now"
- "Please generate background music suitable for playing in the coffee shop"
<div class="title-block" style="text-align: left;">
<img src="https://github.com/SkyworkAI/Mureka-mcp/blob/master/assets/demo1.jpeg?raw=true" width="400">
</div>

[bgm 1 download](https://github.com/SkyworkAI/Mureka-mcp/raw/407ad955ab29c61e81b5d374e492ef8b1353c2f3/assets/16567807049729-9pi6MDiVqTavVUdjf54fmW.mp3)

[bgm 2 download](https://github.com/SkyworkAI/Mureka-mcp/raw/407ad955ab29c61e81b5d374e492ef8b1353c2f3/assets/16567807049729-D7WVCcxp77Prm8b15HSX1G.mp3)

## Troubleshooting

Logs when running with Claude Desktop can be found at:

- **Windows**: `%APPDATA%\Claude\logs\mcp-server-Mureka.log`
- **macOS**: `~/Library/Logs/Claude/mcp-server-Mureka.log`

## Quickstart with Openai agents sdk
Install Agents SDK
```
pip install openai-agents
```
Run example: openapi_agents_example/main.py

Key is required to be filled in: 
```
MUREKA_API_KEY = "<insert-your-api-key-here>"
```
After running, you can see the composition process in the console
<div class="title-block" style="text-align: left;">
<img src="https://raw.githubusercontent.com/SkyworkAI/Mureka-mcp/refs/heads/master/assets/openai_1.jpeg" width="400">
</div>
<div class="title-block" style="text-align: left;">
<img src="https://raw.githubusercontent.com/SkyworkAI/Mureka-mcp/refs/heads/master/assets/openai_2.jpeg" width="400">
</div>
