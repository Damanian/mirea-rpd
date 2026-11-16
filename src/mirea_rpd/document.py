"""The native site's initialized draft, sequential request queue and rolling hash."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

from .exceptions import RPDError

if TYPE_CHECKING:
    from .client import RPDClient


def form_data(values: dict[str, Any]) -> dict[str, Any]:
    """Encode nested models the same way as the site's jQuery dispatcher."""
    output: dict[str, Any] = {}

    def add(key: str, value: Any) -> None:
        if isinstance(value, dict):
            for child, item in value.items():
                add(f"{key}[{child}]", item)
        elif isinstance(value, list):
            if all(not isinstance(item, (list, dict)) for item in value):
                if value:
                    output[f"{key}[]"] = [scalar(item) for item in value]
            else:
                for index, item in enumerate(value):
                    add(f"{key}[{index}]", item)
        else:
            output[key] = scalar(value)

    def scalar(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    for key, value in values.items():
        add(key, value)
    return output


class RPDDocument:
    def __init__(self, client: RPDClient, rup_row_id: int, rp_id: int | None) -> None:
        self.client = client
        self.rup_row_id = rup_row_id
        self.rp_id = rp_id
        self.hash: str | None = None
        self.state: dict[str, Any] = {}
        self.common_state: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self.edit_lock = asyncio.Lock()

    @property
    def params(self) -> dict[str, Any]:
        result = {"rupRowId": self.rup_row_id}
        if self.rp_id is not None:
            result["rpId"] = self.rp_id
        return result

    async def initialize(self, *, force: bool = False) -> dict[str, Any]:
        async with self._lock:
            if self.hash is None or force:
                self.state = await self._request("GET", "/Rp/Initialize", {})
                if not self.hash:
                    raise RPDError("Инициализация РПД не вернула контрольный hash черновика.")
            return self.state

    async def call(self, method: str, path: str, values: dict[str, Any] | None = None) -> dict[str, Any]:
        await self.initialize()
        async with self._lock:
            return await self._request(method, path, values or {})

    async def _request(self, method: str, path: str, values: dict[str, Any]) -> dict[str, Any]:
        payload = dict(values)
        if self.hash is not None:
            payload["hash"] = self.hash
        if method.upper() == "GET":
            response = await self.client.request(method, path, params={**self.params, **form_data(payload)})
        else:
            response = await self.client.request(method, path, params=self.params, data=form_data(payload))
        return self._consume(response, path)

    def _consume(self, response: Any, path: str) -> dict[str, Any]:
        raw = self.client._decode_json(response, path)
        body = raw.get("data") if isinstance(raw, dict) and "result" in raw else raw
        if isinstance(body, dict):
            self.common_state.update(body.get("commonState") or {})
            next_hash = (body.get("commonState") or {}).get("hash")
            if next_hash:
                self.hash = next_hash
        result = self.client.check_api_result(raw, path)
        if result is None:
            return {}
        if not isinstance(result, dict):
            raise RPDError(f"Некорректная структура ответа РПД ({path}).")
        return result

    async def upload_fos(self, values: dict[str, Any], filename: str, content: bytes) -> dict[str, Any]:
        await self.initialize()
        async with self._lock:
            response = await self.client.request(
                "POST",
                "/Rp3/UploadFos",
                params={**self.params, **values},
                data=form_data({"hash": self.hash}),
                files={
                    "file": (
                        filename,
                        content,
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
            )
            return self._consume(response, "/Rp3/UploadFos")

    async def download_fos(self, values: dict[str, Any]) -> bytes:
        await self.initialize()
        async with self._lock:
            response = await self.client.request(
                "GET",
                "/Rp3/DownloadFos",
                params={**self.params, **values, "hash": self.hash},
            )
            if not response.content.startswith(b"PK"):
                self._consume(response, "/Rp3/DownloadFos")
                raise RPDError("Сайт не вернул файл ФОС в формате DOCX.")
            return response.content

    async def save(self) -> dict[str, Any]:
        result = await self.call("POST", "/Rp/Save")
        url = result.get("url")
        if url:
            query = parse_qs(urlparse(url).query)
            saved_id = query.get("rpId", query.get("rpdId", []))
            if saved_id:
                old_key = (self.rup_row_id, self.rp_id)
                self.rp_id = int(saved_id[0])
                if old_key[1] is None:
                    self.client._documents.pop(old_key, None)
                self.client._documents[(self.rup_row_id, self.rp_id)] = self
        return result
