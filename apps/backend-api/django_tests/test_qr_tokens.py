"""Подписанный код точки: что он подтверждает и чего не подтверждает.

Проверяется не «функция возвращает строку», а свойства, на которых всё
держится: подделать нельзя, переиграть нельзя, персональных данных внутри
нет, и разбор не зависит от того, в каком порядке подсовывать мусор.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from humotech.qr_codes.tokens import PREFIX, QrTokenError, issue, read

pytestmark = pytest.mark.django_db


@pytest.fixture()
def qr_settings(settings):
    settings.QR = {
        **settings.QR,
        "SIGNING_SECRET": "test-qr-signing-secret-not-real",
        "TOKEN_TTL_SECONDS": 30,
        "CLOCK_SKEW_SECONDS": 10,
    }
    return settings.QR


def _issue(now, **overrides):
    params = {
        "organization_id": uuid.uuid4(),
        "office_id": uuid.uuid4(),
        "qr_point_id": uuid.uuid4(),
        "direction_mode": "BOTH",
        "issued_at": now,
        "ttl_seconds": 30,
    }
    params.update(overrides)
    return issue(**params)


# --- что внутри ------------------------------------------------------------

def test_token_carries_the_point_and_survives_a_round_trip(qr_settings, now):
    org, office, point = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    token, _ = _issue(
        now, organization_id=org, office_id=office, qr_point_id=point,
        direction_mode="ENTRY",
    )

    payload = read(token, now=now)

    assert payload.organization_id == org
    assert payload.office_id == office
    assert payload.qr_point_id == point
    assert payload.direction_mode == "ENTRY"


def test_token_contains_no_personal_data(qr_settings, now):
    """Код видят все, кто стоит рядом с дверью, и его фотографируют.

    Всё, что в него положено, следует считать опубликованным — поэтому
    сотрудника в нём нет вовсе. Проверяем не «поля называются правильно»,
    а что разбор кода в принципе не даёт ничего о человеке.
    """
    token, payload = _issue(now)

    assert not hasattr(payload, "employee_id")
    assert set(vars(payload)) == {
        "organization_id", "office_id", "qr_point_id",
        "direction_mode", "issued_at", "expires_at", "jti",
    }


def test_two_codes_of_the_same_point_differ(qr_settings, now):
    """Разовый номер делает каждый код одноразовым."""
    first, first_payload = _issue(now)
    second, second_payload = _issue(now)

    assert first != second
    assert first_payload.jti != second_payload.jti
    assert first_payload.nonce_hash != second_payload.nonce_hash


def test_token_is_short_enough_for_a_readable_qr(qr_settings, now):
    """Длина кода — не эстетика, а читаемость с телефона у двери.

    Чем короче строка, тем крупнее модули QR при том же размере наклейки.
    Сто с небольшим символов уверенно читаются в плохом свете.
    """
    token, _ = _issue(now)
    assert len(token) <= 128


# --- подделка --------------------------------------------------------------

def test_tampered_token_is_refused(qr_settings, now):
    token, _ = _issue(now)
    # Меняем один символ содержимого — подпись перестаёт сходиться.
    broken = token[:-8] + ("A" if token[-8] != "A" else "B") + token[-7:]

    with pytest.raises(QrTokenError) as exc:
        read(broken, now=now)
    assert exc.value.reason in ("bad_signature", "malformed")


def test_token_signed_with_another_key_is_refused(qr_settings, now, settings):
    token, _ = _issue(now)
    settings.QR = {**settings.QR, "SIGNING_SECRET": "совершенно-другой-ключ"}

    with pytest.raises(QrTokenError) as exc:
        read(token, now=now)
    assert exc.value.reason == "bad_signature"


def test_foreign_code_is_recognised_without_a_round_trip(qr_settings, now):
    """Чужой QR отличается по метке формата, а не по неудачной подписи.

    Так сканер не ходит на сервер за очевидным отказом: ссылка, код Wi-Fi
    или чья-то визитка отсеиваются на телефоне.
    """
    for foreign in ["https://example.com", "WIFI:S:office;", "", "HT2abcdef"]:
        with pytest.raises(QrTokenError) as exc:
            read(foreign, now=now)
        assert exc.value.reason in ("foreign_code", "unknown_version", "malformed")


def test_garbage_after_the_prefix_does_not_crash(qr_settings, now):
    for tail in ["", "!!!", "aaaa", "A" * 500]:
        with pytest.raises(QrTokenError):
            read(PREFIX + tail, now=now)


def test_signature_is_checked_before_anything_else(qr_settings, now):
    """Подделанный и вдобавок протухший код отвергается как подделка.

    Порядок важен: иначе по разнице ответов выясняли бы, какие поля сервер
    вообще разбирает.
    """
    token, _ = _issue(now - timedelta(hours=1))
    broken = token[:-4] + "AAAA"

    with pytest.raises(QrTokenError) as exc:
        read(broken, now=now)
    assert exc.value.reason in ("bad_signature", "malformed")


def test_missing_signing_secret_refuses_to_issue(settings, now):
    settings.QR = {**settings.QR, "SIGNING_SECRET": ""}

    with pytest.raises(QrTokenError) as exc:
        _issue(now)
    assert exc.value.reason == "signing_secret_not_configured"


# --- срок ------------------------------------------------------------------

def test_token_expires(qr_settings, now):
    token, _ = _issue(now, ttl_seconds=30)

    read(token, now=now + timedelta(seconds=29))  # ещё живой
    with pytest.raises(QrTokenError) as exc:
        read(token, now=now + timedelta(seconds=60))
    assert exc.value.reason == "expired"


def test_clock_skew_is_allowed_in_both_directions(qr_settings, now):
    """Часы экрана и сервера расходятся на секунды.

    Без допуска код, выпущенный «в будущем» на секунду, отвергался бы
    весь свой срок — то есть экран с чуть спешащими часами не работал бы
    вообще, и выглядело бы это как отказ сканера.
    """
    token, _ = _issue(now, ttl_seconds=30)

    read(token, now=now - timedelta(seconds=5))
    read(token, now=now + timedelta(seconds=35))

    with pytest.raises(QrTokenError):
        read(token, now=now - timedelta(seconds=60))


def test_ttl_beyond_the_format_is_refused(qr_settings, now):
    """Срок хранится двумя байтами как смещение от выпуска.

    Поэтому «конец позже начала» — свойство формата, а не проверка,
    которую можно забыть написать.
    """
    with pytest.raises(QrTokenError) as exc:
        _issue(now, ttl_seconds=0)
    assert exc.value.reason == "ttl_out_of_range"

    with pytest.raises(QrTokenError):
        _issue(now, ttl_seconds=100_000)
