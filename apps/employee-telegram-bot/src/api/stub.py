"""Заглушка backend-api на время, пока его нет.

Возвращает payload'ы РОВНО той формы, что описана в контракте
packages/api-contracts/telegram-bot.v1.md — иначе переключение на LiveBackendClient
сломается молча, на несовпадении имён полей.

Демо-аккаунты (код подтверждения всегда 000000):
    +992900000001 — обычный сотрудник
    +992900000002 — HR-менеджер
"""

from __future__ import annotations

import itertools
from datetime import date, datetime, timedelta

from src.api.client import BackendClient
from src.api.errors import BadRequest, Conflict, NotFound

STUB_CODE = "000000"

_DIRECTORY = {
    "+992900000001": {
        "id": 42, "full_name": "Иванов Иван Иванович", "role": "employee",
        "position": "Оператор",
        "department": {"id": 3, "name": "Контакт-центр"},
        "office": {"id": 1, "name": "Душанбе, центральный"},
        "region": {"id": 1, "name": "Душанбе"},
        "hired_at": "2024-02-01", "photo_url": None, "language": "ru",
    },
    "+992900000002": {
        "id": 9, "full_name": "Каримова Нилуфар", "role": "hr",
        "position": "HR-менеджер",
        "department": {"id": 1, "name": "Отдел кадров"},
        "office": {"id": 1, "name": "Душанбе, центральный"},
        "region": {"id": 1, "name": "Душанбе"},
        "hired_at": "2023-05-15", "photo_url": None, "language": "ru",
    },
}


def _norm(phone: str) -> str:
    keep = "".join(ch for ch in phone if ch.isdigit())
    return "+" + keep


class StubBackendClient(BackendClient):
    def __init__(self):
        self._tokens: dict[str, str] = {}          # access_token -> phone
        self._ids = itertools.count(1000)
        self._absences: list[dict] = []            # заявки сотрудника
        self._questions: list[dict] = []
        self._hr_queue: list[dict] = [
            {
                "id": 119, "type": "vacation", "status": "pending",
                "employee": {"id": 42, "full_name": "Иванов И.И.",
                             "position": "Оператор",
                             "office": "Душанбе, центральный"},
                "date_from": "2026-10-01", "date_to": "2026-10-14", "days": 14,
                "comment": "", "attachment_url": None,
                "balance_days_left": 14,
                "created_at": "2026-09-01T10:00:00+05:00",
            },
            {
                "id": 120, "type": "sick_leave", "status": "pending",
                "employee": {"id": 51, "full_name": "Рахимов А.С.",
                             "position": "Логист",
                             "office": "Худжанд, филиал"},
                "date_from": "2026-09-02", "date_to": "2026-09-05", "days": 4,
                "comment": "ОРВИ", "attachment_url": None,
                "balance_days_left": None,
                "created_at": "2026-09-02T08:12:00+05:00",
            },
        ]

    def _phone_of(self, token: str) -> str:
        phone = self._tokens.get(token)
        if phone is None:
            from src.api.errors import Unauthorized
            raise Unauthorized(401, "invalid_token", "Токен недействителен")
        return phone

    def _employee(self, token: str) -> dict:
        return _DIRECTORY[self._phone_of(token)]

    def _require_hr(self, token: str) -> None:
        if self._employee(token)["role"] not in ("hr", "admin"):
            from src.api.errors import Forbidden
            raise Forbidden(403, "forbidden", "Недостаточно прав для этой операции")

    # --- привязка ---
    async def request_code(self, phone, telegram_id):
        if _norm(phone) not in _DIRECTORY:
            raise NotFound(404, "not_found", "Такой номер не найден среди сотрудников")
        return {"sent": True, "expires_in": 300, "retry_after": 60}

    async def link(self, phone, code, telegram_id, telegram_username):
        key = _norm(phone)
        if key not in _DIRECTORY:
            raise NotFound(404, "not_found", "Такой номер не найден среди сотрудников")
        if code.strip() != STUB_CODE:
            raise BadRequest(400, "validation_error", "Неверный код подтверждения")
        emp = _DIRECTORY[key]
        token = f"stub-token-{emp['id']}"
        self._tokens[token] = key
        return {
            "access_token": token,
            "expires_at": (datetime.now() + timedelta(days=180)).isoformat(),
            "employee": {"id": emp["id"], "full_name": emp["full_name"],
                         "role": emp["role"]},
        }

    async def unlink(self, token):
        self._tokens.pop(token, None)

    # --- сотрудник ---
    async def me(self, token):
        return dict(self._employee(token))

    async def attendance_my(self, token, date_from, date_to):
        start = date.fromisoformat(date_from)
        items = []
        for i in range(min(10, (date.fromisoformat(date_to) - start).days + 1)):
            day = start + timedelta(days=i)
            if day.weekday() >= 5:
                items.append({"date": day.isoformat(), "check_in": None,
                              "check_out": None, "worked_minutes": 0,
                              "late_minutes": 0, "status": "weekend"})
                continue
            late = 14 if i % 4 == 0 else 0
            items.append({
                "date": day.isoformat(),
                "check_in": f"{day.isoformat()}T09:{late:02d}:00+05:00",
                "check_out": f"{day.isoformat()}T18:03:00+05:00",
                "worked_minutes": 540 - late,
                "late_minutes": late,
                "status": "late" if late else "ok",
            })
        return {"items": items, "total": len(items)}

    async def attendance_summary(self, token, month):
        self._employee(token)
        return {
            "period": {"month": month, "work_days": 22},
            "days_present": 20, "days_absent": 2,
            "worked_minutes": 10800, "norm_minutes": 11616,
            "overtime_minutes": 0,
            "late_count": 3, "late_minutes_total": 47,
            "early_leave_count": 1,
            "vacation_days": 0, "sick_leave_days": 2,
        }

    def _new_absence(self, kind, date_from, date_to, comment):
        days = (date.fromisoformat(date_to) - date.fromisoformat(date_from)).days + 1
        item = {
            "id": next(self._ids), "type": kind, "status": "pending",
            "date_from": date_from, "date_to": date_to, "days": days,
            "comment": comment,
            "decided_by": None, "decided_at": None, "reject_reason": None,
            "created_at": datetime.now().isoformat(),
        }
        self._absences.append(item)
        return item

    async def create_vacation(self, token, date_from, date_to, comment=""):
        self._employee(token)
        item = self._new_absence("vacation", date_from, date_to, comment)
        if item["days"] > 28:
            self._absences.remove(item)
            raise Conflict(409, "conflict", "Превышен остаток дней отпуска")
        return {**item, "balance_days_left": 28 - item["days"]}

    async def create_sick_leave(self, token, date_from, date_to, comment="",
                                attachment_file_id=None):
        self._employee(token)
        return self._new_absence("sick_leave", date_from, date_to, comment)

    async def absences_my(self, token, status=None):
        self._employee(token)
        items = [a for a in self._absences if status is None or a["status"] == status]
        return {"items": items, "total": len(items)}

    async def cancel_absence(self, token, absence_id):
        self._employee(token)
        for a in self._absences:
            if a["id"] == absence_id:
                if a["status"] != "pending":
                    raise Conflict(409, "conflict", "Заявку уже обработали")
                a["status"] = "cancelled"
                return a
        raise NotFound(404, "not_found", "Заявка не найдена")

    async def create_correction(self, token, date, kind, proposed_time, reason):
        self._employee(token)
        return {"id": next(self._ids), "status": "pending"}

    async def create_question(self, token, text, category=None):
        self._employee(token)
        item = {"id": next(self._ids), "text": text, "status": "open",
                "answer": None, "answered_at": None,
                "created_at": datetime.now().isoformat()}
        self._questions.append(item)
        return {"id": item["id"], "status": "open",
                "created_at": item["created_at"]}

    async def questions_my(self, token):
        self._employee(token)
        return {"items": self._questions, "total": len(self._questions)}

    # --- HR ---
    async def hr_absences_pending(self, token, limit=20, offset=0):
        self._require_hr(token)
        pending = [a for a in self._hr_queue if a["status"] == "pending"]
        return {"items": pending[offset:offset + limit], "total": len(pending)}

    def _decide(self, absence_id: int, status: str, reason: str | None):
        for a in self._hr_queue:
            if a["id"] == absence_id:
                if a["status"] != "pending":
                    raise Conflict(409, "conflict",
                                   "Заявку уже обработал другой сотрудник")
                a["status"] = status
                a["decided_at"] = datetime.now().isoformat()
                a["decided_by"] = 9
                a["reject_reason"] = reason
                return {"id": a["id"], "status": status,
                        "decided_at": a["decided_at"], "decided_by": 9}
        raise NotFound(404, "not_found", "Заявка не найдена")

    async def hr_approve_absence(self, token, absence_id, comment=""):
        self._require_hr(token)
        return self._decide(absence_id, "approved", None)

    async def hr_reject_absence(self, token, absence_id, reason):
        self._require_hr(token)
        return self._decide(absence_id, "rejected", reason)

    async def hr_attendance_today(self, token, office_id=None):
        self._require_hr(token)
        return {
            "date": date.today().isoformat(),
            "office": {"id": 1, "name": "Душанбе, центральный"},
            "total_employees": 84,
            "present": 71, "late": 6, "absent": 5,
            "on_vacation": 2, "on_sick_leave": 0,
            "late_list": [
                {"employee_id": 42, "full_name": "Иванов И.И.",
                 "check_in": f"{date.today().isoformat()}T09:14:00+05:00",
                 "late_minutes": 14},
                {"employee_id": 51, "full_name": "Рахимов А.С.",
                 "check_in": f"{date.today().isoformat()}T09:31:00+05:00",
                 "late_minutes": 31},
            ],
        }

    async def hr_employees(self, token, query, limit=20):
        self._require_hr(token)
        q = query.lower().strip()
        items = [
            {"id": e["id"], "full_name": e["full_name"], "position": e["position"],
             "department": e["department"]["name"], "office": e["office"]["name"],
             "phone": phone, "is_active": True}
            for phone, e in _DIRECTORY.items()
            if q in e["full_name"].lower() or q in phone
        ]
        return {"items": items[:limit], "total": len(items)}

    async def close(self):
        return None
