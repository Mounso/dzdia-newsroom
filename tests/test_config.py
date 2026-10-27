import pytest
from pydantic import ValidationError

from dzdia_newsroom.config import TelegramWebhookSettings

SECRET = "a" * 32


def load(monkeypatch: pytest.MonkeyPatch, **env: str) -> TelegramWebhookSettings:
    for name in ("TELEGRAM_ALLOWED_USER_IDS", "TELEGRAM_TEAM_CHAT_ID"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return TelegramWebhookSettings(_env_file=None)


def test_allowed_user_ids_are_comma_separated(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load(
        monkeypatch,
        TELEGRAM_WEBHOOK_SECRET=SECRET,
        TELEGRAM_ALLOWED_USER_IDS="123, 456,",
        TELEGRAM_TEAM_CHAT_ID="-100789",
    )
    assert settings.telegram_allowed_user_ids == frozenset({123, 456})
    assert settings.telegram_team_chat_id == -100789


def test_allowlist_defaults_to_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load(monkeypatch, TELEGRAM_WEBHOOK_SECRET=SECRET)
    assert settings.telegram_allowed_user_ids == frozenset()
    assert settings.telegram_team_chat_id is None


@pytest.mark.parametrize("secret", ["too-short", "a" * 31, "a" * 31 + "!", "a" * 257])
def test_weak_or_invalid_webhook_secret_is_rejected(
    monkeypatch: pytest.MonkeyPatch, secret: str
) -> None:
    with pytest.raises(ValidationError):
        load(monkeypatch, TELEGRAM_WEBHOOK_SECRET=secret)
