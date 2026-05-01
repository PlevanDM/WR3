"""Headless UI walk — simulates clicking every important button.

Calls the real handler functions with stub Telegram primitives so we
exercise the same code path a user would. Side-effecting integrations
(RemOnline pushes, gallery posts, dashboard refreshes) are stubbed.

Output is a readable report grouped by role + a final pass/fail tally.

Usage:
    .\\.venv\\Scripts\\python.exe scripts\\ui_walk.py
"""
from __future__ import annotations
import asyncio
import sys
import types
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


from scripts._console import configure_console_output


# --- stub side-effecting integrations BEFORE bot imports -----------------
from app.bot.services import sync_ro as _sync_ro
from app.bot.services import gallery as _gallery
from app.bot.services import feed as _feed
from app.bot.services import widgets as _widgets


_ro_log: list[tuple[str, tuple, dict]] = []


async def _ok_photos(*a, **k):
    _ro_log.append(("photos", a, k)); return True


async def _ok_request(*a, **k):
    _ro_log.append(("request", a, k)); return True


async def _ok_status(*a, **k):
    _ro_log.append(("status", a, k)); return True


async def _ok_action(*a, **k):
    _ro_log.append(("action", a, k)); return True


_sync_ro.push_photos_comment = _ok_photos          # type: ignore
_sync_ro.push_request_comment = _ok_request        # type: ignore
_sync_ro.push_request_status_comment = _ok_status  # type: ignore
_sync_ro.push_action_comment = _ok_action          # type: ignore


async def _stub_post_media(*a, **k): return {}
_gallery.post_media = _stub_post_media           # type: ignore


async def _stub_flush(*a, **k): return 0
_feed.flush_events = _stub_flush                 # type: ignore


async def _stub_dash(*a, **k): return None
_widgets.refresh_dashboard = _stub_dash          # type: ignore


# -------------------------------------------------------------------------
from sqlalchemy import delete

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage, StorageKey

from app.db.session import session_scope
from app.db import repo
from app.db.models import (
    User, Role, Order, OrderPhoto, QueueItem, Request, RequestType,
    RequestStatus, Event, PhotoKind,
)
from app.bot import views
from app.bot.handlers import engineer as h_eng
from app.bot.handlers import manager as h_mgr
from app.bot.handlers import reception as h_rec
from app.bot.handlers import admin as h_adm


# ============== fake telegram primitives ==============
@dataclass
class _Out:
    text: str
    kb_rows: list[list[str]] = field(default_factory=list)


class FakeBot:
    """Captures bot-level calls (send_message, edit_*) without networking."""
    id = 1

    def __init__(self):
        self.sent: list[tuple[int, str]] = []
        self.edits: list[tuple[int, int, str]] = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))
        return types.SimpleNamespace(message_id=42, chat=types.SimpleNamespace(id=chat_id))

    async def send_chat_action(self, *a, **k): return None
    async def send_photo(self, *a, **k): return types.SimpleNamespace(message_id=43)
    async def send_document(self, *a, **k): return types.SimpleNamespace(message_id=44)
    async def send_media_group(self, *a, **k): return []
    async def edit_message_text(self, text=None, chat_id=None, message_id=None, **kw):
        self.edits.append((chat_id, message_id, text or ""))
        return None
    async def edit_message_reply_markup(self, *a, **k): return None
    async def pin_chat_message(self, *a, **k): return None
    async def delete_message(self, *a, **k): return None
    async def delete_messages(self, *a, **k): return None
    async def get_me(self):
        return types.SimpleNamespace(id=1, username="warranty3_bot",
                                     first_name="Bot", is_bot=True)


def _kb_to_rows(kb) -> list[list[str]]:
    if kb is None:
        return []
    rows: list[list[str]] = []
    try:
        ikb = getattr(kb, "inline_keyboard", None)
        if ikb is not None:
            for row in ikb:
                rows.append([b.text for b in row])
            return rows
        rkb = getattr(kb, "keyboard", None)
        if rkb is not None:
            for row in rkb:
                rows.append([b.text for b in row])
    except Exception:
        pass
    return rows


class FakeMessage:
    def __init__(self, user_tg_id: int, text: str = "", chat_id: int = 0, bot=None):
        self.from_user = types.SimpleNamespace(
            id=user_tg_id, username="probe", full_name="Probe", is_bot=False,
        )
        self.chat = types.SimpleNamespace(id=chat_id or user_tg_id, type="private")
        self.text = text
        self.html_text = text
        self.message_id = 1
        self.bot = bot
        self.last_answer: Optional[_Out] = None
        self.last_edit: Optional[_Out] = None
        self.photo = None
        self.document = None
        self.media_group_id = None

    async def answer(self, text, reply_markup=None, **kw):
        self.last_answer = _Out(text=text, kb_rows=_kb_to_rows(reply_markup))
        return self

    async def edit_text(self, text, reply_markup=None, **kw):
        self.last_edit = _Out(text=text, kb_rows=_kb_to_rows(reply_markup))
        return self

    async def edit_reply_markup(self, reply_markup=None, **kw):
        prev_text = (self.last_edit.text if self.last_edit
                     else (self.last_answer.text if self.last_answer else ""))
        self.last_edit = _Out(text=prev_text, kb_rows=_kb_to_rows(reply_markup))
        return self

    async def reply(self, text, reply_markup=None, **kw):
        return await self.answer(text, reply_markup=reply_markup, **kw)


class FakeCB:
    """CallbackQuery-like stub.

    A few production handlers fall back to "is this a Message?" via
    ``isinstance(target, CallbackQuery)``. Since we are not real aiogram, we
    expose the same surface as both a CB *and* a Message: ``edit_text`` /
    ``edit_reply_markup`` / ``text`` / ``html_text`` / ``chat`` /
    ``message_id`` are forwarded to the inner FakeMessage.
    """
    def __init__(self, data: str, message: FakeMessage, user_tg_id: int, bot=None):
        self.data = data
        self.message = message
        self.from_user = types.SimpleNamespace(
            id=user_tg_id, username="probe", full_name="Probe", is_bot=False,
        )
        self.id = "fake"
        self.bot = bot
        self.tost: Optional[str] = None
        self.tost_alert: bool = False

    # --- CallbackQuery surface ---
    async def answer(self, text: str | None = None, show_alert: bool = False, **kw):
        self.tost = text or ""
        self.tost_alert = show_alert
        return None

    # --- Message-shaped fallback (some handlers treat target uniformly) ---
    @property
    def text(self) -> str:
        return self.message.text or ""

    @property
    def html_text(self) -> str:
        return self.message.html_text or ""

    @property
    def chat(self):
        return self.message.chat

    @property
    def message_id(self) -> int:
        return self.message.message_id

    async def edit_text(self, *a, **k):
        return await self.message.edit_text(*a, **k)

    async def edit_reply_markup(self, *a, **k):
        return await self.message.edit_reply_markup(*a, **k)


def _fsm_for(tg_id: int) -> FSMContext:
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=tg_id, user_id=tg_id)
    return FSMContext(storage=storage, key=key)


# ============== fixtures ==============
PROBE_PREFIX = "[UI-WALK]"
SIM_TG = {Role.reception: -8001, Role.engineer: -8002,
          Role.manager: -8003, Role.admin: -8004}


async def _fixture() -> dict:
    """Create users + 2 orders with photos + a couple of open requests."""
    now = datetime.now(timezone.utc)
    users: dict[Role, User] = {}
    async with session_scope() as s:
        for role, tg in SIM_TG.items():
            users[role] = await repo.upsert_user(
                s, tg, role,
                username=f"sim_{role.value}",
                full_name=f"Sim {role.value.title()}",
            )

    oid_a = 999_900_001
    oid_b = 999_900_002
    oid_paid = 999_900_003
    async with session_scope() as s:
        await repo.upsert_order(s, {
            "id": oid_a, "number": f"{PROBE_PREFIX}-A",
            "status_id": 1, "status_name": "Принят",
            "client_name": "UI Walk Client",
            "device": "iPhone 12 Pro Max", "serial": "SN-A",
            "defect": "—", "created_at_ro": now, "last_activity_at": now,
            "raw": {}, "is_paid": False,
        })
        await repo.upsert_order(s, {
            "id": oid_b, "number": f"{PROBE_PREFIX}-B",
            "status_id": 1, "status_name": "Принят",
            "client_name": "UI Walk Client",
            "device": "MacBook Air M2", "serial": "SN-B",
            "defect": "—", "created_at_ro": now, "last_activity_at": now,
            "raw": {}, "is_paid": False,
        })
        await repo.upsert_order(s, {
            "id": oid_paid, "number": f"{PROBE_PREFIX}-PAID",
            "status_id": 1, "status_name": "Принят (платный)",
            "client_name": "UI Walk Client",
            "device": "iPad Pro", "serial": "SN-PAID",
            "defect": "—", "created_at_ro": now, "last_activity_at": now,
            "raw": {}, "is_paid": True,
        })
        await repo.ensure_queue_item(s, oid_a)
        await repo.ensure_queue_item(s, oid_b)
        await repo.ensure_queue_item(s, oid_paid)
        await repo.add_photo(
            s, oid_a, PhotoKind.other, "fid-1",
            chat_id=None, message_id=None,
            uploaded_by=users[Role.reception].id, media_type="photo",
        )
        r1 = await repo.create_request(
            s, oid_a, RequestType.asbis, "будет сделано",
            created_by=users[Role.engineer].id,
        )
        r2 = await repo.create_request(
            s, oid_b, RequestType.it4, "оформи списание",
            created_by=users[Role.engineer].id,
        )
    return {"users": users, "oid_a": oid_a, "oid_b": oid_b, "oid_paid": oid_paid,
            "rid_open_asbis": r1.id, "rid_open_it4": r2.id}


async def _cleanup() -> None:
    async with session_scope() as s:
        for tg in SIM_TG.values():
            u = await repo.get_user_by_tg(s, tg)
            if u:
                await s.execute(delete(User).where(User.id == u.id))
        for oid in (999_900_001, 999_900_002, 999_900_003):
            await s.execute(delete(OrderPhoto).where(OrderPhoto.order_id == oid))
            await s.execute(delete(QueueItem).where(QueueItem.order_id == oid))
            await s.execute(delete(Request).where(Request.order_id == oid))
            await s.execute(delete(Event).where(Event.order_id == oid))
            await s.execute(delete(Order).where(Order.id == oid))


# ============== walker ==============
class Walker:
    def __init__(self):
        self.bot = FakeBot()
        self.lines: list[str] = []
        self.checks: list[tuple[str, bool, str]] = []

    def head(self, t):
        self.lines.append("")
        self.lines.append(f"━━━ {t} ━━━")

    def out(self, label, text, kb_rows):
        self.lines.append(f"  ▶ {label}")
        snippet = (text or "").splitlines()
        head = " · ".join(snippet[:1])[:140]
        if len(snippet) > 1:
            head += f"  …(+{len(snippet) - 1} строк)"
        self.lines.append(f"     [text] {head}")
        if kb_rows:
            for row in kb_rows[:6]:
                line = " | ".join(row)
                if len(line) > 140:
                    line = line[:137] + "…"
                self.lines.append("     [btns] " + line)
            if len(kb_rows) > 6:
                self.lines.append(f"     [btns] … ещё {len(kb_rows) - 6} рядов")
        else:
            self.lines.append("     [btns] —")

    def add_check(self, name: str, ok: bool, detail: str = ""):
        self.checks.append((name, ok, detail))
        mark = "✓" if ok else "✗"
        msg = f"     {mark} {name}"
        if detail:
            d = detail[:120].replace("\n", " ")
            msg += f" — {d}"
        self.lines.append(msg)

    # ---------- engineer ----------
    async def role_engineer(self, fix):
        eng = fix["users"][Role.engineer]
        self.head("ENGINEER")

        text, kb = await views.render_queue_view(eng, page=0)
        self.out("📋 Очередь", text, _kb_to_rows(kb))
        self.add_check("queue.opens", "Очередь" in text)

        first_oid = fix["oid_a"]
        out = await views.render_queue_order_view(eng, first_oid, 0, self.bot)
        if out:
            t2, kb2 = out
            self.out("Клик на карточку #A → детали", t2, _kb_to_rows(kb2))
            btns = [r for row in _kb_to_rows(kb2) for r in row]
            self.add_check("queue.order_card.has_take",
                           any("Взять" in b for b in btns))
            self.add_check("queue.order_card.has_remonline",
                           any("RemOnline" in b for b in btns))
            self.add_check("queue.order_card.has_request",
                           any("Запрос" in b or "запрос" in b for b in btns))
        else:
            self.add_check("queue.order_card.has_take", False, "card not rendered")

        # take
        msg = FakeMessage(eng.tg_id, bot=self.bot)
        cb = FakeCB(f"dq:tk:{first_oid}:0", msg, eng.tg_id, bot=self.bot)
        await h_eng.cb_queue_take(cb, eng, self.bot)
        self.add_check("queue.take", cb.tost is not None and "Взял" in (cb.tost or ""),
                       cb.tost or "")
        # untake
        cb = FakeCB(f"dq:un:{first_oid}:0", msg, eng.tg_id, bot=self.bot)
        await h_eng.cb_queue_untake(cb, eng, self.bot)
        self.add_check("queue.untake", cb.tost is not None, cb.tost or "")

        # mine
        text, kb = await views.render_mine_view(eng, page=0)
        self.out("🧰 Мои заказы", text, _kb_to_rows(kb))
        self.add_check("mine.opens", "Мои заказы" in text)

        # myreq
        text, kb = await views.render_myreq_view(eng, flt="open")
        self.out("🗂 Мои запросы (открытые)", text, _kb_to_rows(kb))
        self.add_check("myreq.opens", "Мои запросы" in text)

        text, kb = await views.render_myreq_view(eng, flt="closed")
        self.out("Фильтр «Закрытые»", text, _kb_to_rows(kb))

        # New request via the reply-button
        msg = FakeMessage(eng.tg_id, bot=self.bot)
        st = _fsm_for(eng.tg_id)
        try:
            await h_eng.new_request_button(msg, eng, st)
            ans = msg.last_answer
            self.out("«📨 Новый запрос» → выбор заказа",
                     ans.text if ans else "—",
                     ans.kb_rows if ans else [])
            self.add_check("request.picker_shown",
                           ans is not None and "запрос" in (ans.text or "").lower())
        except Exception as e:
            self.add_check("request.picker_shown", False, f"{type(e).__name__}: {e}")

        # Pick an order from the picker — go to type picker
        msg = FakeMessage(eng.tg_id, bot=self.bot)
        cb = FakeCB(f"eng:req:pick:{fix['oid_b']}", msg, eng.tg_id, bot=self.bot)
        try:
            await h_eng.cb_pick_order(cb, eng, st)
            self.out("Клик на заказ → выбор типа запроса",
                     msg.last_edit.text if msg.last_edit else (msg.last_answer.text if msg.last_answer else "—"),
                     msg.last_edit.kb_rows if msg.last_edit else (msg.last_answer.kb_rows if msg.last_answer else []))
            outp = msg.last_edit or msg.last_answer
            self.add_check("request.type_picker", outp is not None and "Асбис" in (outp.text if outp else ""))
        except Exception as e:
            self.add_check("request.type_picker", False, f"{type(e).__name__}: {e}")

        # Paid-order flow: must show paid menu (no ASBIS/IT4) and block ASBIS.
        msg = FakeMessage(eng.tg_id, bot=self.bot)
        cb = FakeCB(f"eng:reqmenu:{fix['oid_paid']}", msg, eng.tg_id, bot=self.bot)
        try:
            await h_eng.on_request_menu(cb, eng, st)
            outp = msg.last_edit or msg.last_answer
            txt = outp.text if outp else ""
            btns = " | ".join(b for row in (outp.kb_rows if outp else []) for b in row)
            self.out("Клик «Запрос» на 💰 платном", txt, outp.kb_rows if outp else [])
            self.add_check("paid.menu_shown",
                           "Платный" in txt and "Платному инженеру" in btns and "Асбис" not in btns,
                           btns[:120])
        except Exception as e:
            self.add_check("paid.menu_shown", False, f"{type(e).__name__}: {e}")

        msg = FakeMessage(eng.tg_id, bot=self.bot)
        cb = FakeCB(f"eng:req:asbis:{fix['oid_paid']}", msg, eng.tg_id, bot=self.bot)
        try:
            await h_eng.on_request_start(cb, eng, st, self.bot)
            self.add_check("paid.asbis_blocked",
                           cb.tost is not None and "Платный" in (cb.tost or ""),
                           cb.tost or "")
        except Exception as e:
            self.add_check("paid.asbis_blocked", False, f"{type(e).__name__}: {e}")

        # Cancel an authored request from "Мои запросы"
        rid = fix["rid_open_asbis"]
        msg = FakeMessage(eng.tg_id, bot=self.bot)
        cb = FakeCB(f"eng:reqown:cancel:{rid}", msg, eng.tg_id, bot=self.bot)
        await h_eng.cb_own_cancel(cb, eng, self.bot)
        self.add_check("myreq.own_cancel",
                       (cb.tost is not None) or (msg.last_answer is not None) or (msg.last_edit is not None),
                       cb.tost or "")

    # ---------- manager ----------
    async def role_manager(self, fix):
        mgr = fix["users"][Role.manager]
        self.head("MANAGER")

        text, kb = await views.render_inbox_view(mgr, flt="all", page=0)
        self.out("📨 Входящие · Все", text, _kb_to_rows(kb))
        self.add_check("inbox.opens", "Входящие" in text)

        text, kb = await views.render_inbox_view(mgr, flt="asbis", page=0)
        self.out("Фильтр «Асбис»", text, _kb_to_rows(kb))
        text, kb = await views.render_inbox_view(mgr, flt="it4", page=0)
        self.out("Фильтр «ИТ4»", text, _kb_to_rows(kb))

        # open inbox request card (it4 — supports подтвердить/отклонить)
        rid = fix["rid_open_it4"]
        out = await views.render_inbox_request_view(mgr, rid, "all", 0)
        if out:
            t, kb = out
            self.out(f"Клик «Запрос #{rid}»", t, _kb_to_rows(kb))
            btns = [b for row in _kb_to_rows(kb) for b in row]
            self.add_check("inbox.card.has_done", any("Подтвердить" in b for b in btns))
            self.add_check("inbox.card.has_reject", any("Отклон" in b for b in btns))

        # confirm
        msg = FakeMessage(mgr.tg_id, bot=self.bot)
        cb = FakeCB(f"mgr:req:done:{rid}", msg, mgr.tg_id, bot=self.bot)
        try:
            await h_mgr.on_change(cb, mgr, self.bot)
            self.add_check("inbox.confirm", cb.tost is not None or msg.last_edit is not None,
                           cb.tost or "")
        except Exception as e:
            self.add_check("inbox.confirm", False, f"{type(e).__name__}: {e}")

        # reject preset on the asbis request
        rid2 = fix["rid_open_asbis"]
        # open the preset menu
        msg = FakeMessage(mgr.tg_id, bot=self.bot)
        st = _fsm_for(mgr.tg_id)
        cb = FakeCB(f"mgr:req:rejc:{rid2}", msg, mgr.tg_id, bot=self.bot)
        try:
            await h_mgr.ask_reject_comment(cb, mgr, st)
            self.out("Клик «Отклонить с комм.» → просьба коммента",
                     (msg.last_edit or msg.last_answer).text if (msg.last_edit or msg.last_answer) else "—",
                     (msg.last_edit or msg.last_answer).kb_rows if (msg.last_edit or msg.last_answer) else [])
            self.add_check("inbox.reject_prompt",
                           (msg.last_edit or msg.last_answer) is not None)
        except Exception as e:
            self.add_check("inbox.reject_prompt", False, f"{type(e).__name__}: {e}")

        # apply preset «Нет данных»
        msg = FakeMessage(mgr.tg_id, bot=self.bot)
        cb = FakeCB(f"mgr:req:rejr:{rid2}:nodata", msg, mgr.tg_id, bot=self.bot)
        try:
            await h_mgr.reject_with_preset(cb, mgr, self.bot, st)
            self.add_check("inbox.reject_preset",
                           cb.tost is not None or msg.last_edit is not None,
                           cb.tost or "")
        except Exception as e:
            self.add_check("inbox.reject_preset", False, f"{type(e).__name__}: {e}")

        text, kb = await views.render_inbox_view(mgr, flt="all", page=0)
        self.out("Снова «Входящие» (после действий)", text, _kb_to_rows(kb))

    # ---------- reception ----------
    async def role_reception(self, fix):
        rec = fix["users"][Role.reception]
        self.head("RECEPTION")

        text, kb = await views.render_reception_nophoto_view(rec, page=0)
        self.out("📷 Без фото", text, _kb_to_rows(kb))
        self.add_check("reception.nophoto", text != "")

        text, kb = await views.render_reception_stale_view(rec, page=0)
        self.out("⏳ Застряли", text, _kb_to_rows(kb))
        self.add_check("reception.stale", text != "")

        # Click "📷" on the no-photo card → start the FSM
        st = _fsm_for(rec.tg_id)
        msg = FakeMessage(rec.tg_id, bot=self.bot)
        cb = FakeCB(f"rcp:photo:{fix['oid_b']}", msg, rec.tg_id, bot=self.bot)
        try:
            await h_rec.start_photos(cb, rec, st)
            ans = msg.last_answer
            self.out("Клик «📷 Загрузить фото»",
                     ans.text if ans else "—",
                     ans.kb_rows if ans else [])
            self.add_check("reception.upload_prompt", ans is not None and ("фото" in (ans.text or "").lower()))
        except Exception as e:
            self.add_check("reception.upload_prompt", False, f"{type(e).__name__}: {e}")

        # Press cancel
        msg = FakeMessage(rec.tg_id, bot=self.bot)
        cb = FakeCB("rcp:cancel", msg, rec.tg_id, bot=self.bot)
        try:
            await h_rec.on_cancel(cb, st)
            self.add_check("reception.upload_cancel",
                           cb.tost is not None or msg.last_edit is not None,
                           cb.tost or "")
        except Exception as e:
            self.add_check("reception.upload_cancel", False, f"{type(e).__name__}: {e}")

    # ---------- admin ----------
    async def role_admin(self, fix):
        adm = fix["users"][Role.admin]
        self.head("ADMIN")

        # /admin
        msg = FakeMessage(adm.tg_id, bot=self.bot)
        try:
            await h_adm.admin_menu(msg, adm)
            ans = msg.last_answer
            self.out("/admin", ans.text if ans else "—",
                     ans.kb_rows if ans else [])
            self.add_check("admin.menu", ans is not None and "Админ" in (ans.text or ""))
        except Exception as e:
            self.add_check("admin.menu", False, f"{type(e).__name__}: {e}")

        # Users
        msg = FakeMessage(adm.tg_id, bot=self.bot)
        cb = FakeCB("adm:users", msg, adm.tg_id, bot=self.bot)
        try:
            await h_adm.admin_users(cb, adm)
            self.out("👥 Пользователи",
                     msg.last_edit.text if msg.last_edit else "—",
                     msg.last_edit.kb_rows if msg.last_edit else [])
            self.add_check("admin.users", msg.last_edit is not None)
        except Exception as e:
            self.add_check("admin.users", False, f"{type(e).__name__}: {e}")

        # Settings
        msg = FakeMessage(adm.tg_id, bot=self.bot)
        cb = FakeCB("adm:settings", msg, adm.tg_id, bot=self.bot)
        try:
            await h_adm.admin_settings(cb, adm)
            self.out("⚙️ Настройки",
                     msg.last_edit.text if msg.last_edit else "—",
                     msg.last_edit.kb_rows if msg.last_edit else [])
            self.add_check("admin.settings", msg.last_edit is not None)
        except Exception as e:
            self.add_check("admin.settings", False, f"{type(e).__name__}: {e}")

        # Diagnostics (touches RO read-only)
        msg = FakeMessage(adm.tg_id, bot=self.bot)
        cb = FakeCB("adm:diag", msg, adm.tg_id, bot=self.bot)
        try:
            await h_adm.admin_diag(cb, adm)
            txt = (msg.last_edit.text if msg.last_edit
                   else (msg.last_answer.text if msg.last_answer else ""))
            self.out("🩺 Диагностика", txt, [])
            self.add_check("admin.diag", "иагност" in txt or "RemOnline" in txt)
        except Exception as e:
            self.add_check("admin.diag", False, f"{type(e).__name__}: {e}")

        # Invite menu
        msg = FakeMessage(adm.tg_id, bot=self.bot)
        cb = FakeCB("adm:invite", msg, adm.tg_id, bot=self.bot)
        try:
            await h_adm.cb_invite(cb, adm)
            self.out("➕ Пригласить",
                     msg.last_edit.text if msg.last_edit else "—",
                     msg.last_edit.kb_rows if msg.last_edit else [])
            self.add_check("admin.invite", msg.last_edit is not None)
        except Exception as e:
            self.add_check("admin.invite", False, f"{type(e).__name__}: {e}")

        # Queue: move to top
        first_oid = fix["oid_b"]
        msg = FakeMessage(adm.tg_id, bot=self.bot)
        cb = FakeCB(f"adm:q:top:{first_oid}", msg, adm.tg_id, bot=self.bot)
        try:
            await h_adm.cb_queue_move(cb, adm, self.bot)
            self.add_check("admin.queue_top", cb.tost is not None,
                           cb.tost or "")
            async with session_scope() as s:
                qrows = await repo.queue_page(s, offset=0, limit=2)
            first = qrows[0][1].id if qrows else None
            self.add_check("admin.queue_top.applied", first == first_oid,
                           f"head={first} expected={first_oid}")
        except Exception as e:
            self.add_check("admin.queue_top", False, f"{type(e).__name__}: {e}")

        # Toggle is_paid_engineer for the engineer
        eng = fix["users"][Role.engineer]
        msg = FakeMessage(adm.tg_id, bot=self.bot)
        cb = FakeCB(f"adm:paid:{eng.tg_id}", msg, adm.tg_id, bot=self.bot)
        try:
            await h_adm.cb_toggle_paid_engineer(cb, adm)
            async with session_scope() as s:
                u = await repo.get_user_by_tg(s, eng.tg_id)
            self.add_check("admin.paid_engineer.toggle_on",
                           bool(u and u.is_paid_engineer),
                           f"is_paid_engineer={u.is_paid_engineer if u else '?'}")
            cb = FakeCB(f"adm:paid:{eng.tg_id}", msg, adm.tg_id, bot=self.bot)
            await h_adm.cb_toggle_paid_engineer(cb, adm)
            async with session_scope() as s:
                u = await repo.get_user_by_tg(s, eng.tg_id)
            self.add_check("admin.paid_engineer.toggle_off",
                           bool(u and not u.is_paid_engineer),
                           f"is_paid_engineer={u.is_paid_engineer if u else '?'}")
        except Exception as e:
            self.add_check("admin.paid_engineer.toggle_on", False, f"{type(e).__name__}: {e}")

        # Admin assign-engineer flow: pick the paid order, open picker, assign engineer
        oid_paid = fix["oid_paid"]
        msg = FakeMessage(adm.tg_id, bot=self.bot)
        cb = FakeCB(f"adm:as:{oid_paid}:0", msg, adm.tg_id, bot=self.bot)
        try:
            await h_adm.cb_assign_picker(cb, adm, self.bot)
            outp = msg.last_edit or msg.last_answer
            self.add_check("admin.assign.picker",
                           outp is not None and "Назначить" in (outp.text or ""),
                           (outp.text or "")[:80] if outp else "")
        except Exception as e:
            self.add_check("admin.assign.picker", False, f"{type(e).__name__}: {e}")

        msg = FakeMessage(adm.tg_id, bot=self.bot)
        cb = FakeCB(f"adm:asgo:{oid_paid}:{eng.id}", msg, adm.tg_id, bot=self.bot)
        try:
            await h_adm.cb_assign_apply(cb, adm, self.bot)
            async with session_scope() as s:
                o = await repo.get_order(s, oid_paid)
            self.add_check("admin.assign.applied",
                           bool(o and o.assigned_engineer_id == eng.id),
                           f"assigned={o.assigned_engineer_id if o else '?'}")
        except Exception as e:
            self.add_check("admin.assign.applied", False, f"{type(e).__name__}: {e}")

    # ---------- group / gallery widgets ----------
    async def role_group(self):
        self.head("GROUP / GALLERY")
        from app.bot.services.widgets import (
            _render_stats_widget, _render_queue_widget, _render_requests_widget,
        )
        s = await _render_stats_widget()
        self.out("📊 Виджет статистики", s, [])
        self.add_check("group.stats", "Сводка" in s)
        q = await _render_queue_widget()
        self.out("📋 Виджет очереди", q, [])
        self.add_check("group.queue", "Очередь" in q)
        r = await _render_requests_widget()
        self.out("📨 Виджет запросов", r, [])
        self.add_check("group.requests", "Запросы" in r)


# ============== main ==============
async def main() -> int:
    configure_console_output()
    fix = await _fixture()
    w = Walker()
    try:
        await w.role_engineer(fix)
        await w.role_manager(fix)
        await w.role_reception(fix)
        await w.role_admin(fix)
        await w.role_group()
    finally:
        await _cleanup()

    print("\n".join(w.lines))
    print()
    failed = [(n, d) for n, ok, d in w.checks if not ok]
    total = len(w.checks)
    print(f"=== UI WALK · {total - len(failed)}/{total} OK ===")
    if failed:
        for n, d in failed:
            print(f"  FAIL  {n}{'  — ' + d if d else ''}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
