from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from typing import Any

import httpx
from mcp import ClientSession, types
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

from codeyx.config import MCPServerConfig, build_child_env, resolve_env_vars

logger = logging.getLogger(__name__)

# IPC guard rails: a wedged MCP server (hung child process, dead HTTP
# endpoint) must fail a call instead of stalling the agent loop forever.
CONNECT_TIMEOUT_S = 30.0
CALL_TIMEOUT_S = 120.0


class MCPClient:
    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self.name = config.name
        self._session: ClientSession | None = None
        self._stack: AsyncExitStack | None = None
        self._alive = False


    @property
    def is_alive(self) -> bool:
        return self._alive


    async def connect(self) -> None:
        if self._alive:
            return

        self._stack = AsyncExitStack()
        await self._stack.__aenter__()

        try:
            if self.config.is_stdio:
                read, write = await self._connect_stdio()
            else:
                read, write = await self._connect_http()

            session = await self._stack.enter_async_context(
                ClientSession(read, write)
            )
            # initialize() performs the handshake over the just-spawned
            # transport; bound it so a server that never responds fails
            # connect instead of hanging startup.
            await asyncio.wait_for(session.initialize(), timeout=CONNECT_TIMEOUT_S)
            self._session = session
            self._alive = True
            logger.info("MCP server '%s' connected", self.name)
        except BaseException:
            # BaseException: cancelled connects must also tear down the
            # stack, otherwise the stdio child process leaks as a zombie.
            await self._cleanup_stack()
            raise


    async def _connect_stdio(self) -> tuple[Any, Any]:
        assert self._stack is not None
        assert self.config.command is not None

        params = StdioServerParameters(
            command=self.config.command,
            args=self.config.args,
            env=build_child_env(self.config.env),
        )
        read, write = await self._stack.enter_async_context(
            stdio_client(params)
        )
        return read, write

    async def _connect_http(self) -> tuple[Any, Any]:
        assert self._stack is not None
        assert self.config.url is not None

        resolved_headers = {
            k: resolve_env_vars(v) for k, v in self.config.headers.items()
        }
        http_client = httpx.AsyncClient(
            headers=resolved_headers,
            follow_redirects=True,
        )
        await self._stack.enter_async_context(http_client)

        result = await self._stack.enter_async_context(
            streamable_http_client(self.config.url, http_client=http_client)
        )
        read, write = result[0], result[1]
        return read, write


    async def list_tools(self) -> list[types.Tool]:
        assert self._session is not None
        result = await asyncio.wait_for(
            self._session.list_tools(), timeout=CALL_TIMEOUT_S
        )
        return list(result.tools)


    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> types.CallToolResult:
        assert self._session is not None
        return await asyncio.wait_for(
            self._session.call_tool(name, arguments), timeout=CALL_TIMEOUT_S
        )

    async def close(self) -> None:
        self._alive = False
        self._session = None
        await self._cleanup_stack()

    async def _cleanup_stack(self) -> None:
        if self._stack is not None:
            try:
                await self._stack.__aexit__(None, None, None)
            except RuntimeError as e:
                if "cancel scope" in str(e):
                    logger.debug("Cancel scope cleanup (expected during shutdown): %s", e)
                else:
                    raise
            except Exception:
                logger.debug("Error closing stack for '%s'", self.name, exc_info=True)
            self._stack = None
