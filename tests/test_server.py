"""Tests for MCP Server."""


from mirea_rpd.server import create_server
from mirea_rpd.session import SessionStore


def test_server_creation(tmp_path):
    store = SessionStore(path=tmp_path / "session.json")
    server = create_server(session_store=store)

    assert server.name == "mirea-rpd"
    # Check that tools are registered
    # In mcp 2.x, server has get_tools() or registered tools list
    tool_names = [tool.name for tool in server._tool_manager.list_tools()]
    assert "rpd_status" in tool_names
    assert "rpd_departments" in tool_names
    assert "rpd_search_disciplines" in tool_names
    assert "rpd_get_title" in tool_names
    assert "rpd_get_content" in tool_names
    assert "rpd_get_fos" in tool_names
    assert "rpd_get_literature" in tool_names
    assert "rpd_download_draft" in tool_names
