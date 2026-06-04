#!/usr/bin/env python3
"""
=====================================================================
  BOT TELEGRAM — CONCURS CUPA MONDIALĂ 2026
=====================================================================

PUNCTAJ:
  • Campioana turneului ghicită ........ 5 puncte
  • Câștigătoarea unei grupe ghicită .... 2 puncte (x12 grupe)
  • Meci ghicit (1 / X / 2) ............. 3 puncte

ACCES:
  • Fiecare participant dă /start și intră ÎN AȘTEPTARE.
  • Tu (admin) primești o notificare și îl aprobi cu /approve după
    ce ai încasat cotizația. Doar cei aprobați joacă și apar în clasament.

FLUX PARTICIPANT (aprobat):
  /campioana  -> alege campioana turneului
  /grupe      -> alege câștigătoarea fiecărei grupe
  -> în fiecare zi primește, automat, MECIURILE DE MÂINE (cu ~24h înainte)
  -> pronostichează la fiecare meci până la ora de start
  -> seara primește clasamentul

PREMII (din cotizație, gestionate de tine, cash):
  Locul 1 = 50% + tricou  |  Locul 2 = 30%  |  Locul 3 = 20%

---------------------------------------------------------------------
RESETARE (admin):
  /reset_game CONFIRM  -> șterge pronosticurile și rezultatele,
                          PĂSTREAZĂ jucătorii, meciurile și setările
  /reset_all  CONFIRM  -> șterge TOT (inclusiv jucătorii) și reîncarcă
                          echipele implicite (resetare de fabrică)

CONFIGURARE (variabile de mediu):
  BOT_TOKEN, ADMIN_IDS, TZ, DAILY_MATCHES_HOUR,
  LEADERBOARD_HOUR, LEADERBOARD_MINUTE, DB_PATH
=====================================================================
"""

import os
import logging
import sqlite3
import contextlib
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ------------------------------------------------------------------ #
#  CONFIGURARE
# ------------------------------------------------------------------ #
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_IDS = {
    int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if x
}
TZ = ZoneInfo(os.environ.get("TZ", "Europe/Bucharest"))
DB_PATH = os.environ.get("DB_PATH", "contest.db")
DAILY_HOUR = int(os.environ.get("DAILY_MATCHES_HOUR", "9"))
LB_HOUR = int(os.environ.get("LEADERBOARD_HOUR", "23"))
LB_MIN = int(os.environ.get("LEADERBOARD_MINUTE", "30"))

POINTS_MATCH = 3
POINTS_GROUP = 2
POINTS_CHAMPION = 5

logging.basicConfig(
    format="%(asctime)s — %(name)s — %(levelname)s — %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("cupa-bot")

# ------------------------------------------------------------------ #
#  ECHIPELE ȘI GRUPELE — Cupa Mondială 2026
# ------------------------------------------------------------------ #
TEAMS = {
    "A": ["Cehia", "Mexic", "Africa de Sud", "Coreea de Sud"],
    "B": ["Elveția", "Bosnia și Herțegovina", "Canada", "Qatar"],
    "C": ["Scoția", "Brazilia", "Haiti", "Maroc"],
    "D": ["Turcia", "Paraguay", "SUA", "Australia"],
    "E": ["Germania", "Ecuador", "Coasta de Fildeș", "Curacao"],
    "F": ["Suedia", "Țările de Jos", "Tunisia", "Japonia"],
    "G": ["Belgia", "Egipt", "Iran", "Noua Zeelandă"],
    "H": ["Spania", "Uruguay", "Capul Verde", "Arabia Saudită"],
    "I": ["Franța", "Norvegia", "Senegal", "Irak"],
    "J": ["Austria", "Argentina", "Algeria", "Iordania"],
    "K": ["Portugalia", "Columbia", "DR Congo", "Uzbekistan"],
    "L": ["Croația", "Anglia", "Ghana", "Panama"],
}

# ------------------------------------------------------------------ #
#  BAZA DE DATE
# ------------------------------------------------------------------ #
@contextlib.contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    # asigură că folderul bazei de date există (ex. /data de pe volum)
    d = os.path.dirname(os.path.abspath(DB_PATH))
    os.makedirs(d, exist_ok=True)
    with db() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS users(
                user_id   INTEGER PRIMARY KEY,
                username  TEXT,
                full_name TEXT,
                approved  INTEGER DEFAULT 0,
                joined_at TEXT
            );
            CREATE TABLE IF NOT EXISTS teams(
                team_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT UNIQUE,
                group_label TEXT
            );
            CREATE TABLE IF NOT EXISTS matches(
                match_id INTEGER PRIMARY KEY AUTOINCREMENT,
                home     TEXT,
                away     TEXT,
                kickoff  TEXT,
                stage    TEXT,
                result   TEXT
            );
            CREATE TABLE IF NOT EXISTS match_predictions(
                user_id    INTEGER,
                match_id   INTEGER,
                pick       TEXT,
                created_at TEXT,
                PRIMARY KEY(user_id, match_id)
            );
            CREATE TABLE IF NOT EXISTS group_picks(
                user_id     INTEGER,
                group_label TEXT,
                team_name   TEXT,
                PRIMARY KEY(user_id, group_label)
            );
            CREATE TABLE IF NOT EXISTS champion_picks(
                user_id   INTEGER PRIMARY KEY,
                team_name TEXT
            );
            CREATE TABLE IF NOT EXISTS group_results(
                group_label TEXT PRIMARY KEY,
                winner_team TEXT
            );
            CREATE TABLE IF NOT EXISTS config(
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        # migrare: adaugă coloana approved dacă lipsește (baze vechi)
        cols = [r["name"] for r in c.execute("PRAGMA table_info(users)").fetchall()]
        if "approved" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN approved INTEGER DEFAULT 0")
        # populează echipele dacă tabelul e gol
        if not c.execute("SELECT 1 FROM teams LIMIT 1").fetchone():
            for label, names in TEAMS.items():
                for n in names:
                    c.execute(
                        "INSERT OR IGNORE INTO teams(name, group_label) VALUES(?,?)",
                        (n, label),
                    )


def get_config(key):
    with db() as c:
        r = c.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        return r["value"] if r else None


def set_config(key, value):
    with db() as c:
        c.execute(
            "INSERT INTO config(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )


# ------------------------------------------------------------------ #
#  UTILITARE
# ------------------------------------------------------------------ #
def is_admin(update: Update) -> bool:
    return update.effective_user and update.effective_user.id in ADMIN_IDS


def now_local() -> datetime:
    return datetime.now(TZ).replace(tzinfo=None)


def is_approved(uid: int) -> bool:
    if uid in ADMIN_IDS:
        return True
    with db() as c:
        r = c.execute("SELECT approved FROM users WHERE user_id=?", (uid,)).fetchone()
        return bool(r and r["approved"])


async def guard(update: Update) -> bool:
    """Lasă să treacă doar participanții aprobați."""
    if is_approved(update.effective_user.id):
        return True
    await update.message.reply_text(
        "⏳ Ești în așteptare. Organizatorul te aprobă după ce achiți cotizația.\n"
        "După ce ești aprobat, dă din nou /campioana."
    )
    return False


def picks_open() -> bool:
    dl = get_config("picks_deadline")
    if not dl:
        return True
    try:
        return now_local() < datetime.strptime(dl, "%Y-%m-%d %H:%M")
    except ValueError:
        return True


def match_open(kickoff: str) -> bool:
    try:
        return now_local() < datetime.strptime(kickoff, "%Y-%m-%d %H:%M")
    except ValueError:
        return True


def is_group_stage(stage: str) -> bool:
    s = (stage or "").lower()
    return "grup" in s or "group" in s


def register_user(update: Update):
    u = update.effective_user
    appr = 1 if u.id in ADMIN_IDS else 0
    with db() as c:
        c.execute(
            "INSERT INTO users(user_id, username, full_name, approved, joined_at) "
            "VALUES(?,?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, "
            "full_name=excluded.full_name",
            (u.id, u.username or "", u.full_name, appr, now_local().isoformat()),
        )


def pick_label(home, away, pick):
    return {"HOME": home, "DRAW": "Egal", "AWAY": away}.get(pick, pick)


def matches_on(date_str):
    with db() as c:
        return c.execute(
            "SELECT * FROM matches WHERE kickoff LIKE ? ORDER BY kickoff",
            (date_str + "%",),
        ).fetchall()


async def notify_admins(context, text):
    for aid in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=aid, text=text)
        except Exception:
            pass


# ------------------------------------------------------------------ #
#  CALCULUL PUNCTAJULUI (doar participanți aprobați)
# ------------------------------------------------------------------ #
def compute_scores():
    with db() as c:
        users = c.execute(
            "SELECT user_id, full_name, username FROM users WHERE approved=1"
        ).fetchall()
        match_results = dict(
            c.execute(
                "SELECT match_id, result FROM matches WHERE result IS NOT NULL"
            ).fetchall()
        )
        preds = c.execute(
            "SELECT user_id, match_id, pick FROM match_predictions"
        ).fetchall()
        group_results = dict(
            c.execute("SELECT group_label, winner_team FROM group_results").fetchall()
        )
        gpicks = c.execute(
            "SELECT user_id, group_label, team_name FROM group_picks"
        ).fetchall()
        cpicks = dict(
            c.execute("SELECT user_id, team_name FROM champion_picks").fetchall()
        )

    champ = get_config("champion_winner")
    approved_ids = {u["user_id"] for u in users}

    match_pts = {uid: 0 for uid in approved_ids}
    for p in preds:
        if p["user_id"] in approved_ids:
            res = match_results.get(p["match_id"])
            if res and res == p["pick"]:
                match_pts[p["user_id"]] += POINTS_MATCH

    group_pts = {uid: 0 for uid in approved_ids}
    for g in gpicks:
        if g["user_id"] in approved_ids and group_results.get(g["group_label"]) == g["team_name"]:
            group_pts[g["user_id"]] += POINTS_GROUP

    champ_pts = {uid: 0 for uid in approved_ids}
    if champ:
        for uid, team in cpicks.items():
            if uid in approved_ids and team == champ:
                champ_pts[uid] = POINTS_CHAMPION

    rows = []
    for u in users:
        uid = u["user_id"]
        m, g, ch = match_pts.get(uid, 0), group_pts.get(uid, 0), champ_pts.get(uid, 0)
        rows.append((u, m + g + ch, m, g, ch))
    rows.sort(key=lambda x: x[1], reverse=True)
    return rows


def leaderboard_text():
    rows = compute_scores()
    if not rows:
        return "Încă nu există participanți aprobați."
    medals = {0: "🥇", 1: "🥈", 2: "🥉"}
    lines = ["🏆 CLASAMENT\n"]
    for i, (u, total, m, g, ch) in enumerate(rows):
        name = u["full_name"] or (u["username"] and "@" + u["username"]) or str(u["user_id"])
        prefix = medals.get(i, f"{i + 1}.")
        lines.append(f"{prefix} {name} — {total} pct  (M:{m} G:{g} C:{ch})")
    lines.append("\nLegendă: M = meciuri, G = grupe, C = campioană")
    return "\n".join(lines)


# ------------------------------------------------------------------ #
#  COMENZI PARTICIPANȚI
# ------------------------------------------------------------------ #
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    with db() as c:
        existing = c.execute(
            "SELECT approved FROM users WHERE user_id=?", (u.id,)
        ).fetchone()
    register_user(update)
    approved = is_approved(u.id)

    if approved:
        txt = (
            "⚽️ *Bun venit la Concursul Cupa Mondială!*\n\n"
            "Punctaj: campioană 5p · grupă 2p · meci 3p\n\n"
            "Pași:\n"
            "1️⃣ /campioana — alege campioana turneului\n"
            "2️⃣ /grupe — alege câștigătoarea fiecărei grupe\n"
            "3️⃣ În fiecare zi primești meciurile de mâine și pronostichezi cu un tap.\n\n"
            "/azi · /maine · /pronosticurile · /clasament · /premii · /ajutor"
        )
        await update.message.reply_text(txt, parse_mode="Markdown")
    else:
        await update.message.reply_text(
            "👋 Salut! Te-ai înscris la concurs.\n\n"
            "⏳ Ești *în așteptare* — organizatorul te aprobă după ce achiți cotizația.\n"
            "Imediat ce ești aprobat primești un mesaj și poți începe pronosticurile.",
            parse_mode="Markdown",
        )
        if not existing:
            await notify_admins(
                context,
                f"🆕 Cerere de înscriere:\n"
                f"{u.full_name} (@{u.username or '—'})\n"
                f"ID: {u.id}\n\n"
                f"Aprobă cu:  /approve {u.id}",
            )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "Comenzi:\n"
        "/campioana — alege campioana\n"
        "/grupe — alege câștigătoarele de grupă\n"
        "/azi — meciurile de azi\n"
        "/maine — meciurile de mâine\n"
        "/pronosticurile — pronosticurile tale\n"
        "/clasament — clasamentul\n"
        "/premii — premiile"
    )
    if is_admin(update):
        txt += (
            "\n\n🔧 Admin:\n"
            "/pending — cereri în așteptare\n"
            "/approve <id sau @user> — aprobă participant\n"
            "/deny <id> — respinge/scoate participant\n"
            "/participanti — lista celor aprobați\n"
            "/addmatch Echipa1 | Echipa2 | YYYY-MM-DD HH:MM | Faza\n"
            "/setresult <id_meci> <HOME|DRAW|AWAY>\n"
            "/setgroupwinner <Grupa> <Echipa>\n"
            "/setchampion <Echipa>\n"
            "/setpot <suma> [moneda]\n"
            "/setdeadline YYYY-MM-DD HH:MM\n"
            "/setchat — setează grupul curent\n"
            "/listmatches — lista meciurilor\n"
            "/announcetomorrow — trimite meciurile de mâine\n"
            "/postleaderboard — postează clasamentul\n"
            "/reset_game CONFIRM — șterge pronosticuri+rezultate (ține restul)\n"
            "/reset_all CONFIRM — resetare totală (de fabrică)"
        )
    await update.message.reply_text(txt)


# ---------- Campioana ---------- #
def champ_keyboard(page=0, per=12):
    with db() as c:
        teams = c.execute(
            "SELECT team_id, name FROM teams ORDER BY group_label, name"
        ).fetchall()
    total = len(teams)
    chunk = teams[page * per: page * per + per]
    rows, row = [], []
    for t in chunk:
        row.append(InlineKeyboardButton(t["name"], callback_data=f"champ:set:{t['team_id']}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"champ:page:{page - 1}"))
    if (page + 1) * per < total:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"champ:page:{page + 1}"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(rows)


async def cmd_campioana(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update)
    if not await guard(update):
        return
    if not picks_open():
        await update.message.reply_text("⛔️ Perioada de pronostic pentru campioană s-a încheiat.")
        return
    with db() as c:
        cur = c.execute(
            "SELECT team_name FROM champion_picks WHERE user_id=?",
            (update.effective_user.id,),
        ).fetchone()
    extra = f"\n\nAlegerea ta actuală: {cur['team_name']}" if cur else ""
    await update.message.reply_text(
        "Alege *campioana* turneului:" + extra,
        parse_mode="Markdown",
        reply_markup=champ_keyboard(0),
    )


async def cb_champ(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    parts = q.data.split(":")
    if parts[1] == "page":
        await q.edit_message_reply_markup(reply_markup=champ_keyboard(int(parts[2])))
        return
    if not is_approved(q.from_user.id):
        await q.edit_message_text("⏳ Aștepți aprobarea organizatorului.")
        return
    if not picks_open():
        await q.edit_message_text("⛔️ Perioada de pronostic s-a încheiat.")
        return
    team_id = int(parts[2])
    with db() as c:
        t = c.execute("SELECT name FROM teams WHERE team_id=?", (team_id,)).fetchone()
        if not t:
            return
        c.execute(
            "INSERT INTO champion_picks(user_id, team_name) VALUES(?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET team_name=excluded.team_name",
            (q.from_user.id, t["name"]),
        )
    await q.edit_message_text(f"✅ Campioana ta: *{t['name']}*\n(o poți schimba cu /campioana)", parse_mode="Markdown")


# ---------- Grupe ---------- #
def groups_menu_keyboard(user_id):
    with db() as c:
        picked = dict(
            c.execute(
                "SELECT group_label, team_name FROM group_picks WHERE user_id=?",
                (user_id,),
            ).fetchall()
        )
    rows, row = [], []
    for label in sorted(TEAMS.keys()):
        mark = "✅" if label in picked else "⚪️"
        row.append(InlineKeyboardButton(f"{mark} Grupa {label}", callback_data=f"grp:open:{label}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows), picked


async def cmd_grupe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update)
    if not await guard(update):
        return
    if not picks_open():
        await update.message.reply_text("⛔️ Perioada de pronostic pentru grupe s-a încheiat.")
        return
    kb, picked = groups_menu_keyboard(update.effective_user.id)
    await update.message.reply_text(
        f"Alege câștigătoarea fiecărei grupe ({len(picked)}/{len(TEAMS)} completate):",
        reply_markup=kb,
    )


async def cb_grp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    parts = q.data.split(":")
    action = parts[1]

    if not is_approved(q.from_user.id):
        await q.edit_message_text("⏳ Aștepți aprobarea organizatorului.")
        return

    if action == "menu":
        kb, picked = groups_menu_keyboard(q.from_user.id)
        await q.edit_message_text(
            f"Alege câștigătoarea fiecărei grupe ({len(picked)}/{len(TEAMS)} completate):",
            reply_markup=kb,
        )
        return

    if action == "open":
        label = parts[2]
        with db() as c:
            teams = c.execute(
                "SELECT team_id, name FROM teams WHERE group_label=? ORDER BY name",
                (label,),
            ).fetchall()
            cur = c.execute(
                "SELECT team_name FROM group_picks WHERE user_id=? AND group_label=?",
                (q.from_user.id, label),
            ).fetchone()
        rows = []
        for t in teams:
            mark = "✅ " if cur and cur["team_name"] == t["name"] else ""
            rows.append([InlineKeyboardButton(mark + t["name"], callback_data=f"grp:set:{label}:{t['team_id']}")])
        rows.append([InlineKeyboardButton("⬅️ Înapoi la grupe", callback_data="grp:menu")])
        await q.edit_message_text(
            f"Grupa {label} — alege câștigătoarea:",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    if action == "set":
        if not picks_open():
            await q.edit_message_text("⛔️ Perioada de pronostic s-a încheiat.")
            return
        label, team_id = parts[2], int(parts[3])
        with db() as c:
            t = c.execute("SELECT name FROM teams WHERE team_id=?", (team_id,)).fetchone()
            c.execute(
                "INSERT INTO group_picks(user_id, group_label, team_name) VALUES(?,?,?) "
                "ON CONFLICT(user_id, group_label) DO UPDATE SET team_name=excluded.team_name",
                (q.from_user.id, label, t["name"]),
            )
        kb, picked = groups_menu_keyboard(q.from_user.id)
        await q.edit_message_text(
            f"✅ Grupa {label}: {t['name']}\n\nContinuă ({len(picked)}/{len(TEAMS)} completate):",
            reply_markup=kb,
        )


# ---------- Meciuri / pronostic ---------- #
def match_kb(match, current_pick=None):
    home, away, stage = match["home"], match["away"], match["stage"]
    mid = match["match_id"]

    def lbl(text, pick):
        return ("✅ " if current_pick == pick else "") + text

    buttons = [InlineKeyboardButton(lbl("🏠 " + home, "HOME"), callback_data=f"m:{mid}:HOME")]
    if is_group_stage(stage):
        buttons.append(InlineKeyboardButton(lbl("🤝 Egal", "DRAW"), callback_data=f"m:{mid}:DRAW"))
    buttons.append(InlineKeyboardButton(lbl("✈️ " + away, "AWAY"), callback_data=f"m:{mid}:AWAY"))
    return InlineKeyboardMarkup([buttons])


def match_text(match):
    t = match["kickoff"].split(" ")[1] if " " in match["kickoff"] else match["kickoff"]
    return f"🕐 {t} — {match['home']} vs {match['away']}\n({match['stage']})"


async def send_match_prompt(bot, chat_id, match, user_id):
    with db() as c:
        p = c.execute(
            "SELECT pick FROM match_predictions WHERE user_id=? AND match_id=?",
            (user_id, match["match_id"]),
        ).fetchone()
    current = p["pick"] if p else None
    await bot.send_message(
        chat_id=chat_id,
        text=match_text(match),
        reply_markup=match_kb(match, current),
    )


async def _show_day(update, context, date_str, eticheta):
    matches = matches_on(date_str)
    if not matches:
        await update.message.reply_text(f"Nu sunt meciuri {eticheta}. 🌙")
        return
    await update.message.reply_text(f"📅 *Meciurile {eticheta}* — pronostichează:", parse_mode="Markdown")
    for m in matches:
        if match_open(m["kickoff"]):
            await send_match_prompt(context.bot, update.effective_chat.id, m, update.effective_user.id)
        else:
            await update.message.reply_text(match_text(m) + "\n⛔️ Început — pronosticul e închis.")


async def cmd_azi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update)
    if not await guard(update):
        return
    await _show_day(update, context, now_local().strftime("%Y-%m-%d"), "de azi")


async def cmd_maine(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update)
    if not await guard(update):
        return
    await _show_day(update, context, (now_local() + timedelta(days=1)).strftime("%Y-%m-%d"), "de mâine")


async def cb_match(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_approved(q.from_user.id):
        await q.answer("⏳ Aștepți aprobarea organizatorului.", show_alert=True)
        return
    _, mid, pick = q.data.split(":")
    mid = int(mid)
    with db() as c:
        m = c.execute("SELECT * FROM matches WHERE match_id=?", (mid,)).fetchone()
    if not m:
        await q.answer("Meci inexistent.")
        return
    if not match_open(m["kickoff"]):
        await q.answer("⛔️ Meciul a început, nu mai poți pronostica.", show_alert=True)
        return
    with db() as c:
        c.execute(
            "INSERT INTO match_predictions(user_id, match_id, pick, created_at) "
            "VALUES(?,?,?,?) "
            "ON CONFLICT(user_id, match_id) DO UPDATE SET pick=excluded.pick, created_at=excluded.created_at",
            (q.from_user.id, mid, pick, now_local().isoformat()),
        )
    await q.answer(f"Salvat: {pick_label(m['home'], m['away'], pick)} ✅")
    await q.edit_message_reply_markup(reply_markup=match_kb(m, pick))


async def cmd_pronosticurile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    uid = update.effective_user.id
    with db() as c:
        champ = c.execute("SELECT team_name FROM champion_picks WHERE user_id=?", (uid,)).fetchone()
        gpicks = c.execute(
            "SELECT group_label, team_name FROM group_picks WHERE user_id=? ORDER BY group_label",
            (uid,),
        ).fetchall()
        mpicks = c.execute(
            "SELECT m.home, m.away, mp.pick "
            "FROM match_predictions mp JOIN matches m ON m.match_id=mp.match_id "
            "WHERE mp.user_id=? ORDER BY m.kickoff",
            (uid,),
        ).fetchall()
    lines = ["📋 *Pronosticurile tale*\n"]
    lines.append(f"🏆 Campioană: {champ['team_name'] if champ else '—'}")
    lines.append("\n📊 Grupe:")
    lines += [f"  • Grupa {g['group_label']}: {g['team_name']}" for g in gpicks] or ["  —"]
    lines.append("\n⚽️ Meciuri:")
    if mpicks:
        lines += [f"  • {mp['home']} vs {mp['away']}: {pick_label(mp['home'], mp['away'], mp['pick'])}" for mp in mpicks]
    else:
        lines.append("  —")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_clasament(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    await update.message.reply_text(leaderboard_text())


async def cmd_premii(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    pot_per = float(get_config("pot_per_person") or 0)
    currency = get_config("currency") or "RON"
    with db() as c:
        n = c.execute("SELECT COUNT(*) AS n FROM users WHERE approved=1").fetchone()["n"]
    pot = n * pot_per
    txt = (
        "🎁 *Premii (din cotizație)*\n\n"
        f"Participanți aprobați: {n}\n"
        f"Cotizație/persoană: {pot_per:g} {currency}\n"
        f"Fond total: {pot:g} {currency}\n\n"
        f"🥇 Locul 1: {pot * 0.5:g} {currency} *+ tricou*\n"
        f"🥈 Locul 2: {pot * 0.3:g} {currency}\n"
        f"🥉 Locul 3: {pot * 0.2:g} {currency}"
    )
    await update.message.reply_text(txt, parse_mode="Markdown")


# ------------------------------------------------------------------ #
#  COMENZI ADMIN — aprobare
# ------------------------------------------------------------------ #
async def cmd_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    with db() as c:
        rows = c.execute(
            "SELECT user_id, full_name, username FROM users WHERE approved=0 ORDER BY joined_at"
        ).fetchall()
    if not rows:
        await update.message.reply_text("Nicio cerere în așteptare. ✅")
        return
    lines = ["⏳ În așteptare:\n"]
    for r in rows:
        lines.append(f"• {r['full_name']} (@{r['username'] or '—'}) — /approve {r['user_id']}")
    await update.message.reply_text("\n".join(lines))


async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if not context.args:
        await update.message.reply_text("Format: /approve <id> sau /approve @username (vezi /pending)")
        return
    arg = context.args[0]
    with db() as c:
        if arg.startswith("@"):
            row = c.execute("SELECT user_id, full_name FROM users WHERE username=?", (arg[1:],)).fetchone()
        elif arg.isdigit():
            row = c.execute("SELECT user_id, full_name FROM users WHERE user_id=?", (int(arg),)).fetchone()
        else:
            row = None
        if not row:
            await update.message.reply_text("Nu am găsit persoana. Roagă-l să dea /start, apoi vezi /pending.")
            return
        c.execute("UPDATE users SET approved=1 WHERE user_id=?", (row["user_id"],))
    await update.message.reply_text(f"✅ Aprobat: {row['full_name']}")
    try:
        await context.bot.send_message(
            chat_id=row["user_id"],
            text="✅ Ai fost aprobat! Acum poți pronostica:\n1️⃣ /campioana\n2️⃣ /grupe",
        )
    except Exception:
        pass


async def cmd_deny(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Format: /deny <id> (scoate persoana și pronosticurile ei)")
        return
    uid = int(context.args[0])
    with db() as c:
        for t in ("match_predictions", "group_picks", "champion_picks"):
            c.execute(f"DELETE FROM {t} WHERE user_id=?", (uid,))
        c.execute("DELETE FROM users WHERE user_id=?", (uid,))
    await update.message.reply_text("🚫 Persoana a fost scoasă din concurs.")


async def cmd_participanti(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    with db() as c:
        rows = c.execute(
            "SELECT full_name, username FROM users WHERE approved=1 ORDER BY full_name"
        ).fetchall()
    if not rows:
        await update.message.reply_text("Niciun participant aprobat încă.")
        return
    lines = [f"✅ Participanți aprobați ({len(rows)}):\n"]
    lines += [f"• {r['full_name']} (@{r['username'] or '—'})" for r in rows]
    await update.message.reply_text("\n".join(lines))


# ------------------------------------------------------------------ #
#  COMENZI ADMIN — concurs
# ------------------------------------------------------------------ #
async def cmd_addmatch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    parts = [p.strip() for p in " ".join(context.args).split("|")]
    if len(parts) < 3:
        await update.message.reply_text(
            "Format: /addmatch Echipa1 | Echipa2 | YYYY-MM-DD HH:MM | Faza\n"
            "Ex: /addmatch Mexic | SUA | 2026-06-11 19:00 | Grupa A"
        )
        return
    home, away, kickoff = parts[0], parts[1], parts[2]
    stage = parts[3] if len(parts) > 3 else "Grupe"
    try:
        datetime.strptime(kickoff, "%Y-%m-%d %H:%M")
    except ValueError:
        await update.message.reply_text("Dată invalidă. Folosește YYYY-MM-DD HH:MM")
        return
    with db() as c:
        cur = c.execute(
            "INSERT INTO matches(home, away, kickoff, stage) VALUES(?,?,?,?)",
            (home, away, kickoff, stage),
        )
        mid = cur.lastrowid
    await update.message.reply_text(f"✅ Meci #{mid}: {home} vs {away} ({kickoff}, {stage})")


async def cmd_setresult(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if len(context.args) != 2 or context.args[1].upper() not in ("HOME", "DRAW", "AWAY"):
        await update.message.reply_text("Format: /setresult <id_meci> <HOME|DRAW|AWAY>")
        return
    mid, res = int(context.args[0]), context.args[1].upper()
    with db() as c:
        m = c.execute("SELECT * FROM matches WHERE match_id=?", (mid,)).fetchone()
        if not m:
            await update.message.reply_text("Meci inexistent.")
            return
        c.execute("UPDATE matches SET result=? WHERE match_id=?", (res, mid))
    await update.message.reply_text(
        f"✅ Rezultat meci #{mid}: {pick_label(m['home'], m['away'], res)}. Punctajele s-au actualizat."
    )


async def cmd_setgroupwinner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Format: /setgroupwinner <Grupa> <Echipa>")
        return
    label = context.args[0].upper()
    team = " ".join(context.args[1:])
    with db() as c:
        c.execute(
            "INSERT INTO group_results(group_label, winner_team) VALUES(?,?) "
            "ON CONFLICT(group_label) DO UPDATE SET winner_team=excluded.winner_team",
            (label, team),
        )
    await update.message.reply_text(f"✅ Câștigătoarea grupei {label}: {team}")


async def cmd_setchampion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if not context.args:
        await update.message.reply_text("Format: /setchampion <Echipa>")
        return
    set_config("champion_winner", " ".join(context.args))
    await update.message.reply_text(f"✅ Campioana turneului: {' '.join(context.args)}. Punctaje actualizate.")


async def cmd_setpot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if not context.args:
        await update.message.reply_text("Format: /setpot <suma> [moneda]\nEx: /setpot 50 RON")
        return
    set_config("pot_per_person", context.args[0])
    if len(context.args) > 1:
        set_config("currency", context.args[1])
    await update.message.reply_text("✅ Cotizație setată. Vezi /premii")


async def cmd_setdeadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    raw = " ".join(context.args)
    try:
        datetime.strptime(raw, "%Y-%m-%d %H:%M")
    except ValueError:
        await update.message.reply_text("Format: /setdeadline YYYY-MM-DD HH:MM")
        return
    set_config("picks_deadline", raw)
    await update.message.reply_text(f"✅ Termen pronosticuri (campioană+grupe): {raw}")


async def cmd_setchat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    set_config("contest_chat", update.effective_chat.id)
    await update.message.reply_text("✅ Acest chat este acum grupul oficial al concursului.")


async def cmd_listmatches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    with db() as c:
        ms = c.execute("SELECT * FROM matches ORDER BY kickoff").fetchall()
    if not ms:
        await update.message.reply_text("Niciun meci adăugat.")
        return
    lines = [
        f"#{m['match_id']} {m['kickoff']} {m['home']} vs {m['away']} "
        f"[{m['stage']}] {('-> ' + m['result']) if m['result'] else ''}"
        for m in ms
    ]
    await update.message.reply_text("\n".join(lines))


async def cmd_announcetomorrow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    await job_daily_matches(context)
    await update.message.reply_text("✅ Trimis.")


async def cmd_postleaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    chat = get_config("contest_chat")
    if chat:
        await context.bot.send_message(chat_id=int(chat), text=leaderboard_text())
    else:
        await update.message.reply_text(leaderboard_text())


# ------------------------------------------------------------------ #
#  COMENZI ADMIN — resetare
# ------------------------------------------------------------------ #
async def cmd_reset_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if not (context.args and context.args[0] == "CONFIRM"):
        await update.message.reply_text(
            "⚠️ /reset_game șterge toate PRONOSTICURILE și REZULTATELE, dar păstrează "
            "jucătorii, meciurile și setările.\n\nCa să confirmi:\n/reset_game CONFIRM"
        )
        return
    with db() as c:
        for t in ("match_predictions", "group_picks", "champion_picks", "group_results"):
            c.execute(f"DELETE FROM {t}")
        c.execute("UPDATE matches SET result=NULL")
        c.execute("DELETE FROM config WHERE key='champion_winner'")
    await update.message.reply_text(
        "🧹 Gata. Pronosticurile și rezultatele au fost șterse.\n"
        "Meciurile, echipele, jucătorii și setările au rămas."
    )


async def cmd_reset_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if not (context.args and context.args[0] == "CONFIRM"):
        await update.message.reply_text(
            "⚠️ /reset_all șterge TOT: jucători, pronosticuri, meciuri, rezultate și setări, "
            "și reîncarcă echipele implicite (resetare de fabrică).\n\nCa să confirmi:\n/reset_all CONFIRM"
        )
        return
    with db() as c:
        for t in ("users", "match_predictions", "group_picks", "champion_picks",
                  "matches", "group_results", "config", "teams"):
            c.execute(f"DELETE FROM {t}")
    init_db()  # reîncarcă echipele implicite
    await update.message.reply_text(
        "🧹 Resetare totală făcută.\n"
        "Acum: setează din nou /setchat, /setpot, /setdeadline și (dacă e cazul) echipele reale. "
        "Participanții trebuie să dea din nou /start și să fie aprobați."
    )


# ------------------------------------------------------------------ #
#  JOB-URI PROGRAMATE
# ------------------------------------------------------------------ #
async def job_daily_matches(context: ContextTypes.DEFAULT_TYPE):
    tomorrow = (now_local() + timedelta(days=1)).strftime("%Y-%m-%d")
    matches = matches_on(tomorrow)
    if not matches:
        return
    header = "📅 *Meciurile de MÂINE* (pronostichează până la startul fiecăruia):\n\n" + \
        "\n\n".join(match_text(m) for m in matches)

    chat = get_config("contest_chat")
    if chat:
        try:
            await context.bot.send_message(chat_id=int(chat), text=header, parse_mode="Markdown")
        except Exception as e:
            log.warning("Nu am putut posta în grup: %s", e)

    with db() as c:
        users = c.execute("SELECT user_id FROM users WHERE approved=1").fetchall()
    for u in users:
        try:
            await context.bot.send_message(chat_id=u["user_id"], text="📅 Meciurile de mâine — pronostichează:")
            for m in matches:
                await send_match_prompt(context.bot, u["user_id"], m, u["user_id"])
        except Exception as e:
            log.info("Nu pot trimite către %s: %s", u["user_id"], e)


async def job_leaderboard(context: ContextTypes.DEFAULT_TYPE):
    text = "🌙 Clasamentul zilei\n\n" + leaderboard_text()
    chat = get_config("contest_chat")
    if chat:
        try:
            await context.bot.send_message(chat_id=int(chat), text=text)
            return
        except Exception as e:
            log.warning("Nu am putut posta clasamentul: %s", e)
    with db() as c:
        users = c.execute("SELECT user_id FROM users WHERE approved=1").fetchall()
    for u in users:
        try:
            await context.bot.send_message(chat_id=u["user_id"], text=text)
        except Exception:
            pass


# ------------------------------------------------------------------ #
#  PORNIRE
# ------------------------------------------------------------------ #
async def post_init(app: Application):
    init_db()
    await app.bot.set_my_commands(
        [
            BotCommand("start", "Înscriere și instrucțiuni"),
            BotCommand("campioana", "Alege campioana turneului"),
            BotCommand("grupe", "Alege câștigătoarele de grupă"),
            BotCommand("azi", "Meciurile de azi"),
            BotCommand("maine", "Meciurile de mâine"),
            BotCommand("pronosticurile", "Pronosticurile tale"),
            BotCommand("clasament", "Clasamentul"),
            BotCommand("premii", "Premiile"),
            BotCommand("ajutor", "Ajutor"),
        ]
    )
    app.job_queue.run_daily(job_daily_matches, time=dtime(DAILY_HOUR, 0, tzinfo=TZ))
    app.job_queue.run_daily(job_leaderboard, time=dtime(LB_HOUR, LB_MIN, tzinfo=TZ))
    log.info("Bot pornit. Meciuri de mâine la %02d:00, clasament la %02d:%02d.", DAILY_HOUR, LB_HOUR, LB_MIN)


def main():
    if not BOT_TOKEN:
        raise SystemExit("Lipsește BOT_TOKEN. Setează variabila de mediu BOT_TOKEN.")
    if not ADMIN_IDS:
        log.warning("ADMIN_IDS nu este setat — comenzile de admin nu vor funcționa.")

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # Participanți
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler(["ajutor", "help"], cmd_help))
    app.add_handler(CommandHandler("campioana", cmd_campioana))
    app.add_handler(CommandHandler("grupe", cmd_grupe))
    app.add_handler(CommandHandler("azi", cmd_azi))
    app.add_handler(CommandHandler("maine", cmd_maine))
    app.add_handler(CommandHandler("pronosticurile", cmd_pronosticurile))
    app.add_handler(CommandHandler("clasament", cmd_clasament))
    app.add_handler(CommandHandler("premii", cmd_premii))

    # Admin — aprobare
    app.add_handler(CommandHandler("pending", cmd_pending))
    app.add_handler(CommandHandler("approve", cmd_approve))
    app.add_handler(CommandHandler("deny", cmd_deny))
    app.add_handler(CommandHandler("participanti", cmd_participanti))

    # Admin — concurs
    app.add_handler(CommandHandler("addmatch", cmd_addmatch))
    app.add_handler(CommandHandler("setresult", cmd_setresult))
    app.add_handler(CommandHandler("setgroupwinner", cmd_setgroupwinner))
    app.add_handler(CommandHandler("setchampion", cmd_setchampion))
    app.add_handler(CommandHandler("setpot", cmd_setpot))
    app.add_handler(CommandHandler("setdeadline", cmd_setdeadline))
    app.add_handler(CommandHandler("setchat", cmd_setchat))
    app.add_handler(CommandHandler("listmatches", cmd_listmatches))
    app.add_handler(CommandHandler("announcetomorrow", cmd_announcetomorrow))
    app.add_handler(CommandHandler("postleaderboard", cmd_postleaderboard))

    # Admin — resetare
    app.add_handler(CommandHandler("reset_game", cmd_reset_game))
    app.add_handler(CommandHandler("reset_all", cmd_reset_all))

    # Butoane
    app.add_handler(CallbackQueryHandler(cb_champ, pattern=r"^champ:"))
    app.add_handler(CallbackQueryHandler(cb_grp, pattern=r"^grp:"))
    app.add_handler(CallbackQueryHandler(cb_match, pattern=r"^m:"))

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
