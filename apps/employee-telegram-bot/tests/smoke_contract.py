"""Смоук бота: сборка диспетчера + прогон стаба по контракту. Без сети и без Telegram."""
import asyncio
import sys

from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from src.api import MemoryTokenStorage, build_client
from src.api.errors import Conflict, Forbidden, Unauthorized
from src.config.settings import settings
from src.handlers import build_root_router
from src.middlewares.auth import AuthMiddleware
from src.utils.commands import commands_for
from src.utils.filters import RoleFilter

ok = 0
fail = 0


def check(name, cond):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name}")


async def main():
    print("== wiring ==")
    dp = Dispatcher(storage=MemoryStorage())
    client = build_client()
    dp.update.outer_middleware(AuthMiddleware(client, MemoryTokenStorage()))
    root = build_root_router()
    dp.include_router(root)
    names = [r.name for r in root.sub_routers]
    check("10 роутеров подключено", len(names) == 10)
    check("fallback последний, hr перед ним", names[-2:] == ["hr", "fallback"])
    check("api_mode=stub", settings.api_mode == "stub")

    print("== меню команд по ролям ==")
    emp_cmds = {c.command for c in commands_for("employee")}
    hr_cmds = {c.command for c in commands_for("hr")}
    guest_cmds = {c.command for c in commands_for(None)}
    check("сотрудник не видит /hr", "hr" not in emp_cmds)
    check("HR видит /hr и /requests", {"hr", "requests"} <= hr_cmds)
    check("гость видит только /start", guest_cmds == {"start"})

    print("== RoleFilter ==")
    f = RoleFilter("hr", "admin")
    check("employee не проходит", await f(None, role="employee") is False)
    check("hr проходит", await f(None, role="hr") is True)
    check("без роли не проходит", await f(None, role=None) is False)

    print("== привязка (контракт) ==")
    try:
        await client.link("+992900000001", "111111", 1, None)
        check("неверный код отклонён", False)
    except Exception as exc:
        check("неверный код отклонён", exc.code == "validation_error")

    emp = await client.link("+992900000001", "000000", 1, "ivanov")
    hr = await client.link("+992900000002", "000000", 2, "karimova")
    check("employee получил токен", "access_token" in emp)
    check("роль employee", emp["employee"]["role"] == "employee")
    check("роль hr", hr["employee"]["role"] == "hr")

    emp_token, hr_token = emp["access_token"], hr["access_token"]

    print("== форма ответов по контракту ==")
    me = await client.me(emp_token)
    check("me содержит role/office/department",
          {"role", "office", "department", "full_name"} <= set(me))
    summary = await client.attendance_summary(emp_token, "2026-09")
    check("summary содержит все поля контракта",
          {"period", "days_present", "worked_minutes", "norm_minutes",
           "late_count", "late_minutes_total"} <= set(summary))
    att = await client.attendance_my(emp_token, "2026-09-01", "2026-09-10")
    check("attendance items/total", {"items", "total"} <= set(att))
    check("статус отметки из словаря контракта",
          all(i["status"] in {"ok", "late", "early_leave", "absent", "no_checkout",
                              "holiday", "weekend", "vacation", "sick_leave"}
              for i in att["items"]))

    print("== заявка на отпуск ==")
    vac = await client.create_vacation(emp_token, "2026-10-01", "2026-10-14", "")
    check("заявка создана со status=pending", vac["status"] == "pending")
    check("дни считает бэкенд", vac["days"] == 14)
    mine = await client.absences_my(emp_token, "pending")
    check("заявка видна в своих", mine["total"] == 1)

    print("== 403: сотрудник в HR-эндпоинте ==")
    try:
        await client.hr_absences_pending(emp_token)
        check("сотруднику 403", False)
    except Forbidden:
        check("сотруднику 403", True)

    print("== HR: очередь и решение ==")
    pending = await client.hr_absences_pending(hr_token)
    check("в очереди 2 заявки", pending["total"] == 2)
    first = pending["items"][0]["id"]
    res = await client.hr_approve_absence(hr_token, first)
    check("заявка одобрена", res["status"] == "approved")
    try:
        await client.hr_approve_absence(hr_token, first)
        check("повторное решение -> 409", False)
    except Conflict:
        check("повторное решение -> 409", True)
    pending2 = await client.hr_absences_pending(hr_token)
    check("очередь уменьшилась", pending2["total"] == 1)

    today = await client.hr_attendance_today(hr_token)
    check("сводка дня по контракту",
          {"date", "office", "total_employees", "present", "late", "absent",
           "late_list"} <= set(today))
    found = await client.hr_employees(hr_token, "Иванов")
    check("поиск сотрудника работает", found["total"] == 1)

    print("== 401 ==")
    try:
        await client.me("garbage-token")
        check("чужой токен -> 401", False)
    except Unauthorized:
        check("чужой токен -> 401", True)

    print("== маппинг HTTP-статусов live-клиента ==")
    from src.api.errors import ServerError, error_for
    check("409 -> Conflict", isinstance(error_for(409, "conflict", "x"), Conflict))
    check("403 -> Forbidden", isinstance(error_for(403, "forbidden", "x"), Forbidden))
    check("401 -> Unauthorized",
          isinstance(error_for(401, "invalid_token", "x"), Unauthorized))
    check("500 -> ServerError",
          isinstance(error_for(500, "internal_error", "x"), ServerError))

    await client.close()
    print(f"\nИТОГО: {ok} PASS, {fail} FAIL")
    sys.exit(1 if fail else 0)


asyncio.run(main())
