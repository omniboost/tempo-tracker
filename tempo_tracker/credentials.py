"""API-tokens in de macOS Keychain via keyring. Tokens komen nooit in bestanden of logs."""

from __future__ import annotations

import keyring
from keyring.errors import PasswordDeleteError

SERVICE = "tempo-tracker"
JIRA = "jira-api-token"
TEMPO = "tempo-api-token"


def get_token(name: str) -> str | None:
    return keyring.get_password(SERVICE, name)


def set_token(name: str, value: str) -> None:
    keyring.set_password(SERVICE, name, value.strip())


def delete_token(name: str) -> None:
    try:
        keyring.delete_password(SERVICE, name)
    except PasswordDeleteError:
        pass
