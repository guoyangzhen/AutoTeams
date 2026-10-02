"""The tool catalog must retain server identity for the actual frontend grouping."""
import pytest


@pytest.mark.asyncio
async def test_tool_catalog_can_be_grouped_by_registered_server(enterprise_authenticated_client):
    client = enterprise_authenticated_client
    servers = await client.get('/api/v1/mcp/servers')
    assert servers.status_code == 200
    server_ids = {server['server_id'] for server in servers.json()['data']}
    tools = await client.get('/api/v1/mcp/tools')
    assert tools.status_code == 200
    catalog = tools.json()['data']
    assert catalog
    assert all(tool.get('server_id') in server_ids for tool in catalog)
    local_tools = [tool for tool in catalog if tool['server_id'] == 'local_runner']
    reader = next(tool for tool in local_tools if tool['name'] == 'runner_read_file')
    assert set(reader['inputSchema']['required']) == {'grant_id', 'relative_path'}
