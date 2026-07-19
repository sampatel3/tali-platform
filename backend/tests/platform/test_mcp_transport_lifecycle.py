from __future__ import annotations

import asyncio
import gc
import warnings

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from app.mcp import mcp_app


def _exercise_stateless_posts() -> list[tuple[int, str, dict]]:
    async def run() -> list[tuple[int, str, dict]]:
        server = FastMCP(
            "stream-lifecycle-probe",
            stateless_http=True,
            json_response=mcp_app.settings.json_response,
            streamable_http_path="/",
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=False,
            ),
        )
        asgi_app = server.streamable_http_app()
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        responses: list[tuple[int, str, dict]] = []
        async with server.session_manager.run():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=asgi_app),
                base_url="http://testserver",
            ) as client:
                for request_id in range(3):
                    response = await client.post(
                        "/",
                        headers=headers,
                        json={
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "method": "tools/list",
                        },
                    )
                    responses.append(
                        (
                            response.status_code,
                            response.headers["content-type"],
                            response.json(),
                        )
                    )
        return responses

    return asyncio.run(run())


def test_stateless_json_posts_return_results_without_leaking_receive_streams():
    assert mcp_app.settings.stateless_http is True
    assert mcp_app.settings.json_response is True

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        responses = _exercise_stateless_posts()
        gc.collect()

    assert [status for status, _content_type, _body in responses] == [200, 200, 200]
    assert all(
        content_type.startswith("application/json")
        for _status, content_type, _body in responses
    )
    assert [body["id"] for _status, _content_type, body in responses] == [0, 1, 2]
    assert all(
        body["jsonrpc"] == "2.0" and body["result"]["tools"] == []
        for _status, _content_type, body in responses
    )
    assert not [
        warning
        for warning in caught
        if warning.category is ResourceWarning
        and "MemoryObjectReceiveStream" in str(warning.message)
    ]
