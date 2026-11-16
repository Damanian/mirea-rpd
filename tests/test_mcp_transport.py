"""Exercise the same stdio protocol and JSON Schema used by a real AI client."""

import sys

import pytest
from mcp import ClientSession, StdioServerParameters, stdio_client


@pytest.mark.asyncio
async def test_stdio_autofill_validation_and_preview():
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-X", "utf8", "-m", "mirea_rpd", "mcp"],
    )
    async with stdio_client(parameters) as streams, ClientSession(*streams, read_timeout_seconds=15) as session:
        initialized = await session.initialize()
        assert initialized.server_info.name == "mirea-rpd"
        tools = await session.list_tools()
        assert "rpd_validate_template" in {tool.name for tool in tools.tools}

        validation = await session.call_tool("rpd_validate_template", {"template": {"it_text": "Python"}})
        assert not validation.is_error
        assert validation.structured_content["valid"] is True

        preview = await session.call_tool(
            "rpd_apply_template",
            {"rup_row_id": 1, "rp_id": 2, "template": {"it_text": "Python"}, "dry_run": True},
        )
        assert not preview.is_error
        assert preview.structured_content["success"] is True
        assert preview.structured_content["saved_to_db"] is False

        invalid = await session.call_tool("rpd_validate_template", {"template": {"unknown_field": "bad"}})
        assert invalid.is_error
