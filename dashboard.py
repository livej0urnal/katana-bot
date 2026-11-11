import os
import secrets
from html import escape
from urllib.parse import parse_qs

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from main import PROJECT_DIR, open_settings
from panel_auth import sign, verify


app = FastAPI(title="Katana Moderator", docs_url=None, redoc_url=None, openapi_url=None)
basic = HTTPBasic()


def authenticate(credentials: HTTPBasicCredentials = Depends(basic)) -> None:
    load_dotenv(PROJECT_DIR / ".env")
    password = os.getenv("DASHBOARD_PASSWORD", "")
    if (
        not password or credentials.username != "admin"
        or not secrets.compare_digest(credentials.password.encode(), password.encode())
    ):
        raise HTTPException(401, headers={"WWW-Authenticate": 'Basic realm="Katana"'})


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(authenticate)])
def overview():
    with open_settings(os.getenv("DATABASE_URL", "").strip()) as settings:
        rows = []
        for chat_id, title, chat_type, status in settings.registered_groups():
            counts = " · ".join(f"{action}: {count}" for action, count in settings.action_totals(chat_id))
            rows.append(
                f"<tr><th scope='row'>{escape(title)}<small>{chat_id}</small></th>"
                f"<td>{escape(chat_type)} · {escape(status)}</td>"
                f"<td>{escape(counts or 'Пока нет действий')}</td></tr>"
            )
    table_rows = "".join(rows) or "<tr><td colspan='3'>Подключите группу командой /settings.</td></tr>"
    return HTMLResponse(
        """<!doctype html><html lang="ru"><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Katana Moderator</title>
        <style>
        body{font:16px system-ui;margin:0;background:#131416;color:#f4f2ee}
        main{max-width:960px;margin:0 auto;padding:32px 20px}
        h1{font-size:clamp(2rem,6vw,4rem);margin:.4em 0;letter-spacing:-.06em}
        h2{font-size:1.1rem;color:#e99a89}
        .groups{overflow:auto;border:1px solid #3e3f43;border-radius:12px}
        table{border-collapse:collapse;width:100%;text-align:left}
        th,td{padding:14px;border-bottom:1px solid #3e3f43}
        tbody tr:last-child>*{border-bottom:0}
        thead{background:#27282c}
        small{display:block;color:#b9b9bd;font-weight:400}
        </style></head><body><main><h1>Katana Moderator</h1><h2>Группы</h2>
        <div class="groups"><table><thead><tr><th>Группа</th><th>Статус бота</th>
        <th>Журнал действий</th></tr></thead><tbody>"""
        + table_rows
        + "</tbody></table></div></main></body></html>",
        headers={"Cache-Control": "no-store"},
    )


# Group sessions are scoped to a Telegram administrator and checked against Telegram on every request.
COOKIE = "katana_group_session"


def dashboard_secret() -> str:
    load_dotenv(PROJECT_DIR / ".env")
    return os.getenv("DASHBOARD_SESSION_SECRET", "")


async def verify_admin(user_id: int, chat_id: int):
    try:
        async with Bot(os.environ["BOT_TOKEN"]) as bot:
            member = await bot.get_chat_member(chat_id, user_id)
            if member.status not in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}:
                raise HTTPException(403, "Требуются права администратора группы.")
            return await bot.get_chat_member(chat_id, bot.id)
    except (TelegramAPIError, KeyError, ValueError):
        raise HTTPException(503, "Не удалось проверить права в Telegram.")


async def form_data(request: Request) -> dict[str, list[str]]:
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 4096:
            raise HTTPException(413)
    try:
        return parse_qs(data.decode(), max_num_fields=5)
    except (UnicodeError, ValueError):
        raise HTTPException(400)


@app.get("/login", response_class=HTMLResponse)
async def login_form():
    return HTMLResponse(
        '<!doctype html><html lang="ru"><meta charset="utf-8"><title>Вход в Katana</title>'
        '<h1>Вход администратора группы</h1><p>Отправьте /panel в своей Telegram-группе.</p>'
        '<form method="post"><label>Код из личного чата <input name="code" required></label>'
        '<button>Войти</button></form>',
        headers={"Cache-Control": "no-store"},
    )


@app.post("/login")
async def login(request: Request):
    if request.headers.get("origin") != str(request.base_url).rstrip("/"):
        raise HTTPException(403)
    secret = dashboard_secret()
    data = await form_data(request)
    claims = verify(secret, "login", data.get("code", [""])[0].strip())
    if claims is None:
        raise HTTPException(403, "Код неверен или истёк.")
    user_id, chat_id, _ = claims
    await verify_admin(user_id, chat_id)
    response = RedirectResponse("/group", status_code=303)
    response.set_cookie(
        COOKIE, sign(secret, "session", user_id, chat_id, 1800), max_age=1800,
        httponly=True, samesite="strict", secure=request.url.scheme == "https",
    )
    return response


@app.get("/group", response_class=HTMLResponse)
async def group_page(request: Request):
    claims = verify(dashboard_secret(), "session", request.cookies.get(COOKIE, ""))
    if claims is None:
        return RedirectResponse("/login", status_code=303)
    user_id, chat_id, nonce = claims
    await verify_admin(user_id, chat_id)
    with open_settings(os.getenv("DATABASE_URL", "").strip()) as settings:
        actions = settings.action_totals(chat_id)
        options = [(option, settings.option(chat_id, option)) for option in ("flood", "shadow")]
    buttons = "".join(
        f'<form method="post"><input type="hidden" name="csrf" value="{escape(nonce)}">'
        f'<button name="option" value="{option}">{option}: {escape(value)} · переключить</button></form>'
        for option, value in options
    )
    counts = "".join(f"<li>{escape(action)}: {count}</li>" for action, count in actions)
    return HTMLResponse(
        '<!doctype html><html lang="ru"><meta charset="utf-8"><title>Настройки Katana</title>'
        f'<h1>Группа {chat_id}</h1><h2>Настройки</h2>{buttons}<h2>Действия</h2><ul>{counts}</ul>',
        headers={"Cache-Control": "no-store"},
    )


@app.post("/group")
async def change_group(request: Request):
    claims = verify(dashboard_secret(), "session", request.cookies.get(COOKIE, ""))
    if claims is None:
        raise HTTPException(403)
    user_id, chat_id, nonce = claims
    data = await form_data(request)
    option = data.get("option", [""])[0]
    csrf = data.get("csrf", [""])[0]
    if not secrets.compare_digest(nonce.encode(), csrf.encode()) or option not in {"flood", "shadow"}:
        raise HTTPException(403)
    member = await verify_admin(user_id, chat_id)
    with open_settings(os.getenv("DATABASE_URL", "").strip()) as settings:
        enabled = settings.option(chat_id, option) != "on"
        if option == "flood" and enabled and not getattr(member, "can_delete_messages", False):
            raise HTTPException(400, "Боту нужно право удалять сообщения.")
        value = "on" if enabled else "off"
        settings.set_option(chat_id, option, value)
        settings.audit(chat_id, "policy_change", actor_id=user_id, detail=f"{option}: {value}")
    return RedirectResponse("/group", status_code=303)
