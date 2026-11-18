from sg_mcproperty.server.app import mcp


def test_server_builds():
    assert mcp.name == "sg-property"
