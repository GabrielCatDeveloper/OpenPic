"""Dobles de prueba para los objetos de Telegram.

Permiten probar los handlers del bot sin red ni Telegram: cada objeto fake
guarda lo que el bot responde para poder inspeccionarlo.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Coroutine


class FakeUser:
    def __init__(
        self,
        user_id: int,
        username: str | None = None,
        first_name: str | None = None,
    ):
        self.id = user_id
        self.username = username
        self.first_name = first_name


class FakeFile:
    def __init__(self, file_id: str, data: bytes | None = None):
        self.file_id = file_id
        self.file_unique_id = file_id
        self.file_path = file_id
        self._data = data if data is not None else b"\xff\xd8\xff" + b"0" * 256

    async def download_as_bytearray(self) -> bytearray:
        return bytearray(self._data)


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.files: dict[str, FakeFile] = {}
        self.commands: list[Any] = []

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> "FakeMessage":
        self.sent.append((chat_id, text))
        return FakeMessage(text)

    async def get_file(self, file_id: str) -> FakeFile:
        return self.files.get(file_id) or FakeFile(file_id)

    async def set_my_commands(self, commands: Any) -> bool:
        self.commands = commands
        return True


class FakeMessage:
    def __init__(
        self,
        text: str | None = None,
        caption: str | None = None,
        photo: list[Any] | None = None,
        document: Any | None = None,
    ):
        self.text = text
        self.caption = caption
        self.photo = photo
        self.document = document
        self.replies: list[str] = []
        self.edits: list[str] = []
        self.documents: list[bytes] = []
        self.replies_markup: Any = None
        self.bot = FakeBot()

    async def reply_text(self, text: str, **kwargs: Any) -> "FakeMessage":
        self.replies.append(text)
        self.replies_markup = kwargs.get("reply_markup")
        return self

    async def edit_text(self, text: str, **kwargs: Any) -> "FakeMessage":
        self.edits.append(text)
        return self

    async def reply_document(self, document: bytes | None = None, **kwargs: Any) -> "FakeMessage":
        self.documents.append(document or b"")
        return self

    def get_bot(self) -> FakeBot:
        return self.bot

    @property
    def last_reply(self) -> str:
        return self.replies[-1] if self.replies else ""

    @property
    def last_edit(self) -> str:
        return self.edits[-1] if self.edits else ""

    @property
    def everything(self) -> str:
        return "\n".join(self.replies + self.edits)


class FakeCallbackQuery:
    def __init__(self, data: str, user: FakeUser | None = None):
        self.data = data
        self.answered = False
        self.edits: list[str] = []
        self.message = FakeMessage()

    async def answer(self, *args: Any, **kwargs: Any) -> None:
        self.answered = True

    async def edit_message_text(self, text: str, **kwargs: Any) -> FakeMessage:
        self.edits.append(text)
        return self.message


class FakeUpdate:
    def __init__(
        self,
        user: FakeUser | None = None,
        message: FakeMessage | None = None,
        callback_query: FakeCallbackQuery | None = None,
        text: str | None = None,
    ):
        self.effective_user = user
        self.effective_message = message if message is not None else FakeMessage(text=text)
        self.callback_query = callback_query
        self.message = self.effective_message


class FakeContext:
    def __init__(self, args: list[str] | None = None, user_data: dict | None = None):
        self.args = args or []
        self.user_data: dict = {} if user_data is None else user_data
        self.bot = FakeBot()
        self.chat_data: dict = {}


def run(scenario: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
    """Ejecuta un escenario asincrono en un unico event loop.

    Se usa en vez de pytest-asyncio para no anadir dependencias: todos los
    pasos de un test deben ir en la misma llamada para no romper el semaforo
    de inferencia (que pertenece a un event loop).
    """
    return asyncio.run(scenario())
