#!/usr/bin/env python3
"""
=====================================================================
  BOT TELEGRAM — CONCURS CUPA MONDIALĂ 2026  (versiune completă)
=====================================================================

PUNCTAJ:
  • Campioana turneului ....... 5 puncte  (o singură dată, BLOCATĂ)
  • Golgheterul turneului ..... 4 puncte  (o singură dată, BLOCAT)
  • Câștigătoarea unei grupe .. 2 puncte  (BLOCATĂ după alegere)
  • Meci ghicit (1 / X / 2) ... 3 puncte  (până la startul meciului)
  • Bonus manual (admin) ...... +/- oricâte puncte (corecturi)

FLUX GHIDAT (la /start, după aprobare):
  campioana -> toate grupele deschise (pe rând) -> golgheter -> gata
  Apoi primește zilnic meciurile și pariază 1/X/2.

LA START vin pre-încărcate: toate grupele DESCHISE + 10 meciuri de test
(5 azi + 5 mâine, cu datele zilei în care pornești botul).
=====================================================================
"""

import os
import logging
import sqlite3
import contextlib
import unicodedata
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, BotCommandScopeChat,
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
    MessageHandler, filters,
)

# ------------------------------------------------------------------ #
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if x}
TZ = ZoneInfo(os.environ.get("TZ", "Europe/Bucharest"))
DB_PATH = os.environ.get("DB_PATH", "contest.db")
DAILY_HOUR = int(os.environ.get("DAILY_MATCHES_HOUR", "9"))
LB_HOUR = int(os.environ.get("LEADERBOARD_HOUR", "23"))
LB_MIN = int(os.environ.get("LEADERBOARD_MINUTE", "30"))

POINTS_MATCH = 3
POINTS_GROUP = 2
POINTS_CHAMPION = 5
POINTS_SCORER = 4

logging.basicConfig(format="%(asctime)s — %(levelname)s — %(message)s", level=logging.INFO)
log = logging.getLogger("cupa-bot")

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
GROUP_LABELS = list(TEAMS.keys())
DEFAULT_OPEN_GROUPS = ",".join(GROUP_LABELS)  # TOATE deschise


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
    d = os.path.dirname(os.path.abspath(DB_PATH))
    os.makedirs(d, exist_ok=True)
    with db() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS users(
                user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT,
                approved INTEGER DEFAULT 0, step TEXT DEFAULT '', joined_at TEXT
            );
            CREATE TABLE IF NOT EXISTS teams(
                team_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, group_label TEXT
            );
            CREATE TABLE IF NOT EXISTS matches(
                match_id INTEGER PRIMARY KEY AUTOINCREMENT, home TEXT, away TEXT,
                kickoff TEXT, stage TEXT, result TEXT, score TEXT
            );
            CREATE TABLE IF NOT EXISTS match_predictions(
                user_id INTEGER, match_id INTEGER, pick TEXT, created_at TEXT,
                PRIMARY KEY(user_id, match_id)
            );
            CREATE TABLE IF NOT EXISTS group_picks(
                user_id INTEGER, group_label TEXT, team_name TEXT,
                PRIMARY KEY(user_id, group_label)
            );
            CREATE TABLE IF NOT EXISTS champion_picks(user_id INTEGER PRIMARY KEY, team_name TEXT);
            CREATE TABLE IF NOT EXISTS scorer_picks(user_id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE IF NOT EXISTS group_results(group_label TEXT PRIMARY KEY, winner_team TEXT);
            CREATE TABLE IF NOT EXISTS bonus_points(
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, points INTEGER,
                reason TEXT, created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS config(key TEXT PRIMARY KEY, value TEXT);
            """
        )
        ucols = [r["name"] for r in c.execute("PRAGMA table_info(users)").fetchall()]
        if "approved" not in ucols:
            c.execute("ALTER TABLE users ADD COLUMN approved INTEGER DEFAULT 0")
        if "step" not in ucols:
            c.execute("ALTER TABLE users ADD COLUMN step TEXT DEFAULT ''")
        mcols = [r["name"] for r in c.execute("PRAGMA table_info(matches)").fetchall()]
        if "score" not in mcols:
            c.execute("ALTER TABLE matches ADD COLUMN score TEXT")
        if not c.execute("SELECT 1 FROM teams LIMIT 1").fetchone():
            for label, names in TEAMS.items():
                for n in names:
                    c.execute("INSERT OR IGNORE INTO teams(name, group_label) VALUES(?,?)", (n, label))
        if c.execute("SELECT 1 FROM config WHERE key='open_groups'").fetchone() is None:
            c.execute("INSERT INTO config(key,value) VALUES('open_groups',?)", (DEFAULT_OPEN_GROUPS,))
        # --- seed o singură dată: toate grupele deschise + meciuri de test ---
        if c.execute("SELECT 1 FROM config WHERE key='matches_seeded'").fetchone() is None:
            c.execute("INSERT INTO config(key,value) VALUES('open_groups',?) "
                      "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (DEFAULT_OPEN_GROUPS,))
            today = datetime.now(TZ).date()
            tmrw = today + timedelta(days=1)
            t, tm = today.strftime("%Y-%m-%d"), tmrw.strftime("%Y-%m-%d")
            seed = [
                (f"{t} 20:45", "România", "Țara Galilor"),
                (f"{t} 20:45", "Portugalia", "Chile"),
                (f"{t} 21:30", "SUA", "Germania"),
                (f"{t} 22:00", "Elveția", "Australia"),
                (f"{t} 23:00", "Anglia", "Noua Zeelandă"),
                (f"{tm} 19:00", "Belgia", "Tunisia"),
                (f"{tm} 20:00", "Bolivia", "Scoția"),
                (f"{tm} 20:45", "Ungaria", "Finlanda"),
                (f"{tm} 21:00", "Canada", "Irlanda"),
                (f"{tm} 22:00", "Panama", "Bosnia și Herțegovina"),
            ]
            for kickoff, home, away in seed:
                c.execute("INSERT INTO matches(home,away,kickoff,stage) VALUES(?,?,?,?)", (home, away, kickoff, "Amical"))
            c.execute("INSERT INTO config(key,value) VALUES('matches_seeded','1')")
            log.info("Seed: %d meciuri + toate grupele deschise.", len(seed))


def get_config(key):
    with db() as c:
        r = c.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        return r["value"] if r else None


def set_config(key, value):
    with db() as c:
        c.execute("INSERT INTO config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def open_groups():
    v = get_config("open_groups") or DEFAULT_OPEN_GROUPS
    return [g.strip().upper() for g in v.split(",") if g.strip()]


# ------------------------------------------------------------------ #
def is_admin(update): return update.effective_user and update.effective_user.id in ADMIN_IDS
def now_local(): return datetime.now(TZ).replace(tzinfo=None)


def _is_int(s):
    try:
        int(s); return True
    except (ValueError, TypeError):
        return False


def normalize(s):
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return " ".join(s.split())


def is_approved(uid):
    if uid in ADMIN_IDS:
        return True
    with db() as c:
        r = c.execute("SELECT approved FROM users WHERE user_id=?", (uid,)).fetchone()
        return bool(r and r["approved"])


def picks_open():
    dl = get_config("picks_deadline")
    if not dl:
        return True
    try:
        return now_local() < datetime.strptime(dl, "%Y-%m-%d %H:%M")
    except ValueError:
        return True


def match_open(kickoff):
    try:
        return now_local() < datetime.strptime(kickoff, "%Y-%m-%d %H:%M")
    except ValueError:
        return True


def register_user(update):
    u = update.effective_user
    appr = 1 if u.id in ADMIN_IDS else 0
    with db() as c:
        c.execute(
            "INSERT INTO users(user_id, username, full_name, approved, step, joined_at) "
            "VALUES(?,?,?,?,'',?) "
            "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, full_name=excluded.full_name",
            (u.id, u.username or "", u.full_name, appr, now_local().isoformat()),
        )


def pick_label(home, away, pick):
    return {"HOME": home, "DRAW": "Egal", "AWAY": away}.get(pick, pick)


def team_name(tid):
    with db() as c:
        r = c.execute("SELECT name FROM teams WHERE team_id=?", (tid,)).fetchone()
        return r["name"] if r else None


def matches_on(date_str):
    with db() as c:
        return c.execute("SELECT * FROM matches WHERE kickoff LIKE ? ORDER BY kickoff", (date_str + "%",)).fetchall()


def get_step(uid):
    with db() as c:
        r = c.execute("SELECT step FROM users WHERE user_id=?", (uid,)).fetchone()
        return (r["step"] if r else "") or ""


def set_step(uid, step):
    with db() as c:
        c.execute("UPDATE users SET step=? WHERE user_id=?", (step, uid))


def has_champion(uid):
    with db() as c:
        return c.execute("SELECT 1 FROM champion_picks WHERE user_id=?", (uid,)).fetchone() is not None


def has_scorer(uid):
    with db() as c:
        return c.execute("SELECT 1 FROM scorer_picks WHERE user_id=?", (uid,)).fetchone() is not None


def has_group(uid, label):
    with db() as c:
        return c.execute("SELECT 1 FROM group_picks WHERE user_id=? AND group_label=?", (uid, label)).fetchone() is not None


def next_open_unpicked_group(uid):
    for g in open_groups():
        if not has_group(uid, g):
            return g
    return None


def onboarding_done(uid):
    return get_step(uid) == "done"


async def guard(update):
    if is_approved(update.effective_user.id):
        return True
    await update.message.reply_text("⏳ Ești în așteptare. Organizatorul te aprobă după ce achiți cotizația.\nApoi dă /start.")
    return False


async def notify_admins(context, text):
    for aid in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=aid, text=text)
        except Exception:
            pass


def find_users(arg):
    arg = (arg or "").strip()
    with db() as c:
        if arg.startswith("@"):
            return c.execute("SELECT * FROM users WHERE LOWER(username)=?", (arg[1:].lower(),)).fetchall()
        if arg.isdigit():
            return c.execute("SELECT * FROM users WHERE user_id=?", (int(arg),)).fetchall()
        like = f"%{arg.lower()}%"
        return c.execute("SELECT * FROM users WHERE LOWER(full_name) LIKE ? OR LOWER(username) LIKE ?", (like, like)).fetchall()


# ------------------------------------------------------------------ #
#  PUNCTAJ
# ------------------------------------------------------------------ #
def compute_scores():
    with db() as c:
        users = c.execute("SELECT user_id, full_name, username FROM users WHERE approved=1").fetchall()
        mres = dict(c.execute("SELECT match_id, result FROM matches WHERE result IS NOT NULL").fetchall())
        preds = c.execute("SELECT user_id, match_id, pick FROM match_predictions").fetchall()
        gres = dict(c.execute("SELECT group_label, winner_team FROM group_results").fetchall())
        gpicks = c.execute("SELECT user_id, group_label, team_name FROM group_picks").fetchall()
        cpicks = dict(c.execute("SELECT user_id, team_name FROM champion_picks").fetchall())
        spicks = dict(c.execute("SELECT user_id, name FROM scorer_picks").fetchall())
        bonus = dict(c.execute("SELECT user_id, COALESCE(SUM(points),0) FROM bonus_points GROUP BY user_id").fetchall())
    champ = get_config("champion_winner")
    scorer = get_config("scorer_winner")
    ids = {u["user_id"] for u in users}

    m = {i: 0 for i in ids}
    for p in preds:
        if p["user_id"] in ids and mres.get(p["match_id"]) == p["pick"]:
            m[p["user_id"]] += POINTS_MATCH
    g = {i: 0 for i in ids}
    for x in gpicks:
        if x["user_id"] in ids and gres.get(x["group_label"]) == x["team_name"]:
            g[x["user_id"]] += POINTS_GROUP
    ch = {i: 0 for i in ids}
    if champ:
        for uid, t in cpicks.items():
            if uid in ids and t == champ:
                ch[uid] = POINTS_CHAMPION
    sc = {i: 0 for i in ids}
    if scorer:
        for uid, nm in spicks.items():
            if uid in ids and normalize(nm) == normalize(scorer):
                sc[uid] = POINTS_SCORER

    rows = []
    for u in users:
        i = u["user_id"]
        b = int(bonus.get(i, 0))
        total = m[i] + g[i] + ch[i] + sc[i] + b
        rows.append((u, total, m[i], g[i], ch[i], sc[i], b))
    rows.sort(key=lambda x: x[1], reverse=True)
    return rows


def leaderboard_text():
    rows = compute_scores()
    if not rows:
        return "Încă nu există participanți aprobați."
    medals = {0: "🥇", 1: "🥈", 2: "🥉"}
    out = ["🏆 CLASAMENT\n"]
    for i, (u, total, m, g, ch, sc, b) in enumerate(rows):
        name = u["full_name"] or (u["username"] and "@" + u["username"]) or str(u["user_id"])
        pre = medals.get(i, f"{i + 1}.")
        extra = f"  (M:{m} G:{g} C:{ch} Gol:{sc}" + (f" B:{b}" if b else "") + ")"
        out.append(f"{pre} {name} — {total}p{extra}")
    out.append("\nM=meciuri · G=grupe · C=campioană · Gol=golgheter · B=bonus")
    return "\n".join(out)


# ------------------------------------------------------------------ #
#  TASTATURI / PROMPTURI
# ------------------------------------------------------------------ #
def champ_keyboard(page=0, per=12, prefix="champ"):
    with db() as c:
        teams = c.execute("SELECT team_id, name FROM teams ORDER BY group_label, name").fetchall()
    chunk = teams[page * per: page * per + per]
    rows, row = [], []
    for t in chunk:
        row.append(InlineKeyboardButton(t["name"], callback_data=f"{prefix}:set:{t['team_id']}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"{prefix}:page:{page - 1}"))
    if (page + 1) * per < len(teams):
        nav.append(InlineKeyboardButton("▶️", callback_data=f"{prefix}:page:{page + 1}"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(rows)


def group_team_keyboard(label, prefix="grp"):
    with db() as c:
        teams = c.execute("SELECT team_id, name FROM teams WHERE group_label=? ORDER BY name", (label,)).fetchall()
    return InlineKeyboardMarkup([[InlineKeyboardButton(t["name"], callback_data=f"{prefix}:set:{label}:{t['team_id']}")] for t in teams])


def disclaimer_text():
    closed = [g for g in GROUP_LABELS if g not in open_groups()]
    if not closed:
        return "✅ Toate grupele sunt deschise."
    return ("🔒 Grupele " + ", ".join(closed) +
            " se vor deschide mai târziu. Vei primi un mesaj atunci ca să le pronostichezi.")


def wizard_done_text(uid):
    with db() as c:
        champ = c.execute("SELECT team_name FROM champion_picks WHERE user_id=?", (uid,)).fetchone()
        scorer = c.execute("SELECT name FROM scorer_picks WHERE user_id=?", (uid,)).fetchone()
        gp = c.execute("SELECT group_label, team_name FROM group_picks WHERE user_id=? ORDER BY group_label", (uid,)).fetchall()
    lines = ["✅ Gata! Ai completat pronosticurile inițiale.\n",
             f"🏆 Campioană: {champ['team_name'] if champ else '—'}",
             f"⚽️ Golgheter: {scorer['name'] if scorer else '—'}"]
    for x in gp:
        lines.append(f"📊 Grupa {x['group_label']}: {x['team_name']}")
    lines.append("\nDe acum primești zilnic meciurile și pariezi 1/X/2. Mult succes! 🍀")
    return "\n".join(lines)


async def send_champion_prompt(bot, chat_id):
    await bot.send_message(chat_id, "1️⃣ Alege *CAMPIOANA* turneului (5 puncte).\n⚠️ Nu o mai poți schimba după ce alegi!",
                           parse_mode="Markdown", reply_markup=champ_keyboard(0))


async def send_group_prompt(bot, chat_id, label):
    n_open = len(open_groups())
    await bot.send_message(chat_id, f"Alege câștigătoarea *GRUPEI {label}* (2 puncte).\n⚠️ Se blochează după alegere.",
                           parse_mode="Markdown", reply_markup=group_team_keyboard(label))


async def send_scorer_prompt(bot, chat_id):
    await bot.send_message(chat_id, disclaimer_text() +
                           "\n\n⚽️ Acum scrie numele *GOLGHETERULUI* turneului (4 puncte).\n"
                           "Trimite-l ca mesaj normal (ex: Mbappé, Haaland, Kane).", parse_mode="Markdown")


async def start_or_resume_wizard(bot, uid):
    if not is_approved(uid):
        return
    if not has_champion(uid):
        set_step(uid, "champion"); await send_champion_prompt(bot, uid); return
    g = next_open_unpicked_group(uid)
    if g:
        set_step(uid, "groups"); await send_group_prompt(bot, uid, g); return
    if not has_scorer(uid):
        set_step(uid, "scorer"); await send_scorer_prompt(bot, uid); return
    set_step(uid, "done")
    await bot.send_message(uid, wizard_done_text(uid))


# ------------------------------------------------------------------ #
#  COMENZI PARTICIPANȚI
# ------------------------------------------------------------------ #
async def cmd_start(update, context):
    u = update.effective_user
    with db() as c:
        existing = c.execute("SELECT 1 FROM users WHERE user_id=?", (u.id,)).fetchone()
    register_user(update)
    if not is_approved(u.id):
        await update.message.reply_text(
            "👋 Salut! Te-ai înscris la concurs.\n\n⏳ Ești *în așteptare* — organizatorul te aprobă "
            "după ce achiți cotizația. Apoi începi automat pronosticurile.", parse_mode="Markdown")
        if not existing:
            await notify_admins(context, f"🆕 Cerere nouă: {u.full_name} (@{u.username or '—'})\nID: {u.id}\nAprobă cu /pending sau /approve {u.id}")
        return
    await update.message.reply_text("⚽️ *Bine ai venit!* Hai să-ți completăm pronosticurile, pas cu pas:", parse_mode="Markdown")
    await start_or_resume_wizard(context.bot, u.id)


async def cmd_help(update, context):
    txt = ("📋 Comenzi jucător:\n/start — (re)pornește pronosticurile\n/campioana — campioana ta\n"
           "/grupe — grupele tale\n/golgheter — golgheterul tău\n/azi — meciurile de azi\n"
           "/maine — meciurile de mâine\n/pronosticurile — tot ce ai pus\n/clasament — clasamentul\n/premii — premiile")
    if is_admin(update):
        txt += ("\n\n🔧 ADMIN — acces:\n/pending /approve /deny /participanti\n\n"
                "👁 Vezi:\n/allpicks — toți pe scurt\n/picks <nume> — un jucător complet\n"
                "/matchpicks <id> — cine ce a pus la un meci\n/statusazi — cine n-a pariat azi\n/clasament\n\n"
                "⚽️ Meciuri:\n/addmatch — adaugă meci\n/editmatch <id> ... — editează (nume/dată/fază)\n"
                "/delmatch <id> — șterge meci\n/listmatches — lista cu ID-uri\n/announcetomorrow — trimite meciurile de mâine\n\n"
                "🏁 Rezultate:\n/setresult — întâi câștigătorul, apoi scorul\n/score <id> 2-1 — doar scorul\n"
                "/setgroupwinner — câștigătoarea unei grupe\n/setchampion — campioana reală\n/setscorer <nume> — golgheterul real\n\n"
                "➕ Bonus:\n/bonus <puncte> <nume> — ex: /bonus 3 Ana (sau -2)\n/bonuses — lista bonusurilor\n/clearbonus <nume> — șterge bonusurile cuiva\n\n"
                "📊 Grupe:\n/opengroups A B C ... — ce grupe sunt deschise\n\n"
                "⚙️ Setări:\n/setpot /setdeadline /setchat /postleaderboard\n\n"
                "🧹 Reset (NU șterg jucătorii):\n/reset_game CONFIRM — șterge pronosticuri, ține meciuri+jucători\n"
                "/reset_all CONFIRM — șterge și meciurile, repornește ID de la #1")
    await update.message.reply_text(txt)


async def cmd_campioana(update, context):
    register_user(update)
    if not await guard(update):
        return
    if has_champion(update.effective_user.id):
        with db() as c:
            t = c.execute("SELECT team_name FROM champion_picks WHERE user_id=?", (update.effective_user.id,)).fetchone()
        await update.message.reply_text(f"🏆 Campioana ta: {t['team_name']}\n(blocată — nu se poate schimba)")
        return
    if not picks_open():
        await update.message.reply_text("⛔️ Termenul pentru campioană a trecut.")
        return
    await send_champion_prompt(context.bot, update.effective_chat.id)


def groups_menu(uid):
    with db() as c:
        picked = dict(c.execute("SELECT group_label, team_name FROM group_picks WHERE user_id=?", (uid,)).fetchall())
    opn = open_groups()
    lines = ["📊 *Grupele tale:*\n"]
    buttons = []
    for g in GROUP_LABELS:
        if g in picked:
            lines.append(f"✅ Grupa {g}: {picked[g]}")
        elif g in opn:
            lines.append(f"🔓 Grupa {g}: de ales")
            buttons.append([InlineKeyboardButton(f"Alege Grupa {g}", callback_data=f"grp:open:{g}")])
        else:
            lines.append(f"🔒 Grupa {g}: se deschide curând")
    return "\n".join(lines), (InlineKeyboardMarkup(buttons) if buttons else None)


async def cmd_grupe(update, context):
    register_user(update)
    if not await guard(update):
        return
    text, kb = groups_menu(update.effective_user.id)
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)


async def cmd_golgheter(update, context):
    register_user(update)
    if not await guard(update):
        return
    uid = update.effective_user.id
    if has_scorer(uid):
        with db() as c:
            s = c.execute("SELECT name FROM scorer_picks WHERE user_id=?", (uid,)).fetchone()
        await update.message.reply_text(f"⚽️ Golgheterul tău: {s['name']}\n(blocat — nu se poate schimba)")
        return
    if not picks_open():
        await update.message.reply_text("⛔️ Termenul a trecut.")
        return
    set_step(uid, "scorer")
    await update.message.reply_text("⚽️ Scrie numele golgheterului turneului (4 puncte) — trimite-l ca mesaj normal.")


async def _show_day(update, context, date_str, eticheta):
    uid = update.effective_user.id
    if not onboarding_done(uid):
        await update.message.reply_text("✋ Mai întâi completează campioana și grupele. Dă /start.")
        return
    ms = matches_on(date_str)
    if not ms:
        await update.message.reply_text(f"Nu sunt meciuri {eticheta}. 🌙")
        return
    await update.message.reply_text(f"📅 *Meciurile {eticheta}* — pariază:", parse_mode="Markdown")
    for m in ms:
        if match_open(m["kickoff"]):
            await send_match_prompt(context.bot, update.effective_chat.id, m, uid)
        else:
            await update.message.reply_text(match_text(m) + "\n⛔️ Început — închis.")


async def cmd_azi(update, context):
    register_user(update)
    if not await guard(update):
        return
    await _show_day(update, context, now_local().strftime("%Y-%m-%d"), "de azi")


async def cmd_maine(update, context):
    register_user(update)
    if not await guard(update):
        return
    await _show_day(update, context, (now_local() + timedelta(days=1)).strftime("%Y-%m-%d"), "de mâine")


async def cmd_pronosticurile(update, context):
    if not await guard(update):
        return
    await update.message.reply_text(picks_text(update.effective_user.id, mine=True))


def picks_text(uid, mine=False, title=None):
    with db() as c:
        champ = c.execute("SELECT team_name FROM champion_picks WHERE user_id=?", (uid,)).fetchone()
        scorer = c.execute("SELECT name FROM scorer_picks WHERE user_id=?", (uid,)).fetchone()
        gp = c.execute("SELECT group_label, team_name FROM group_picks WHERE user_id=? ORDER BY group_label", (uid,)).fetchall()
        mp = c.execute("SELECT m.home, m.away, mp.pick FROM match_predictions mp JOIN matches m ON m.match_id=mp.match_id WHERE mp.user_id=? ORDER BY m.kickoff", (uid,)).fetchall()
    head = title or ("📋 Pronosticurile tale" if mine else "📋 Pronosticuri")
    lines = [head + "\n", f"🏆 Campioană: {champ['team_name'] if champ else '—'}",
             f"⚽️ Golgheter: {scorer['name'] if scorer else '—'}", "\n📊 Grupe:"]
    lines += [f"  • {g['group_label']}: {g['team_name']}" for g in gp] or ["  —"]
    lines.append("\n⚽️ Meciuri:")
    if mp:
        lines += [f"  • {x['home']} vs {x['away']}: {pick_label(x['home'], x['away'], x['pick'])}" for x in mp]
    else:
        lines.append("  —")
    return "\n".join(lines)


async def cmd_clasament(update, context):
    if not await guard(update):
        return
    await update.message.reply_text(leaderboard_text())


async def cmd_premii(update, context):
    if not await guard(update):
        return
    pot = float(get_config("pot_per_person") or 0)
    cur = get_config("currency") or "RON"
    with db() as c:
        n = c.execute("SELECT COUNT(*) AS n FROM users WHERE approved=1").fetchone()["n"]
    total = n * pot
    await update.message.reply_text(
        f"🎁 *Premii (din cotizație)*\n\nParticipanți: {n}\nCotizație/persoană: {pot:g} {cur}\n"
        f"Fond total: {total:g} {cur}\n\n🥇 Loc 1: {total*0.5:g} {cur} *+ tricou*\n"
        f"🥈 Loc 2: {total*0.3:g} {cur}\n🥉 Loc 3: {total*0.2:g} {cur}", parse_mode="Markdown")


# ------------------------------------------------------------------ #
#  MECIURI — afișare/pariere
# ------------------------------------------------------------------ #
def match_kb(match, current=None):
    mid = match["match_id"]
    def lbl(t, p): return ("✅ " if current == p else "") + t
    btns = [
        InlineKeyboardButton(lbl("🏠 " + match["home"], "HOME"), callback_data=f"m:{mid}:HOME"),
        InlineKeyboardButton(lbl("🤝 Egal", "DRAW"), callback_data=f"m:{mid}:DRAW"),
        InlineKeyboardButton(lbl("✈️ " + match["away"], "AWAY"), callback_data=f"m:{mid}:AWAY"),
    ]
    return InlineKeyboardMarkup([btns])


def match_text(m):
    t = m["kickoff"].split(" ")[1] if " " in m["kickoff"] else m["kickoff"]
    return f"🕐 {t} — {m['home']} vs {m['away']}  ({m['stage']})"


def result_str(m):
    if not m["result"]:
        return ""
    s = pick_label(m["home"], m["away"], m["result"])
    sc = m["score"] if ("score" in m.keys() and m["score"]) else None
    return s + (f" ({sc})" if sc else "")


async def send_match_prompt(bot, chat_id, match, uid):
    with db() as c:
        p = c.execute("SELECT pick FROM match_predictions WHERE user_id=? AND match_id=?", (uid, match["match_id"])).fetchone()
    await bot.send_message(chat_id=chat_id, text=match_text(match), reply_markup=match_kb(match, p["pick"] if p else None))


# ------------------------------------------------------------------ #
#  CALLBACK-URI participanți
# ------------------------------------------------------------------ #
async def cb_champ(update, context):
    q = update.callback_query
    parts = q.data.split(":")
    uid = q.from_user.id
    if parts[1] == "page":
        await q.answer(); await q.edit_message_reply_markup(reply_markup=champ_keyboard(int(parts[2]))); return
    if not is_approved(uid):
        await q.answer("Aștepți aprobarea.", show_alert=True); return
    if has_champion(uid):
        await q.answer("Campioana e aleasă deja, nu se poate schimba.", show_alert=True); return
    if not picks_open():
        await q.answer("Termenul a trecut.", show_alert=True); return
    t = team_name(int(parts[2]))
    await q.answer()
    with db() as c:
        c.execute("INSERT OR IGNORE INTO champion_picks(user_id, team_name) VALUES(?,?)", (uid, t))
    await q.edit_message_text(f"✅ Campioana ta: {t}\n(blocată — nu se mai poate schimba)")
    if get_step(uid) in ("champion", ""):
        await start_or_resume_wizard(context.bot, uid)


async def cb_grp(update, context):
    q = update.callback_query
    parts = q.data.split(":")
    uid = q.from_user.id
    if parts[1] == "open":
        await q.answer()
        label = parts[2]
        if has_group(uid, label):
            await q.edit_message_text(f"✅ Ai ales deja la Grupa {label}."); return
        await q.edit_message_text(f"Alege câștigătoarea Grupei {label}:", reply_markup=group_team_keyboard(label)); return
    if parts[1] == "set":
        label, tid = parts[2], int(parts[3])
        if not is_approved(uid):
            await q.answer("Aștepți aprobarea.", show_alert=True); return
        if has_group(uid, label):
            await q.answer("Ai ales deja la grupa asta.", show_alert=True); return
        if label not in open_groups():
            await q.answer("Grupa nu e încă deschisă.", show_alert=True); return
        if not picks_open():
            await q.answer("Termenul a trecut.", show_alert=True); return
        t = team_name(tid)
        await q.answer()
        with db() as c:
            c.execute("INSERT OR IGNORE INTO group_picks(user_id, group_label, team_name) VALUES(?,?,?)", (uid, label, t))
        await q.edit_message_text(f"✅ Grupa {label}: {t} (blocată)")
        if get_step(uid) == "groups":
            await start_or_resume_wizard(context.bot, uid)
        else:
            text, kb = groups_menu(uid)
            await context.bot.send_message(uid, text, parse_mode="Markdown", reply_markup=kb)


async def cb_match(update, context):
    q = update.callback_query
    uid = q.from_user.id
    if not is_approved(uid):
        await q.answer("Aștepți aprobarea.", show_alert=True); return
    if not onboarding_done(uid):
        await q.answer("Întâi completează campioana și grupele (/start).", show_alert=True); return
    _, mid, pick = q.data.split(":")
    mid = int(mid)
    with db() as c:
        m = c.execute("SELECT * FROM matches WHERE match_id=?", (mid,)).fetchone()
    if not m:
        await q.answer("Meci inexistent."); return
    if not match_open(m["kickoff"]):
        await q.answer("⛔️ Meciul a început.", show_alert=True); return
    await q.answer(f"Salvat: {pick_label(m['home'], m['away'], pick)} ✅")
    with db() as c:
        c.execute("INSERT INTO match_predictions(user_id, match_id, pick, created_at) VALUES(?,?,?,?) "
                  "ON CONFLICT(user_id, match_id) DO UPDATE SET pick=excluded.pick, created_at=excluded.created_at",
                  (uid, mid, pick, now_local().isoformat()))
    await q.edit_message_reply_markup(reply_markup=match_kb(m, pick))


# ------------------------------------------------------------------ #
#  HANDLER TEXT (golgheter jucător + scor admin)
# ------------------------------------------------------------------ #
async def on_text(update, context):
    uid = update.effective_user.id
    # ADMIN — captură scor după ce a ales câștigătorul
    if uid in ADMIN_IDS:
        mid = context.user_data.get("await_score_for")
        if mid:
            score = (update.message.text or "").strip()
            context.user_data.pop("await_score_for", None)
            with db() as c:
                m = c.execute("SELECT 1 FROM matches WHERE match_id=?", (mid,)).fetchone()
                if m:
                    c.execute("UPDATE matches SET score=? WHERE match_id=?", (score, mid))
            await update.message.reply_text(f"✅ Scor salvat la meciul #{mid}: {score}")
            return
    # JUCĂTOR — captură golgheter
    if not is_approved(uid):
        return
    if get_step(uid) == "scorer" and not has_scorer(uid):
        name = (update.message.text or "").strip()
        if not name:
            return
        with db() as c:
            c.execute("INSERT OR IGNORE INTO scorer_picks(user_id, name) VALUES(?,?)", (uid, name))
        await update.message.reply_text(f"✅ Golgheter: {name} (blocat)")
        await start_or_resume_wizard(context.bot, uid)


# ------------------------------------------------------------------ #
#  ADMIN — acces (cu butoane)
# ------------------------------------------------------------------ #
async def cmd_pending(update, context):
    if not is_admin(update):
        return
    with db() as c:
        rows = c.execute("SELECT user_id, full_name, username FROM users WHERE approved=0 ORDER BY joined_at").fetchall()
    if not rows:
        await update.message.reply_text("Nicio cerere în așteptare. ✅"); return
    await update.message.reply_text(f"⏳ {len(rows)} în așteptare — apasă pentru a aproba:",
                                    reply_markup=InlineKeyboardMarkup(
                                        [[InlineKeyboardButton(f"✅ {r['full_name']} (@{r['username'] or '—'})", callback_data=f"appr:{r['user_id']}")] for r in rows]))


async def cb_approve(update, context):
    q = update.callback_query
    if q.from_user.id not in ADMIN_IDS:
        await q.answer(); return
    uid = int(q.data.split(":")[1])
    with db() as c:
        u = c.execute("SELECT full_name FROM users WHERE user_id=?", (uid,)).fetchone()
        c.execute("UPDATE users SET approved=1 WHERE user_id=?", (uid,))
    await q.answer("Aprobat ✅")
    await q.edit_message_text(f"✅ Aprobat: {u['full_name'] if u else uid}")
    try:
        await context.bot.send_message(uid, "✅ Ai fost aprobat! Hai să completăm pronosticurile:")
        await start_or_resume_wizard(context.bot, uid)
    except Exception:
        pass


async def cmd_approve(update, context):
    if not is_admin(update):
        return
    if not context.args:
        await update.message.reply_text("Format: /approve <nume sau @user sau id>\n(sau folosește /pending cu butoane)")
        return
    rows = find_users(" ".join(context.args))
    if not rows:
        await update.message.reply_text("Nu am găsit pe nimeni. Roagă-l să dea /start, apoi /pending.")
        return
    if len(rows) > 1:
        await update.message.reply_text("Sunt mai mulți — alege:", reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(f"✅ {r['full_name']} (@{r['username'] or '—'})", callback_data=f"appr:{r['user_id']}")] for r in rows]))
        return
    u = rows[0]
    with db() as c:
        c.execute("UPDATE users SET approved=1 WHERE user_id=?", (u["user_id"],))
    await update.message.reply_text(f"✅ Aprobat: {u['full_name']}")
    try:
        await context.bot.send_message(u["user_id"], "✅ Ai fost aprobat! Hai să completăm pronosticurile:")
        await start_or_resume_wizard(context.bot, u["user_id"])
    except Exception:
        pass


async def cmd_deny(update, context):
    if not is_admin(update):
        return
    if not context.args:
        await update.message.reply_text("Format: /deny <nume sau @user sau id>")
        return
    rows = find_users(" ".join(context.args))
    if len(rows) != 1:
        await update.message.reply_text("Specifică mai exact (am găsit 0 sau mai mulți)." if rows else "Nu am găsit.")
        return
    uid = rows[0]["user_id"]
    with db() as c:
        for t in ("match_predictions", "group_picks", "champion_picks", "scorer_picks", "bonus_points"):
            c.execute(f"DELETE FROM {t} WHERE user_id=?", (uid,))
        c.execute("DELETE FROM users WHERE user_id=?", (uid,))
    await update.message.reply_text("🚫 Scos din concurs.")


async def cmd_participanti(update, context):
    if not is_admin(update):
        return
    with db() as c:
        rows = c.execute("SELECT user_id, full_name, step FROM users WHERE approved=1 ORDER BY full_name").fetchall()
    if not rows:
        await update.message.reply_text("Niciun participant aprobat."); return
    await update.message.reply_text(
        f"✅ {len(rows)} participanți (apasă pentru pronosticuri):",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(f"{'✅' if r['step']=='done' else '⏳'} {r['full_name']}", callback_data=f"view:{r['user_id']}")] for r in rows]))


async def cb_view(update, context):
    q = update.callback_query
    if q.from_user.id not in ADMIN_IDS:
        await q.answer(); return
    await q.answer()
    uid = int(q.data.split(":")[1])
    with db() as c:
        u = c.execute("SELECT full_name FROM users WHERE user_id=?", (uid,)).fetchone()
    await context.bot.send_message(q.from_user.id, picks_text(uid, title=f"📋 {u['full_name'] if u else uid}"))


# ------------------------------------------------------------------ #
#  ADMIN — vizualizare
# ------------------------------------------------------------------ #
async def cmd_allpicks(update, context):
    if not is_admin(update):
        return
    with db() as c:
        users = c.execute("SELECT user_id, full_name, step FROM users WHERE approved=1 ORDER BY full_name").fetchall()
        champs = dict(c.execute("SELECT user_id, team_name FROM champion_picks").fetchall())
        scorers = dict(c.execute("SELECT user_id, name FROM scorer_picks").fetchall())
        gc = dict(c.execute("SELECT user_id, COUNT(*) FROM group_picks GROUP BY user_id").fetchall())
        mc = dict(c.execute("SELECT user_id, COUNT(*) FROM match_predictions GROUP BY user_id").fetchall())
    if not users:
        await update.message.reply_text("Niciun participant aprobat."); return
    out = ["📋 Toți participanții\n"]
    for u in users:
        i = u["user_id"]
        flag = "✅" if u["step"] == "done" else "⏳"
        out.append(f"{flag} {u['full_name']} — 🏆 {champs.get(i,'—')} | ⚽️ {scorers.get(i,'—')} | Gr {gc.get(i,0)}/{len(open_groups())} | Meci {mc.get(i,0)}")
    out.append("\nDetalii: /picks <nume>")
    await update.message.reply_text("\n".join(out))


async def cmd_picks(update, context):
    if not is_admin(update):
        return
    if not context.args:
        await update.message.reply_text("Format: /picks <nume sau @user sau id>\n(sau /participanti cu butoane)")
        return
    rows = find_users(" ".join(context.args))
    if not rows:
        await update.message.reply_text("Nu am găsit pe nimeni.")
        return
    if len(rows) > 1:
        await update.message.reply_text("Sunt mai mulți — alege:", reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(r["full_name"], callback_data=f"view:{r['user_id']}")] for r in rows]))
        return
    u = rows[0]
    await update.message.reply_text(picks_text(u["user_id"], title=f"📋 {u['full_name']}"))


async def cmd_matchpicks(update, context):
    if not is_admin(update):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Format: /matchpicks <id_meci> (vezi /listmatches)")
        return
    mid = int(context.args[0])
    with db() as c:
        m = c.execute("SELECT * FROM matches WHERE match_id=?", (mid,)).fetchone()
        if not m:
            await update.message.reply_text("Meci inexistent."); return
        rows = c.execute("SELECT u.full_name, mp.pick FROM match_predictions mp JOIN users u ON u.user_id=mp.user_id WHERE mp.match_id=? AND u.approved=1 ORDER BY u.full_name", (mid,)).fetchall()
    out = [f"⚽️ {m['home']} vs {m['away']} ({m['stage']}):\n"]
    if not rows:
        out.append("Nimeni n-a pariat încă.")
    else:
        for r in rows:
            out.append(f"• {r['full_name']}: {pick_label(m['home'], m['away'], r['pick'])}")
    if m["result"]:
        out.append(f"\nRezultat: {result_str(m)}")
    await update.message.reply_text("\n".join(out))


async def cmd_statusazi(update, context):
    if not is_admin(update):
        return
    ms = matches_on(now_local().strftime("%Y-%m-%d"))
    if not ms:
        await update.message.reply_text("Nu sunt meciuri azi.")
        return
    ids = [m["match_id"] for m in ms]
    with db() as c:
        users = c.execute("SELECT user_id, full_name FROM users WHERE approved=1 ORDER BY full_name").fetchall()
        qmarks = ",".join("?" * len(ids))
        preds = c.execute(f"SELECT user_id, match_id FROM match_predictions WHERE match_id IN ({qmarks})", ids).fetchall()
    done = {}
    for p in preds:
        done.setdefault(p["user_id"], set()).add(p["match_id"])
    out = [f"📊 Status pariuri AZI ({len(ms)} meciuri):\n"]
    nimeni = []
    for u in users:
        s = done.get(u["user_id"], set())
        if len(s) == len(ids):
            out.append(f"✅ {u['full_name']} — {len(s)}/{len(ids)}")
        elif len(s) == 0:
            nimeni.append(u["full_name"])
        else:
            missing = [f"{m['home']}-{m['away']}" for m in ms if m["match_id"] not in s]
            out.append(f"🟡 {u['full_name']} — {len(s)}/{len(ids)} (lipsesc: {', '.join(missing)})")
    if nimeni:
        out.append("\n❌ N-au pus NIMIC azi: " + ", ".join(nimeni))
    await update.message.reply_text("\n".join(out))


# ------------------------------------------------------------------ #
#  ADMIN — bonus
# ------------------------------------------------------------------ #
async def cmd_bonus(update, context):
    if not is_admin(update):
        return
    if not context.args or not _is_int(context.args[0]):
        await update.message.reply_text("Format: /bonus <puncte> <nume> [| motiv]\nEx: /bonus 3 Ana  ·  /bonus -2 Bogdan | greșeală meci")
        return
    pts = int(context.args[0])
    rest = " ".join(context.args[1:]).strip()
    reason = ""
    if "|" in rest:
        rest, reason = [x.strip() for x in rest.split("|", 1)]
    if not rest:
        await update.message.reply_text("Spune și pentru cine: /bonus 3 <nume>")
        return
    rows = find_users(rest)
    if not rows:
        await update.message.reply_text("Nu am găsit pe nimeni.")
        return
    if len(rows) > 1:
        await update.message.reply_text("Sunt mai mulți cu numele ăsta — fii mai exact:\n" + "\n".join(f"• {r['full_name']} (id {r['user_id']})" for r in rows))
        return
    u = rows[0]
    with db() as c:
        c.execute("INSERT INTO bonus_points(user_id, points, reason, created_at) VALUES(?,?,?,?)",
                  (u["user_id"], pts, reason, now_local().isoformat()))
    await update.message.reply_text(f"✅ {pts:+d} puncte bonus pentru {u['full_name']}" + (f"\nMotiv: {reason}" if reason else ""))


async def cmd_bonuses(update, context):
    if not is_admin(update):
        return
    with db() as c:
        rows = c.execute("SELECT b.points, b.reason, u.full_name FROM bonus_points b JOIN users u ON u.user_id=b.user_id ORDER BY u.full_name, b.id").fetchall()
    if not rows:
        await update.message.reply_text("Niciun bonus încă.")
        return
    out = ["➕ Bonusuri:\n"]
    for r in rows:
        out.append(f"• {r['full_name']}: {r['points']:+d}" + (f" — {r['reason']}" if r['reason'] else ""))
    await update.message.reply_text("\n".join(out))


async def cmd_clearbonus(update, context):
    if not is_admin(update):
        return
    if not context.args:
        await update.message.reply_text("Format: /clearbonus <nume>")
        return
    rows = find_users(" ".join(context.args))
    if len(rows) != 1:
        await update.message.reply_text("Specifică mai exact." if rows else "Nu am găsit.")
        return
    u = rows[0]
    with db() as c:
        c.execute("DELETE FROM bonus_points WHERE user_id=?", (u["user_id"],))
    await update.message.reply_text(f"🧹 Bonusurile lui {u['full_name']} au fost șterse.")


# ------------------------------------------------------------------ #
#  ADMIN — grupe / meciuri
# ------------------------------------------------------------------ #
async def cmd_opengroups(update, context):
    if not is_admin(update):
        return
    if not context.args:
        await update.message.reply_text(f"Acum deschise: {', '.join(open_groups())}\nFormat: /opengroups A B C ...")
        return
    groups = [a.strip().upper() for a in context.args if a.strip().upper() in GROUP_LABELS]
    if not groups:
        await update.message.reply_text("Litere de grupă invalide.")
        return
    set_config("open_groups", ",".join(groups))
    await update.message.reply_text(f"✅ Grupe deschise acum: {', '.join(groups)}")
    with db() as c:
        users = c.execute("SELECT user_id FROM users WHERE approved=1").fetchall()
    for u in users:
        try:
            await context.bot.send_message(u["user_id"], "🔓 S-au actualizat grupele deschise! Pune-ți pronosticurile cu /grupe.")
        except Exception:
            pass


async def cmd_addmatch(update, context):
    if not is_admin(update):
        return
    parts = [p.strip() for p in " ".join(context.args).split("|")]
    if len(parts) < 3:
        await update.message.reply_text("Format: /addmatch Echipa1 | Echipa2 | YYYY-MM-DD HH:MM | Faza\n(pentru knockout poți scrie nume gen: Câștigătoare A | Locul 2 B | ... | Optimi)")
        return
    home, away, kickoff = parts[0], parts[1], parts[2]
    stage = parts[3] if len(parts) > 3 else "Meci"
    try:
        datetime.strptime(kickoff, "%Y-%m-%d %H:%M")
    except ValueError:
        await update.message.reply_text("Dată invalidă. Folosește YYYY-MM-DD HH:MM"); return
    with db() as c:
        cur = c.execute("INSERT INTO matches(home, away, kickoff, stage) VALUES(?,?,?,?)", (home, away, kickoff, stage))
        mid = cur.lastrowid
    await update.message.reply_text(f"✅ Meci #{mid}: {home} vs {away} ({kickoff}, {stage})")


async def cmd_editmatch(update, context):
    if not is_admin(update):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Format: /editmatch <id> Echipa1 | Echipa2 [| YYYY-MM-DD HH:MM | Faza]\nEx (după grupe): /editmatch 13 Brazilia | Franța | 2026-07-01 21:00 | Optimi")
        return
    mid = int(context.args[0])
    parts = [p.strip() for p in " ".join(context.args[1:]).split("|")]
    if len(parts) < 2:
        await update.message.reply_text("Trebuie cel puțin: /editmatch <id> Echipa1 | Echipa2")
        return
    fields = {"home": parts[0], "away": parts[1]}
    if len(parts) >= 3 and parts[2]:
        try:
            datetime.strptime(parts[2], "%Y-%m-%d %H:%M")
        except ValueError:
            await update.message.reply_text("Dată invalidă. Folosește YYYY-MM-DD HH:MM"); return
        fields["kickoff"] = parts[2]
    if len(parts) >= 4 and parts[3]:
        fields["stage"] = parts[3]
    with db() as c:
        if not c.execute("SELECT 1 FROM matches WHERE match_id=?", (mid,)).fetchone():
            await update.message.reply_text("Meci inexistent. Vezi /listmatches."); return
        setclause = ", ".join(f"{k}=?" for k in fields)
        c.execute(f"UPDATE matches SET {setclause} WHERE match_id=?", (*fields.values(), mid))
        m = c.execute("SELECT * FROM matches WHERE match_id=?", (mid,)).fetchone()
    await update.message.reply_text(f"✏️ Meci #{mid} actualizat: {m['home']} vs {m['away']} ({m['kickoff']}, {m['stage']})")


async def cmd_delmatch(update, context):
    if not is_admin(update):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Format: /delmatch <id>")
        return
    mid = int(context.args[0])
    with db() as c:
        m = c.execute("SELECT * FROM matches WHERE match_id=?", (mid,)).fetchone()
        if not m:
            await update.message.reply_text("Meci inexistent."); return
        c.execute("DELETE FROM match_predictions WHERE match_id=?", (mid,))
        c.execute("DELETE FROM matches WHERE match_id=?", (mid,))
    await update.message.reply_text(f"🗑 Șters meciul #{mid} ({m['home']} vs {m['away']}).")


async def cmd_listmatches(update, context):
    if not is_admin(update):
        return
    with db() as c:
        ms = c.execute("SELECT * FROM matches ORDER BY kickoff").fetchall()
    if not ms:
        await update.message.reply_text("Niciun meci."); return
    out = []
    for m in ms:
        r = result_str(m)
        out.append(f"#{m['match_id']} · {m['kickoff']} · {m['home']} vs {m['away']} [{m['stage']}]" + (f" → {r}" if r else ""))
    await update.message.reply_text("\n".join(out))


# ------------------------------------------------------------------ #
#  ADMIN — rezultate (întâi câștigătorul, apoi scorul)
# ------------------------------------------------------------------ #
async def cmd_setresult(update, context):
    if not is_admin(update):
        return
    if len(context.args) == 2 and context.args[1].upper() in ("HOME", "DRAW", "AWAY") and context.args[0].isdigit():
        await _apply_result(update.message, int(context.args[0]), context.args[1].upper())
        return
    with db() as c:
        ms = c.execute("SELECT * FROM matches ORDER BY kickoff DESC LIMIT 25").fetchall()
    if not ms:
        await update.message.reply_text("Niciun meci. Adaugă cu /addmatch."); return
    await update.message.reply_text("Alege meciul pentru rezultat:", reply_markup=InlineKeyboardMarkup(
        [[InlineKeyboardButton(f"#{m['match_id']} {m['home']} vs {m['away']}{(' ✓'+m['result']) if m['result'] else ''}", callback_data=f"sres:m:{m['match_id']}")] for m in ms]))


async def _apply_result(message, mid, res):
    with db() as c:
        m = c.execute("SELECT * FROM matches WHERE match_id=?", (mid,)).fetchone()
        if not m:
            await message.reply_text("Meci inexistent."); return
        c.execute("UPDATE matches SET result=? WHERE match_id=?", (res, mid))
    await message.reply_text(f"✅ Câștigător #{mid}: {pick_label(m['home'], m['away'], res)}. (Scor opțional: /score {mid} 2-1)")


async def cb_setres(update, context):
    q = update.callback_query
    if q.from_user.id not in ADMIN_IDS:
        await q.answer(); return
    await q.answer()
    parts = q.data.split(":")
    if parts[1] == "m":
        mid = int(parts[2])
        with db() as c:
            m = c.execute("SELECT * FROM matches WHERE match_id=?", (mid,)).fetchone()
        await q.edit_message_text(f"Cine câștigă? {m['home']} vs {m['away']}", reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(f"🏠 {m['home']}", callback_data=f"sres:r:{mid}:HOME"),
            InlineKeyboardButton("🤝 Egal", callback_data=f"sres:r:{mid}:DRAW"),
            InlineKeyboardButton(f"✈️ {m['away']}", callback_data=f"sres:r:{mid}:AWAY"),
        ]]))
    elif parts[1] == "r":
        mid, res = int(parts[2]), parts[3]
        with db() as c:
            m = c.execute("SELECT * FROM matches WHERE match_id=?", (mid,)).fetchone()
            c.execute("UPDATE matches SET result=? WHERE match_id=?", (res, mid))
        context.user_data["await_score_for"] = mid
        await q.edit_message_text(
            f"✅ Câștigător #{mid}: {pick_label(m['home'], m['away'], res)}.\n\n"
            f"📝 Acum scrie SCORUL ca mesaj (ex: 2-1).\nDacă nu vrei scor, ignoră — punctajul e deja actualizat.")


async def cmd_score(update, context):
    if not is_admin(update):
        return
    if len(context.args) < 2 or not context.args[0].isdigit():
        await update.message.reply_text("Format: /score <id> 2-1")
        return
    mid = int(context.args[0])
    score = " ".join(context.args[1:])
    with db() as c:
        m = c.execute("SELECT 1 FROM matches WHERE match_id=?", (mid,)).fetchone()
        if not m:
            await update.message.reply_text("Meci inexistent."); return
        c.execute("UPDATE matches SET score=? WHERE match_id=?", (score, mid))
    await update.message.reply_text(f"✅ Scor #{mid}: {score}")


async def cmd_setgroupwinner(update, context):
    if not is_admin(update):
        return
    if len(context.args) >= 2:
        label = context.args[0].upper()
        team = " ".join(context.args[1:])
        with db() as c:
            c.execute("INSERT INTO group_results(group_label, winner_team) VALUES(?,?) ON CONFLICT(group_label) DO UPDATE SET winner_team=excluded.winner_team", (label, team))
        await update.message.reply_text(f"✅ Câștigătoarea Grupei {label}: {team}")
        return
    await update.message.reply_text("Alege grupa:", reply_markup=InlineKeyboardMarkup(
        [[InlineKeyboardButton(f"Grupa {g}", callback_data=f"sgrp:g:{g}") for g in GROUP_LABELS[i:i+3]] for i in range(0, len(GROUP_LABELS), 3)]))


async def cb_setgrp(update, context):
    q = update.callback_query
    if q.from_user.id not in ADMIN_IDS:
        await q.answer(); return
    await q.answer()
    parts = q.data.split(":")
    if parts[1] == "g":
        label = parts[2]
        await q.edit_message_text(f"Câștigătoarea Grupei {label}:", reply_markup=group_team_keyboard(label, prefix="sgrp_t"))


async def cb_setgrp_team(update, context):
    q = update.callback_query
    if q.from_user.id not in ADMIN_IDS:
        await q.answer(); return
    await q.answer()
    parts = q.data.split(":")
    label, tid = parts[2], int(parts[3])
    t = team_name(tid)
    with db() as c:
        c.execute("INSERT INTO group_results(group_label, winner_team) VALUES(?,?) ON CONFLICT(group_label) DO UPDATE SET winner_team=excluded.winner_team", (label, t))
    await q.edit_message_text(f"✅ Câștigătoarea Grupei {label}: {t}. Punctaje actualizate.")


async def cmd_setchampion(update, context):
    if not is_admin(update):
        return
    if context.args:
        set_config("champion_winner", " ".join(context.args))
        await update.message.reply_text(f"✅ Campioana reală: {' '.join(context.args)}. Punctaje actualizate.")
        return
    await update.message.reply_text("Alege campioana reală:", reply_markup=champ_keyboard(0, prefix="schamp"))


async def cb_setchamp(update, context):
    q = update.callback_query
    if q.from_user.id not in ADMIN_IDS:
        await q.answer(); return
    parts = q.data.split(":")
    if parts[1] == "page":
        await q.answer(); await q.edit_message_reply_markup(reply_markup=champ_keyboard(int(parts[2]), prefix="schamp")); return
    await q.answer()
    t = team_name(int(parts[2]))
    set_config("champion_winner", t)
    await q.edit_message_text(f"✅ Campioana reală: {t}. Punctaje actualizate.")


async def cmd_setscorer(update, context):
    if not is_admin(update):
        return
    if not context.args:
        await update.message.reply_text("Format: /setscorer <nume golgheter>\nEx: /setscorer Mbappé")
        return
    set_config("scorer_winner", " ".join(context.args))
    await update.message.reply_text(f"✅ Golgheterul real: {' '.join(context.args)}. Punctaje actualizate.")


async def cmd_setpot(update, context):
    if not is_admin(update):
        return
    if not context.args:
        await update.message.reply_text("Format: /setpot <suma> [moneda]"); return
    set_config("pot_per_person", context.args[0])
    if len(context.args) > 1:
        set_config("currency", context.args[1])
    await update.message.reply_text("✅ Cotizație setată. Vezi /premii")


async def cmd_setdeadline(update, context):
    if not is_admin(update):
        return
    raw = " ".join(context.args)
    try:
        datetime.strptime(raw, "%Y-%m-%d %H:%M")
    except ValueError:
        await update.message.reply_text("Format: /setdeadline YYYY-MM-DD HH:MM"); return
    set_config("picks_deadline", raw)
    await update.message.reply_text(f"✅ Termen campioană/grupe/golgheter: {raw}")


async def cmd_setchat(update, context):
    if not is_admin(update):
        return
    set_config("contest_chat", update.effective_chat.id)
    await update.message.reply_text("✅ Acest chat e grupul oficial.")


async def cmd_announcetomorrow(update, context):
    if not is_admin(update):
        return
    await job_daily_matches(context)
    await update.message.reply_text("✅ Trimis (dacă există meciuri mâine).")


async def cmd_postleaderboard(update, context):
    if not is_admin(update):
        return
    chat = get_config("contest_chat")
    if chat:
        await context.bot.send_message(chat_id=int(chat), text=leaderboard_text())
    else:
        await update.message.reply_text(leaderboard_text())


# ------------------------------------------------------------------ #
#  ADMIN — resetare (NU șterge jucătorii)
# ------------------------------------------------------------------ #
async def cmd_reset_game(update, context):
    if not is_admin(update):
        return
    if not (context.args and context.args[0] == "CONFIRM"):
        await update.message.reply_text("⚠️ /reset_game șterge PRONOSTICURILE, REZULTATELE și BONUSURILE, dar păstrează jucătorii, meciurile și setările.\nConfirmă: /reset_game CONFIRM")
        return
    with db() as c:
        for t in ("match_predictions", "group_picks", "champion_picks", "scorer_picks", "group_results", "bonus_points"):
            c.execute(f"DELETE FROM {t}")
        c.execute("UPDATE matches SET result=NULL, score=NULL")
        c.execute("DELETE FROM config WHERE key IN ('champion_winner','scorer_winner')")
        c.execute("UPDATE users SET step=''")
    await update.message.reply_text("🧹 Gata. Pronosticuri, rezultate și bonusuri șterse. Jucătorii AU RĂMAS (dau /start ca să reia).")


async def cmd_reset_all(update, context):
    if not is_admin(update):
        return
    if not (context.args and context.args[0] == "CONFIRM"):
        await update.message.reply_text("⚠️ /reset_all șterge pronosticurile, rezultatele, bonusurile ȘI meciurile (numerotarea repornește de la #1). PĂSTREAZĂ jucătorii, echipele și setările.\nConfirmă: /reset_all CONFIRM")
        return
    with db() as c:
        for t in ("match_predictions", "group_picks", "champion_picks", "scorer_picks", "group_results", "bonus_points", "matches"):
            c.execute(f"DELETE FROM {t}")
        try:
            c.execute("DELETE FROM sqlite_sequence WHERE name='matches'")
        except sqlite3.OperationalError:
            pass
        c.execute("DELETE FROM config WHERE key IN ('champion_winner','scorer_winner')")
        c.execute("UPDATE users SET step=''")
    await update.message.reply_text("🧹 Resetat. Meciurile repornesc de la #1. Jucătorii AU RĂMAS — roagă-i să dea /start.")


# ------------------------------------------------------------------ #
#  JOB-URI
# ------------------------------------------------------------------ #
async def job_daily_matches(context):
    tomorrow = (now_local() + timedelta(days=1)).strftime("%Y-%m-%d")
    ms = matches_on(tomorrow)
    if not ms:
        return
    header = "📅 *Meciurile de MÂINE* (pariază până la startul fiecăruia):\n\n" + "\n\n".join(match_text(m) for m in ms)
    chat = get_config("contest_chat")
    if chat:
        try:
            await context.bot.send_message(chat_id=int(chat), text=header, parse_mode="Markdown")
        except Exception as e:
            log.warning("grup: %s", e)
    with db() as c:
        users = c.execute("SELECT user_id, step FROM users WHERE approved=1").fetchall()
    for u in users:
        try:
            if u["step"] == "done":
                await context.bot.send_message(u["user_id"], "📅 Meciurile de mâine — pariază:")
                for m in ms:
                    await send_match_prompt(context.bot, u["user_id"], m, u["user_id"])
            else:
                await context.bot.send_message(u["user_id"], "📅 Mâine sunt meciuri! Dar întâi termină pronosticurile inițiale: /start")
        except Exception as e:
            log.info("dm %s: %s", u["user_id"], e)


async def job_leaderboard(context):
    text = "🌙 Clasamentul zilei\n\n" + leaderboard_text()
    chat = get_config("contest_chat")
    if chat:
        try:
            await context.bot.send_message(chat_id=int(chat), text=text); return
        except Exception as e:
            log.warning("grup lb: %s", e)
    with db() as c:
        users = c.execute("SELECT user_id FROM users WHERE approved=1").fetchall()
    for u in users:
        try:
            await context.bot.send_message(u["user_id"], text)
        except Exception:
            pass


# ------------------------------------------------------------------ #
#  PORNIRE
# ------------------------------------------------------------------ #
async def post_init(app):
    init_db()
    participant_cmds = [
        BotCommand("start", "Pornește/reia pronosticurile"),
        BotCommand("campioana", "Campioana ta"),
        BotCommand("grupe", "Grupele tale"),
        BotCommand("golgheter", "Golgheterul tău"),
        BotCommand("azi", "Meciurile de azi"),
        BotCommand("maine", "Meciurile de mâine"),
        BotCommand("pronosticurile", "Pronosticurile tale"),
        BotCommand("clasament", "Clasamentul"),
        BotCommand("premii", "Premiile"),
        BotCommand("ajutor", "Ajutor"),
    ]
    admin_cmds = participant_cmds + [
        BotCommand("pending", "Cereri în așteptare"),
        BotCommand("approve", "Aprobă jucător"),
        BotCommand("deny", "Scoate jucător"),
        BotCommand("participanti", "Lista jucătorilor"),
        BotCommand("allpicks", "Toate pronosticurile"),
        BotCommand("picks", "Pronosticurile unui jucător"),
        BotCommand("matchpicks", "Pariuri la un meci"),
        BotCommand("statusazi", "Cine n-a pariat azi"),
        BotCommand("addmatch", "Adaugă meci"),
        BotCommand("editmatch", "Editează meci"),
        BotCommand("delmatch", "Șterge meci"),
        BotCommand("listmatches", "Lista meciurilor"),
        BotCommand("setresult", "Rezultat (câștigător + scor)"),
        BotCommand("score", "Scor la un meci"),
        BotCommand("setgroupwinner", "Câștigătoarea unei grupe"),
        BotCommand("setchampion", "Campioana reală"),
        BotCommand("setscorer", "Golgheterul real"),
        BotCommand("bonus", "Puncte bonus"),
        BotCommand("bonuses", "Lista bonusurilor"),
        BotCommand("clearbonus", "Șterge bonusuri"),
        BotCommand("opengroups", "Deschide grupe"),
        BotCommand("setpot", "Cotizația"),
        BotCommand("setdeadline", "Termen pronosticuri"),
        BotCommand("setchat", "Setează grupul oficial"),
        BotCommand("announcetomorrow", "Trimite meciurile de mâine"),
        BotCommand("postleaderboard", "Postează clasamentul"),
        BotCommand("reset_game", "Reset pronosticuri"),
        BotCommand("reset_all", "Reset complet meciuri"),
    ]
    await app.bot.set_my_commands(participant_cmds)
    for aid in ADMIN_IDS:
        try:
            await app.bot.set_my_commands(admin_cmds, scope=BotCommandScopeChat(chat_id=aid))
        except Exception as e:
            log.info("scope admin %s: %s", aid, e)
    app.job_queue.run_daily(job_daily_matches, time=dtime(DAILY_HOUR, 0, tzinfo=TZ))
    app.job_queue.run_daily(job_leaderboard, time=dtime(LB_HOUR, LB_MIN, tzinfo=TZ))
    log.info("Bot pornit. Meciuri mâine la %02d:00, clasament la %02d:%02d.", DAILY_HOUR, LB_HOUR, LB_MIN)


def main():
    if not BOT_TOKEN:
        raise SystemExit("Lipsește BOT_TOKEN.")
    if not ADMIN_IDS:
        log.warning("ADMIN_IDS nu e setat.")
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler(["ajutor", "help"], cmd_help))
    app.add_handler(CommandHandler("campioana", cmd_campioana))
    app.add_handler(CommandHandler("grupe", cmd_grupe))
    app.add_handler(CommandHandler("golgheter", cmd_golgheter))
    app.add_handler(CommandHandler("azi", cmd_azi))
    app.add_handler(CommandHandler("maine", cmd_maine))
    app.add_handler(CommandHandler("pronosticurile", cmd_pronosticurile))
    app.add_handler(CommandHandler("clasament", cmd_clasament))
    app.add_handler(CommandHandler("premii", cmd_premii))

    app.add_handler(CommandHandler("pending", cmd_pending))
    app.add_handler(CommandHandler("approve", cmd_approve))
    app.add_handler(CommandHandler("deny", cmd_deny))
    app.add_handler(CommandHandler("participanti", cmd_participanti))
    app.add_handler(CommandHandler("allpicks", cmd_allpicks))
    app.add_handler(CommandHandler("picks", cmd_picks))
    app.add_handler(CommandHandler("matchpicks", cmd_matchpicks))
    app.add_handler(CommandHandler("statusazi", cmd_statusazi))
    app.add_handler(CommandHandler("bonus", cmd_bonus))
    app.add_handler(CommandHandler("bonuses", cmd_bonuses))
    app.add_handler(CommandHandler("clearbonus", cmd_clearbonus))
    app.add_handler(CommandHandler("opengroups", cmd_opengroups))
    app.add_handler(CommandHandler("addmatch", cmd_addmatch))
    app.add_handler(CommandHandler("editmatch", cmd_editmatch))
    app.add_handler(CommandHandler("delmatch", cmd_delmatch))
    app.add_handler(CommandHandler("listmatches", cmd_listmatches))
    app.add_handler(CommandHandler("setresult", cmd_setresult))
    app.add_handler(CommandHandler("score", cmd_score))
    app.add_handler(CommandHandler("setgroupwinner", cmd_setgroupwinner))
    app.add_handler(CommandHandler("setchampion", cmd_setchampion))
    app.add_handler(CommandHandler("setscorer", cmd_setscorer))
    app.add_handler(CommandHandler("setpot", cmd_setpot))
    app.add_handler(CommandHandler("setdeadline", cmd_setdeadline))
    app.add_handler(CommandHandler("setchat", cmd_setchat))
    app.add_handler(CommandHandler("announcetomorrow", cmd_announcetomorrow))
    app.add_handler(CommandHandler("postleaderboard", cmd_postleaderboard))
    app.add_handler(CommandHandler("reset_game", cmd_reset_game))
    app.add_handler(CommandHandler("reset_all", cmd_reset_all))

    app.add_handler(CallbackQueryHandler(cb_champ, pattern=r"^champ:"))
    app.add_handler(CallbackQueryHandler(cb_grp, pattern=r"^grp:"))
    app.add_handler(CallbackQueryHandler(cb_match, pattern=r"^m:"))
    app.add_handler(CallbackQueryHandler(cb_approve, pattern=r"^appr:"))
    app.add_handler(CallbackQueryHandler(cb_view, pattern=r"^view:"))
    app.add_handler(CallbackQueryHandler(cb_setres, pattern=r"^sres:"))
    app.add_handler(CallbackQueryHandler(cb_setgrp, pattern=r"^sgrp:"))
    app.add_handler(CallbackQueryHandler(cb_setgrp_team, pattern=r"^sgrp_t:"))
    app.add_handler(CallbackQueryHandler(cb_setchamp, pattern=r"^schamp:"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
