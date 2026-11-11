import os
import secrets
from html import escape

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from main import PROJECT_DIR, open_settings


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
