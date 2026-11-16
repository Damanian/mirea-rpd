"""Tests for MCP Server."""

from mirea_rpd.server import create_server
from mirea_rpd.session import SessionStore


def test_server_creation(tmp_path):
    store = SessionStore(path=tmp_path / "session.json")
    server, login_srv = create_server(session_store=store)

    assert server.name == "mirea-rpd"
    assert login_srv is not None

    tool_names = [tool.name for tool in server._tool_manager.list_tools()]
    assert "rpd_login" in tool_names
    assert "rpd_status" in tool_names
    assert "rpd_departments" in tool_names
    assert "rpd_search_disciplines" in tool_names
    assert "rpd_get_title" in tool_names
    assert "rpd_get_content" in tool_names
    assert "rpd_get_fos" in tool_names
    assert "rpd_get_literature" in tool_names
    assert "rpd_apply_template" in tool_names
    assert "rpd_export_template" in tool_names
    assert "rpd_download_draft" in tool_names
    assert "rpd_validate_template" in tool_names
    assert "rpd_get_autofill_context" in tool_names


def test_autofill_tool_has_structured_schema(tmp_path):
    server, _ = create_server(session_store=SessionStore(tmp_path / "session.json"))
    schema = server._tool_manager.get_tool("rpd_apply_template").parameters
    assert schema["$defs"]["RPDTemplate"]["additionalProperties"] is False
    assert "sections" in schema["$defs"]["RPDTemplate"]["properties"]
    assert "semester" in schema["$defs"]["WorkItemTemplate"]["properties"]
