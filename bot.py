# -*- coding: utf-8 -*-
"""
================================================================================
  CONCURS CUPA MONDIALĂ 2026 — BOT TELEGRAM
================================================================================

PUNCTAJ:
  • Campioana mondială ......... 5p
  • Câștigătoarea unei grupe ... 2p
  • Rezultat meci (1 X 2) ...... 3p
  • Scor exact ................. +2p (peste cele 3 de la 1X2)
  • Golgheterul turneului ...... 4p

--------------------------------------------------------------------------------
  CUM ÎL PORNEȘTI (o singură dată)
--------------------------------------------------------------------------------
  1. Instalează Python 3.10+ și librăria:
         pip install "python-telegram-bot[job-queue]==21.6"

  2. Creează botul la @BotFather pe Telegram și ia TOKEN-ul.

  3. Află ID-ul tău de Telegram: pornește botul, scrie-i /id în privat.
     Pune numărul la ADMIN_IDS mai jos.

  4. Bagă TOKEN-ul la BOT_TOKEN mai jos.

  5. Pornește botul:
         python worldcup_bot.py

  6. Adaugă botul în grupul tău, dă-i drept de admin (ca să vadă membrii noi),
     apoi scrie /setgrup CHIAR ÎN GRUP. De acolo postează automat la 12:00.

--------------------------------------------------------------------------------
  CUM SE JOACĂ (participanții)
--------------------------------------------------------------------------------
  - Intră în grup -> botul le spune să-ți scrie în privat -> tu îi aprobi.
  - După aprobare aleg, în ordine (NU se mai pot schimba):
        1) Campioana   2) Golgheterul   3) Câștig. Grupa A   4) Câștig. Grupa B
  - Restul grupelor le deschizi tu mai târziu cu /deschide_grupa C, D, ...
  - Pentru meciuri: /azi și /maine. Pronosticul se poate schimba până începe meciul.

--------------------------------------------------------------------------------
  COMENZI ADMIN (scrie-le în privat botului)
--------------------------------------------------------------------------------
  APROBARE
    /aproba <id>            aprobă un participant (sau folosește butoanele)
    /resping <id>           respinge / scoate un participant
    /useri                  lista participanților cu ID și status

  REZULTATE (de aici dai punctele)
    /scor <id_meci> 2-1     pune rezultatul unui meci (merge și 2:1 sau 2 1)
    /anuleaza_scor <id>     șterge rezultatul unui meci
    /grupa A Mexic          cine a câștigat grupa A (pentru cele 2p)
    /campioana Spania       campioana mondială (pentru cele 5p)
    /golgheter Mbappe       golgheterul turneului (4p). 'ALTUL' dacă nu e în listă

  MECIURI
    /lista                  toate meciurile cu ID, oră, rezultat (referință rapidă)
    /echipe <id> Brazilia - Spania   pune echipele la un meci din faza eliminatorie
    /ora <id> 2026-07-09 23:00       schimbă ora unui meci
    /amical Italia | Brazilia | 2026-06-06 21:00   adaugă rapid un amical
    /adauga_meci grupe | A | Mexic | Cehia | 2026-06-11 22:00 | Mexico   (avansat)
    /sterge_meci <id>       șterge un meci

  ALTELE
    /deschide_grupa C       deschide o nouă grupă la vot pentru toți
    /bonus <id sau @user> 3 motiv   adaugă (sau scade, cu -3) puncte manual
    /premii_set <text>      setează textul de la /premii
    /anunt <text>           trimite un mesaj tuturor participanților

  RESETARE (NU scoate oamenii din concurs)
    /reset_pronosticuri     șterge toate pronosticurile pe meciuri ale tuturor
    /reset_rezultate        șterge toate rezultatele (meciuri + campioană/grupe...)
    /reset_tot              șterge TOT (pronosticuri + rezultate + bonus), DAR
                            păstrează participanții (nu mai intră din nou în grup)

================================================================================
"""

import logging
import os
import sqlite3
import threading
import unicodedata
from datetime import datetime, timedelta, time as dt_time
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ==============================================================================
#  >>>>>>>>>>>>>>>>>>>>>>  MODIFICĂ AICI  <<<<<<<<<<<<<<<<<<<<<<<<
# ==============================================================================

# Pe RAILWAY pune astea ca "Variables" (NU le scrie în cod — altfel token-ul ajunge pe GitHub):
#   BOT_TOKEN        = token-ul de la @BotFather
#   ADMIN_IDS        = ID-ul tău (mai multe, separate prin virgulă: 111,222)
#   RAILWAY_RUN_UID  = 0     (OBLIGATORIU ca să meargă Volume-ul, altfel datele tot se șterg)
# Dacă atașezi un Volume, baza de date se salvează automat pe el și persistă între deploy-uri.

BOT_TOKEN = os.environ.get("BOT_TOKEN", "PUNE_TOKENUL_AICI")

ADMIN_IDS = set()
for _x in os.environ.get("ADMIN_IDS", "123456789").replace(" ", "").split(","):
    try:
        ADMIN_IDS.add(int(_x))
    except ValueError:
        pass

TIMEZONE = os.environ.get("TIMEZONE", "Europe/Bucharest")
DAILY_HOUR = int(os.environ.get("DAILY_HOUR", "12"))      # ora postării zilnice
DAILY_MINUTE = int(os.environ.get("DAILY_MINUTE", "0"))

# Baza de date: dacă există un Volume Railway, o punem AUTOMAT pe el (date persistente).
_vol = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
DB_PATH = os.environ.get("DB_PATH") or (
    f"{_vol.rstrip('/')}/worldcup.db" if _vol else "worldcup.db")

# ==============================================================================

TZ = ZoneInfo(TIMEZONE)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO
)
log = logging.getLogger("wc-bot")


# ==============================================================================
#  DATELE TURNEULUI
# ==============================================================================

GROUPS = {
    "A": ["Mexic", "Africa de Sud", "Coreea de Sud", "Cehia"],
    "B": ["Canada", "Bosnia", "Qatar", "Elveția"],
    "C": ["Brazilia", "Maroc", "Haiti", "Scoția"],
    "D": ["SUA", "Paraguay", "Australia", "Turcia"],
    "E": ["Germania", "Curaçao", "Coasta de Fildeș", "Ecuador"],
    "F": ["Olanda", "Japonia", "Suedia", "Tunisia"],
    "G": ["Belgia", "Egipt", "Iran", "Noua Zeelandă"],
    "H": ["Spania", "Capul Verde", "Arabia Saudită", "Uruguay"],
    "I": ["Franța", "Senegal", "Irak", "Norvegia"],
    "J": ["Argentina", "Algeria", "Austria", "Iordania"],
    "K": ["Portugalia", "DR Congo", "Uzbekistan", "Columbia"],
    "L": ["Anglia", "Croația", "Ghana", "Panama"],
}
ALL_TEAMS = sorted({t for teams in GROUPS.values() for t in teams})

# Golgheteri: (nume, cotă). Cei cu cotă <= 40 apar ca butoane rapide; restul prin căutare.
TOPSCORERS = [
    ("Kylian Mbappe", 7), ("Harry Kane", 8), ("Lionel Messi", 13),
    ("Erling Haaland", 15), ("Lamine Yamal", 15),
    ("Cristiano Ronaldo", 20), ("Mikel Oyarzabal", 20), ("Ousmane Dembele", 20),
    ("Lautaro Martinez", 25), ("Vinicius Jr.", 25), ("Kai Havertz", 30),
    ("Bukayo Saka", 35), ("Mikel Merino", 35), ("Nick Woltemade", 35),
    ("Raphinha", 35), ("Romelu Lukaku", 35),
    ("Bruno Fernandes", 40), ("Cody Gakpo", 40), ("Florian Wirtz", 40),
    ("Jude Bellingham", 40), ("Julian Alvarez", 40), ("Michael Olise", 40),
    ("Neymar Jr", 40),
    ("Dani Olmo", 50), ("Desire Doue", 50), ("Ferran Torres", 50),
    ("Goncalo Ramos", 50), ("Jean-Philippe Mateta", 50), ("Leandro Trossard", 50),
    ("Luis Diaz", 50), ("Marcus Rashford", 50), ("Memphis Depay", 50),
    ("Mohamed Salah", 50), ("Morgan Rogers", 50), ("Nico Williams", 50),
    ("Serge Gnabry", 50),
    ("Igor Thiago", 70),
    ("Alexander Sorloth", 75), ("Anthony Gordon", 75), ("Bradley Barcola", 75),
    ("Christian Pulisic", 75), ("Darwin Nunez", 75), ("Donyell Malen", 75),
    ("Eberechi Eze", 75),
    ("Deniz Undav", 80), ("Endrick", 80), ("Jeremy Doku", 80), ("Jhon Duran", 80),
    ("Kevin de Bruyne", 80), ("Kingsley Coman", 80), ("Leroy Sane", 80),
    ("Lois Openda", 80), ("Pedro Neto", 80), ("Rafael Leao", 80),
    ("Randal Muani", 80), ("Sadio Mane", 80), ("Viktor Gyokeres", 80),
    ("Armando Gonzalez", 100), ("Edin Dzeko", 100), ("Folarin Balogun", 100),
    ("Gabriel Martinelli", 100), ("Haji Wright", 100), ("James Rodriguez", 100),
    ("Jonathan David", 100), ("Kerem Akturkoglu", 100), ("Matheus Cunha", 100),
    ("Nicolas Jackson", 100), ("Noa Lang", 100), ("Omar Marmoush", 100),
    ("Pedri", 100), ("Promise David", 100), ("Raul Gimenez", 100),
    ("Ricardo Pepi", 100), ("Xavi Simons", 100),
    ("Arda Guler", 125), ("Ayase Ueda", 125), ("Baris Yilmaz", 125),
    ("Breel Embolo", 125), ("Ermedin Demirovic", 125), ("Heung-Min Son", 125),
    ("Kenan Yildiz", 125),
    ("Alphonso Davies", 150), ("Andrej Kramaric", 150), ("Anthony Elanga", 150),
    ("Antonio Nusa", 150), ("Ayoub El Kaabi", 150), ("Brian Rodriguez", 150),
    ("Cyle Larin", 150), ("Daizen Maeda", 150), ("Enzo Fernandez", 150),
    ("Granit Xhaka", 150), ("Hamza Igamane", 150), ("Hirving Lozano", 150),
    ("Jhon Arias", 150), ("Jorgen Larsen", 150), ("Kang-In Lee", 150),
    ("Orbelin Pineda", 150), ("Oscar Bobb", 150), ("Patrik Schick", 150),
    ("Riyad Mahrez", 150), ("Scott McTominay", 150), ("Tomas Chory", 150),
    ("Youssef En Nesyri", 150),
    ("Cedric Bakambu", 200), ("Chris Wood", 200), ("Facundo Pellistri", 200),
    ("Giovanni Reyna", 200), ("Hee-Chan Hwang", 200), ("Ismaila Sarr", 200),
    ("Martin Odegaard", 200), ("Nikola Vlasic", 200), ("Pavel Sulc", 200),
    ("Takumi Minamino", 200),
    ("Che Adams", 250), ("Declan Rice", 250), ("Saleh Al Shehri", 250),
    ("Salem Al Dawsari", 250), ("Yoane Wissa", 250),
    ("Daichi Kamada", 350), ("Julio Enciso", 350), ("Lyndon Dykes", 350),
    ("Ryan Christie", 350),
    ("Brenden Aaronson", 500), ("Eldor Shomurodov", 500), ("John McGinn", 500),
    ("Lyle Foster", 500), ("Martin Boyle", 500), ("Nestory Irankunda", 500),
    ("Ben Waine", 750),
]
TS_NAMES = [n for n, _ in TOPSCORERS]
TS_FAVORITES = [(n, c) for n, c in TOPSCORERS if c <= 40]

# Meciuri inițiale: (stage, grupa, gazda, oaspete, "YYYY-MM-DD HH:MM", loc, teams_known, nota)
# teams_known = 0 înseamnă că echipele sunt necunoscute (faza eliminatorie) -> le pui tu cu /echipe
SEED_MATCHES = [
    # ---------------- AMICALE TEST (azi) — ca să verifici că merge tot ----------------
    ("amical", None, "România", "Rep. Moldova", "2026-06-06 20:00", "Amical", 1, "AMICAL TEST"),
    ("amical", None, "Italia", "Argentina", "2026-06-06 23:00", "Amical", 1, "AMICAL TEST"),
    ("amical", None, "Spania", "Portugalia", "2026-06-07 21:00", "Amical", 1, "AMICAL TEST"),

    # ---------------- ETAPA 1 ----------------
    ("grupe", "A", "Mexic", "Africa de Sud", "2026-06-11 22:00", "Mexico City", 1, "Etapa 1"),
    ("grupe", "A", "Coreea de Sud", "Cehia", "2026-06-12 05:00", "Guadalajara", 1, "Etapa 1"),
    ("grupe", "B", "Canada", "Bosnia", "2026-06-12 22:00", "Toronto", 1, "Etapa 1"),
    ("grupe", "D", "SUA", "Paraguay", "2026-06-13 04:00", "Los Angeles", 1, "Etapa 1"),
    ("grupe", "B", "Qatar", "Elveția", "2026-06-13 22:00", "San Francisco", 1, "Etapa 1"),
    ("grupe", "C", "Brazilia", "Maroc", "2026-06-14 01:00", "New Jersey", 1, "Etapa 1"),
    ("grupe", "C", "Haiti", "Scoția", "2026-06-14 04:00", "Boston", 1, "Etapa 1"),
    ("grupe", "D", "Australia", "Turcia", "2026-06-14 07:00", "Vancouver", 1, "Etapa 1"),
    ("grupe", "E", "Germania", "Curaçao", "2026-06-14 20:00", "Houston", 1, "Etapa 1"),
    ("grupe", "F", "Olanda", "Japonia", "2026-06-14 23:00", "Dallas", 1, "Etapa 1"),
    ("grupe", "E", "Coasta de Fildeș", "Ecuador", "2026-06-15 02:00", "Philadelphia", 1, "Etapa 1"),
    ("grupe", "F", "Suedia", "Tunisia", "2026-06-15 05:00", "Monterrey", 1, "Etapa 1"),
    ("grupe", "H", "Spania", "Capul Verde", "2026-06-15 19:00", "Atlanta", 1, "Etapa 1"),
    ("grupe", "G", "Belgia", "Egipt", "2026-06-15 22:00", "Seattle", 1, "Etapa 1"),
    ("grupe", "H", "Arabia Saudită", "Uruguay", "2026-06-16 01:00", "Miami", 1, "Etapa 1"),
    ("grupe", "G", "Iran", "Noua Zeelandă", "2026-06-16 04:00", "Los Angeles", 1, "Etapa 1"),
    ("grupe", "I", "Franța", "Senegal", "2026-06-16 22:00", "New Jersey", 1, "Etapa 1"),
    ("grupe", "I", "Irak", "Norvegia", "2026-06-17 01:00", "Boston", 1, "Etapa 1"),
    ("grupe", "J", "Argentina", "Algeria", "2026-06-17 04:00", "Kansas City", 1, "Etapa 1"),
    ("grupe", "J", "Austria", "Iordania", "2026-06-17 07:00", "San Francisco", 1, "Etapa 1"),
    ("grupe", "K", "Portugalia", "DR Congo", "2026-06-17 20:00", "Houston", 1, "Etapa 1"),
    ("grupe", "L", "Anglia", "Croația", "2026-06-17 23:00", "Dallas", 1, "Etapa 1"),
    ("grupe", "L", "Ghana", "Panama", "2026-06-18 02:00", "Toronto", 1, "Etapa 1"),
    ("grupe", "K", "Uzbekistan", "Columbia", "2026-06-18 05:00", "Mexico City", 1, "Etapa 1"),

    # ---------------- ETAPA 2 ----------------
    ("grupe", "A", "Cehia", "Africa de Sud", "2026-06-18 19:00", "Atlanta", 1, "Etapa 2"),
    ("grupe", "B", "Elveția", "Bosnia", "2026-06-18 22:00", "Los Angeles", 1, "Etapa 2"),
    ("grupe", "B", "Canada", "Qatar", "2026-06-19 01:00", "Vancouver", 1, "Etapa 2"),
    ("grupe", "A", "Mexic", "Coreea de Sud", "2026-06-19 04:00", "Guadalajara", 1, "Etapa 2"),
    ("grupe", "D", "SUA", "Australia", "2026-06-19 22:00", "Seattle", 1, "Etapa 2"),
    ("grupe", "C", "Scoția", "Maroc", "2026-06-20 01:00", "Boston", 1, "Etapa 2"),
    ("grupe", "C", "Brazilia", "Haiti", "2026-06-20 04:00", "Philadelphia", 1, "Etapa 2"),
    ("grupe", "D", "Turcia", "Paraguay", "2026-06-20 07:00", "San Francisco", 1, "Etapa 2"),
    ("grupe", "F", "Olanda", "Suedia", "2026-06-20 20:00", "Houston", 1, "Etapa 2"),
    ("grupe", "E", "Germania", "Coasta de Fildeș", "2026-06-20 23:00", "Toronto", 1, "Etapa 2"),
    ("grupe", "E", "Ecuador", "Curaçao", "2026-06-21 03:00", "Kansas City", 1, "Etapa 2"),
    ("grupe", "F", "Tunisia", "Japonia", "2026-06-21 07:00", "Monterrey", 1, "Etapa 2"),
    ("grupe", "H", "Spania", "Arabia Saudită", "2026-06-21 19:00", "Atlanta", 1, "Etapa 2"),
    ("grupe", "G", "Belgia", "Iran", "2026-06-21 22:00", "Los Angeles", 1, "Etapa 2"),
    ("grupe", "H", "Uruguay", "Capul Verde", "2026-06-22 01:00", "Miami", 1, "Etapa 2"),
    ("grupe", "G", "Noua Zeelandă", "Egipt", "2026-06-22 04:00", "Vancouver", 1, "Etapa 2"),
    ("grupe", "J", "Argentina", "Austria", "2026-06-22 20:00", "Dallas", 1, "Etapa 2"),
    ("grupe", "I", "Franța", "Irak", "2026-06-23 00:00", "Philadelphia", 1, "Etapa 2"),
    ("grupe", "I", "Norvegia", "Senegal", "2026-06-23 03:00", "Toronto", 1, "Etapa 2"),
    ("grupe", "J", "Iordania", "Algeria", "2026-06-23 06:00", "San Francisco", 1, "Etapa 2"),
    ("grupe", "K", "Portugalia", "Uzbekistan", "2026-06-23 20:00", "Houston", 1, "Etapa 2"),
    ("grupe", "L", "Anglia", "Ghana", "2026-06-23 23:00", "Boston", 1, "Etapa 2"),
    ("grupe", "L", "Panama", "Croația", "2026-06-24 02:00", "Boston", 1, "Etapa 2"),
    ("grupe", "K", "Columbia", "DR Congo", "2026-06-24 05:00", "Guadalajara", 1, "Etapa 2"),

    # ---------------- ETAPA 3 ----------------
    ("grupe", "B", "Elveția", "Canada", "2026-06-24 22:00", "Vancouver", 1, "Etapa 3"),
    ("grupe", "B", "Bosnia", "Qatar", "2026-06-24 22:00", "Seattle", 1, "Etapa 3"),
    ("grupe", "C", "Maroc", "Haiti", "2026-06-25 01:00", "Atlanta", 1, "Etapa 3"),
    ("grupe", "C", "Scoția", "Brazilia", "2026-06-25 01:00", "Miami", 1, "Etapa 3"),
    ("grupe", "A", "Cehia", "Mexic", "2026-06-25 04:00", "Mexico City", 1, "Etapa 3"),
    ("grupe", "A", "Africa de Sud", "Coreea de Sud", "2026-06-25 04:00", "Monterrey", 1, "Etapa 3"),
    ("grupe", "E", "Curaçao", "Coasta de Fildeș", "2026-06-25 23:00", "Philadelphia", 1, "Etapa 3"),
    ("grupe", "E", "Ecuador", "Germania", "2026-06-25 23:00", "New Jersey", 1, "Etapa 3"),
    ("grupe", "F", "Japonia", "Suedia", "2026-06-26 02:00", "Dallas", 1, "Etapa 3"),
    ("grupe", "F", "Tunisia", "Olanda", "2026-06-26 02:00", "Kansas City", 1, "Etapa 3"),
    ("grupe", "D", "Turcia", "SUA", "2026-06-26 05:00", "Los Angeles", 1, "Etapa 3"),
    ("grupe", "D", "Paraguay", "Australia", "2026-06-26 05:00", "San Francisco", 1, "Etapa 3"),
    ("grupe", "I", "Norvegia", "Franța", "2026-06-26 22:00", "Boston", 1, "Etapa 3"),
    ("grupe", "I", "Senegal", "Irak", "2026-06-26 22:00", "Toronto", 1, "Etapa 3"),
    ("grupe", "H", "Capul Verde", "Arabia Saudită", "2026-06-27 03:00", "Houston", 1, "Etapa 3"),
    ("grupe", "H", "Uruguay", "Spania", "2026-06-27 03:00", "Guadalajara", 1, "Etapa 3"),
    ("grupe", "G", "Noua Zeelandă", "Belgia", "2026-06-27 06:00", "Vancouver", 1, "Etapa 3"),
    ("grupe", "G", "Egipt", "Iran", "2026-06-27 06:00", "Seattle", 1, "Etapa 3"),
    ("grupe", "L", "Panama", "Anglia", "2026-06-28 00:00", "New Jersey", 1, "Etapa 3"),
    ("grupe", "L", "Croația", "Ghana", "2026-06-28 00:00", "Philadelphia", 1, "Etapa 3"),
    ("grupe", "K", "Columbia", "Portugalia", "2026-06-28 02:30", "Miami", 1, "Etapa 3"),
    ("grupe", "K", "DR Congo", "Uzbekistan", "2026-06-28 02:30", "Atlanta", 1, "Etapa 3"),
    ("grupe", "J", "Algeria", "Austria", "2026-06-28 05:00", "Kansas City", 1, "Etapa 3"),
    ("grupe", "J", "Iordania", "Argentina", "2026-06-28 05:00", "Dallas", 1, "Etapa 3"),

    # ---------------- 16-imi (echipele se pun cu /echipe) ----------------
    ("r32", None, "Locul 2 Gr. A", "Locul 2 Gr. B", "2026-06-28 22:00", "Los Angeles", 0, "M73"),
    ("r32", None, "Câștig. Gr. C", "Locul 2 Gr. F", "2026-06-29 20:00", "Houston", 0, "M76"),
    ("r32", None, "Câștig. Gr. E", "Cel mai bun loc 3", "2026-06-29 23:30", "Boston", 0, "M74"),
    ("r32", None, "Câștig. Gr. F", "Locul 2 Gr. C", "2026-06-30 04:00", "Monterrey", 0, "M75"),
    ("r32", None, "Câștig. Gr. E", "Câștig. Gr. I", "2026-06-30 20:00", "Dallas", 0, "M78"),
    ("r32", None, "Câștig. Gr. I", "Cel mai bun loc 3", "2026-07-01 00:00", "New York", 0, "M77"),
    ("r32", None, "Câștig. Gr. A", "Cel mai bun loc 3", "2026-07-01 04:00", "Mexico City", 0, "M79"),
    ("r32", None, "Câștig. Gr. L", "Cel mai bun loc 3", "2026-07-01 19:00", "Atlanta", 0, "M80"),
    ("r32", None, "Câștig. Gr. G", "Cel mai bun loc 3", "2026-07-01 23:00", "Seattle", 0, "M82"),
    ("r32", None, "Câștig. Gr. D", "Cel mai bun loc 3", "2026-07-02 03:00", "San Francisco", 0, "M81"),
    ("r32", None, "Câștig. Gr. H", "Locul 2 Gr. J", "2026-07-02 04:00", "Los Angeles", 0, "M84"),
    ("r32", None, "Locul 2 Gr. K", "Locul 2 Gr. L", "2026-07-03 02:00", "Toronto", 0, "M83"),
    ("r32", None, "Câștig. Gr. B", "Cel mai bun loc 3", "2026-07-03 06:00", "Vancouver", 0, "M85"),
    ("r32", None, "Câștig. Gr. J", "Locul 2 Gr. H", "2026-07-03 21:00", "Dallas", 0, "M88"),
    ("r32", None, "Locul 2 Gr. D", "Locul 2 Gr. G", "2026-07-04 01:00", "Miami", 0, "M86"),
    ("r32", None, "Câștig. Gr. K", "Cel mai bun loc 3", "2026-07-04 04:30", "Vancouver", 0, "M87"),

    # ---------------- Optimi ----------------
    ("r16", None, "Câștig. M73", "Câștig. M75", "2026-07-04 20:00", "Houston", 0, "Optimi (M90)"),
    ("r16", None, "Câștig. M74", "Câștig. M77", "2026-07-05 00:00", "Philadelphia", 0, "Optimi (M89)"),
    ("r16", None, "Câștig. M76", "Câștig. M78", "2026-07-05 01:00", "New York", 0, "Optimi (M91)"),
    ("r16", None, "Câștig. M79", "Câștig. M80", "2026-07-06 03:00", "Mexico City", 0, "Optimi (M92)"),
    ("r16", None, "Câștig. M83", "Câștig. M84", "2026-07-06 22:00", "Dallas", 0, "Optimi (M93)"),
    ("r16", None, "Câștig. M81", "Câștig. M82", "2026-07-07 03:00", "Seattle", 0, "Optimi (M94)"),
    ("r16", None, "Câștig. M86", "Câștig. M88", "2026-07-07 19:00", "Atlanta", 0, "Optimi (M95)"),
    ("r16", None, "Câștig. M85", "Câștig. M87", "2026-07-07 23:00", "Vancouver", 0, "Optimi (M96)"),

    # ---------------- Sferturi ----------------
    ("sferturi", None, "Câștig. Optimi 1", "Câștig. Optimi 2", "2026-07-09 23:00", "Boston", 0, "Sfert 1"),
    ("sferturi", None, "Câștig. Optimi 3", "Câștig. Optimi 4", "2026-07-10 22:00", "Los Angeles", 0, "Sfert 2"),
    ("sferturi", None, "Câștig. Optimi 5", "Câștig. Optimi 6", "2026-07-12 00:00", "Miami", 0, "Sfert 3"),
    ("sferturi", None, "Câștig. Optimi 7", "Câștig. Optimi 8", "2026-07-12 04:00", "Kansas City", 0, "Sfert 4"),

    # ---------------- Semifinale ----------------
    ("semifinale", None, "Câștig. Sfert 1", "Câștig. Sfert 2", "2026-07-14 22:00", "Dallas", 0, "Semifinala 1"),
    ("semifinale", None, "Câștig. Sfert 3", "Câștig. Sfert 4", "2026-07-15 22:00", "Atlanta", 0, "Semifinala 2"),

    # ---------------- Finala mică & Finala ----------------
    ("finala_mica", None, "Învins SF1", "Învins SF2", "2026-07-19 00:00", "Miami", 0, "Finala mică"),
    ("finala", None, "Câștig. SF1", "Câștig. SF2", "2026-07-19 22:00", "New York", 0, "FINALA"),
]

# Butoane de scor pentru un meci (1X2 + scoruri uzuale + alt scor)
SCORE_ROWS = [
    ["1", "X", "2"],
    ["0-0", "1-0", "0-1"],
    ["1-1", "2-1", "1-2"],
    ["2-2", "2-0", "0-2"],
    ["3-3", "3-2", "2-3"],
    ["3-0", "0-3", "3-1"],
    ["1-3", "✏️ Alt scor"],
]

POINTS_TEXT = (
    "📊 Punctaj:\n"
    "• Campioana mondială — 5p\n"
    "• Câștigătoarea unei grupe — 2p\n"
    "• Rezultat meci (1 X 2) — 3p\n"
    "• Scor exact — +2p (bonus)\n"
    "• Golgheterul turneului — 4p"
)


# ==============================================================================
#  BAZA DE DATE
# ==============================================================================

_db_dir = os.path.dirname(DB_PATH)
if _db_dir:
    os.makedirs(_db_dir, exist_ok=True)
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.row_factory = sqlite3.Row
_lock = threading.Lock()


def db_run(query, params=()):
    with _lock:
        cur = _conn.execute(query, params)
        _conn.commit()
        return cur


def db_all(query, params=()):
    with _lock:
        return _conn.execute(query, params).fetchall()


def db_one(query, params=()):
    with _lock:
        return _conn.execute(query, params).fetchone()


SCHEMA_VERSION = "2"   # crește numărul dacă schimbi structura tabelelor


def init_db():
    # config table — reparat dacă a rămas incompatibil de la alt bot
    info = db_all("PRAGMA table_info(config)")
    if info and not {"key", "value"}.issubset({r["name"] for r in info}):
        db_run("DROP TABLE config")
    db_run("""CREATE TABLE IF NOT EXISTS config(key TEXT PRIMARY KEY, value TEXT)""")

    # Dacă baza de date de pe Volume nu e a botului ăstuia (rămasă de la botul vechi)
    # sau lipsește marcajul de versiune, ștergem TOATE tabelele și le refacem curat.
    # La un restart normal versiunea se potrivește și NU se șterge nimic (datele rămân).
    if get_config("schema_version") != SCHEMA_VERSION:
        log.warning("Schemă veche/incompatibilă pe Volume — recreez baza de date curat.")
        for t in ("users", "global_predictions", "matches",
                  "match_predictions", "results", "bonus"):
            db_run(f"DROP TABLE IF EXISTS {t}")
        set_config("schema_version", SCHEMA_VERSION)

    db_run("""CREATE TABLE IF NOT EXISTS users(
        telegram_id INTEGER PRIMARY KEY,
        username TEXT, full_name TEXT,
        approved INTEGER DEFAULT 0, joined_at TEXT)""")
    db_run("""CREATE TABLE IF NOT EXISTS global_predictions(
        telegram_id INTEGER, kind TEXT, value TEXT,
        PRIMARY KEY(telegram_id, kind))""")
    db_run("""CREATE TABLE IF NOT EXISTS matches(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stage TEXT, grp TEXT, home TEXT, away TEXT, kickoff TEXT,
        venue TEXT, teams_known INTEGER DEFAULT 1, note TEXT,
        result_home INTEGER, result_away INTEGER, finished INTEGER DEFAULT 0)""")
    db_run("""CREATE TABLE IF NOT EXISTS match_predictions(
        telegram_id INTEGER, match_id INTEGER, pick TEXT,
        PRIMARY KEY(telegram_id, match_id))""")
    db_run("""CREATE TABLE IF NOT EXISTS results(kind TEXT PRIMARY KEY, value TEXT)""")
    db_run("""CREATE TABLE IF NOT EXISTS bonus(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER, points REAL, reason TEXT, created_at TEXT)""")

    # Seed meciuri dacă tabelul e gol.
    if db_one("SELECT COUNT(*) c FROM matches")["c"] == 0:
        for m in SEED_MATCHES:
            db_run("""INSERT INTO matches(stage,grp,home,away,kickoff,venue,teams_known,note)
                      VALUES(?,?,?,?,?,?,?,?)""", m)
        set_config("seeded", "1")
        log.info("Meciuri inițializate: %d", len(SEED_MATCHES))


def get_config(key, default=None):
    row = db_one("SELECT value FROM config WHERE key=?", (key,))
    return row["value"] if row else default


def set_config(key, value):
    db_run("INSERT OR REPLACE INTO config(key,value) VALUES(?,?)", (key, str(value)))


# ==============================================================================
#  HELPERE
# ==============================================================================

def norm(s):
    """Normalizează pentru comparații (fără diacritice, lowercase)."""
    if s is None:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.strip().lower()


def is_admin(uid):
    return uid in ADMIN_IDS


def open_groups():
    return [g.strip() for g in (get_config("open_groups", "A,B")).split(",") if g.strip()]


def fmt_pts(p):
    p = float(p)
    return str(int(p)) if p == int(p) else f"{p:.1f}"


def parse_score(text):
    """'2-1' / '2:1' / '2 1' -> (2,1) sau None."""
    t = text.strip().replace(":", "-").replace(" ", "-")
    while "--" in t:
        t = t.replace("--", "-")
    parts = t.split("-")
    if len(parts) != 2:
        return None
    try:
        h, a = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if 0 <= h <= 30 and 0 <= a <= 30:
        return (h, a)
    return None


def outcome(h, a):
    return "1" if h > a else ("X" if h == a else "2")


def fmt_kick(kickoff):
    try:
        d = datetime.strptime(kickoff, "%Y-%m-%d %H:%M")
        return d.strftime("%d.%m, %H:%M")
    except ValueError:
        return kickoff


def now_str():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M")


def current_window(now=None):
    now = now or datetime.now(TZ)
    if now.hour >= DAILY_HOUR:
        start = now.replace(hour=DAILY_HOUR, minute=0, second=0, microsecond=0)
    else:
        start = (now - timedelta(days=1)).replace(
            hour=DAILY_HOUR, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def matches_in(start_dt, end_dt):
    s = start_dt.strftime("%Y-%m-%d %H:%M")
    e = end_dt.strftime("%Y-%m-%d %H:%M")
    return db_all(
        "SELECT * FROM matches WHERE kickoff>=? AND kickoff<? ORDER BY kickoff", (s, e))


def find_team(query, pool):
    """Caută o echipă într-o listă, tolerant la diacritice / litere mari."""
    q = norm(query)
    for t in pool:
        if norm(t) == q:
            return t
    matches = [t for t in pool if q in norm(t)]
    return matches[0] if len(matches) == 1 else None


def find_player(query):
    q = norm(query)
    for n in TS_NAMES:
        if norm(n) == q:
            return n
    matches = [n for n in TS_NAMES if q in norm(n)]
    return matches[0] if len(matches) == 1 else None


# ==============================================================================
#  PUNCTAJ
# ==============================================================================

def score_user(uid, results=None):
    """Returnează (total, listă_linii) pentru un participant."""
    if results is None:
        results = {r["kind"]: r["value"] for r in db_all("SELECT kind,value FROM results")}
    total = 0.0
    lines = []

    gp = {r["kind"]: r["value"] for r in
          db_all("SELECT kind,value FROM global_predictions WHERE telegram_id=?", (uid,))}

    # Campioana
    if "champion" in gp:
        real = results.get("champion")
        if real:
            ok = norm(gp["champion"]) == norm(real)
            total += 5 if ok else 0
            lines.append(f"Campioană: {gp['champion']} {'✅ +5' if ok else '❌'}")
        else:
            lines.append(f"Campioană: {gp['champion']} ⏳")

    # Golgheter
    if "topscorer" in gp:
        real = results.get("topscorer")
        if real:
            ok = (norm(gp["topscorer"]) == norm(real)) or \
                 (gp["topscorer"] == "ALTUL" and find_player(real) is None)
            total += 4 if ok else 0
            lines.append(f"Golgheter: {gp['topscorer']} {'✅ +4' if ok else '❌'}")
        else:
            lines.append(f"Golgheter: {gp['topscorer']} ⏳")

    # Grupe
    for k in sorted(gp):
        if not k.startswith("group_"):
            continue
        letter = k.split("_")[1]
        real = results.get(k)
        if real:
            ok = norm(gp[k]) == norm(real)
            total += 2 if ok else 0
            lines.append(f"Grupa {letter}: {gp[k]} {'✅ +2' if ok else '❌'}")
        else:
            lines.append(f"Grupa {letter}: {gp[k]} ⏳")

    # Meciuri
    preds = db_all("SELECT match_id, pick FROM match_predictions WHERE telegram_id=?", (uid,))
    if preds:
        ids = [str(p["match_id"]) for p in preds]
        rows = db_all(
            f"SELECT * FROM matches WHERE id IN ({','.join('?' * len(ids))})", ids)
        mby = {r["id"]: r for r in rows}
        mpts = 0.0
        for p in preds:
            m = mby.get(p["match_id"])
            if not m or not m["finished"] or m["result_home"] is None:
                continue
            rh, ra = m["result_home"], m["result_away"]
            res_out = outcome(rh, ra)
            pick = p["pick"]
            gained = 0
            if pick in ("1", "X", "2"):
                if pick == res_out:
                    gained = 3
            else:
                sc = parse_score(pick)
                if sc:
                    if sc == (rh, ra):
                        gained = 5
                    elif outcome(*sc) == res_out:
                        gained = 3
            mpts += gained
        if mpts:
            lines.append(f"Meciuri: +{fmt_pts(mpts)}p")
        total += mpts

    # Bonus
    brow = db_one("SELECT COALESCE(SUM(points),0) s FROM bonus WHERE telegram_id=?", (uid,))
    bsum = brow["s"] if brow else 0
    if bsum:
        total += bsum
        lines.append(f"Bonus: {'+' if bsum >= 0 else ''}{fmt_pts(bsum)}p")

    return total, lines


def leaderboard():
    results = {r["kind"]: r["value"] for r in db_all("SELECT kind,value FROM results")}
    users = db_all("SELECT telegram_id, full_name FROM users WHERE approved=1")
    rows = []
    for u in users:
        total, _ = score_user(u["telegram_id"], results)
        rows.append((u["full_name"] or "—", total))
    rows.sort(key=lambda x: (-x[1], x[0].lower()))
    return rows


def render_leaderboard():
    rows = leaderboard()
    if not rows:
        return "🏆 CLASAMENT\n\nNiciun participant încă."
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    out = ["🏆 CLASAMENT\n"]
    rank, prev = 0, None
    for i, (name, pts) in enumerate(rows, 1):
        if pts != prev:
            rank = i
            prev = pts
        tag = medals.get(rank, f"{rank}.")
        out.append(f"{tag} {name} — {fmt_pts(pts)}p")
    return "\n".join(out)


# ==============================================================================
#  TASTATURI
# ==============================================================================

def kb_champion():
    btns = [InlineKeyboardButton(t, callback_data=f"champ:{i}")
            for i, t in enumerate(ALL_TEAMS)]
    rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
    return InlineKeyboardMarkup(rows)


def kb_topscorers_fav():
    btns = [InlineKeyboardButton(f"{n} ({fmt_pts(c)})",
                                 callback_data=f"ts:{TS_NAMES.index(n)}")
            for n, c in TS_FAVORITES]
    rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
    rows.append([InlineKeyboardButton("🔍 Alt jucător", callback_data="ts:search")])
    return InlineKeyboardMarkup(rows)


def kb_topscorers_search(found):
    btns = [InlineKeyboardButton(n, callback_data=f"ts:{TS_NAMES.index(n)}")
            for n in found[:12]]
    rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
    rows.append([InlineKeyboardButton("🔍 Caută din nou", callback_data="ts:search"),
                 InlineKeyboardButton("❓ ALTUL", callback_data="ts:other")])
    return InlineKeyboardMarkup(rows)


def kb_group(letter):
    teams = GROUPS[letter]
    btns = [InlineKeyboardButton(t, callback_data=f"grp:{letter}:{i}")
            for i, t in enumerate(teams)]
    return InlineKeyboardMarkup([btns[i:i + 2] for i in range(0, len(btns), 2)])


def kb_match_scores(win, mid):
    rows = []
    for row in SCORE_ROWS:
        line = []
        for label in row:
            pick = "alt" if label.startswith("✏️") else label
            line.append(InlineKeyboardButton(label, callback_data=f"sp:{win}:{mid}:{pick}"))
        rows.append(line)
    rows.append([InlineKeyboardButton("« Înapoi la meciuri", callback_data=f"bl:{win}")])
    return InlineKeyboardMarkup(rows)


def kb_match_list(win, uid):
    start, end = current_window()
    if win == "m":
        start, end = end, end + timedelta(days=1)
    ms = matches_in(start, end)
    rows = []
    for m in ms:
        pred = db_one(
            "SELECT pick FROM match_predictions WHERE telegram_id=? AND match_id=?",
            (uid, m["id"]))
        if not m["teams_known"]:
            label = f"🔒 {fmt_kick(m['kickoff'])} {m['home']} – {m['away']}"
            rows.append([InlineKeyboardButton(label, callback_data="noop")])
        elif m["kickoff"] <= now_str():
            tag = f" (tu: {pred['pick']})" if pred else ""
            label = f"⏱ {fmt_kick(m['kickoff'])} {m['home']}–{m['away']}{tag}"
            rows.append([InlineKeyboardButton(label, callback_data="noop")])
        else:
            tag = f" ✅{pred['pick']}" if pred else ""
            label = f"⚽️ {fmt_kick(m['kickoff'])} {m['home']}–{m['away']}{tag}"
            rows.append([InlineKeyboardButton(label, callback_data=f"po:{win}:{m['id']}")])
    return ms, InlineKeyboardMarkup(rows) if rows else None


# ==============================================================================
#  ÎNROLARE (onboarding)
# ==============================================================================

async def prompt_next(bot, chat_id, uid):
    """Trimite următorul pas obligatoriu, sau meniul dacă s-a terminat."""
    gp = {r["kind"] for r in
          db_all("SELECT kind FROM global_predictions WHERE telegram_id=?", (uid,))}

    if "champion" not in gp:
        await bot.send_message(chat_id, "1️⃣ Alege CAMPIOANA mondială:\n"
                               "(atenție: alegerea NU se mai poate schimba)",
                               reply_markup=kb_champion())
        return
    if "topscorer" not in gp:
        await bot.send_message(chat_id, "2️⃣ Alege GOLGHETERUL turneului:\n"
                               "(cei mai cotați sunt mai jos; pentru altcineva apasă 'Alt jucător')",
                               reply_markup=kb_topscorers_fav())
        return
    for L in open_groups():
        if f"group_{L}" not in gp:
            await bot.send_message(chat_id, f"Alege CÂȘTIGĂTOAREA Grupei {L}:",
                                   reply_markup=kb_group(L))
            return

    await bot.send_message(
        chat_id,
        "✅ Gata! Ți-ai trimis toate pronosticurile obligatorii.\n\n"
        + POINTS_TEXT +
        "\n\nDe acum: /azi · /maine ca să pariezi pe meciuri.\n"
        "/pronosticurile · /clasament · /premii · /ajutor")


# ==============================================================================
#  COMENZI UTILIZATOR
# ==============================================================================

WELCOME = (
    "⚽️ Bun venit la Concursul Cupa Mondială 2026!\n\n"
    + POINTS_TEXT +
    "\n\n📌 Comenzi: /azi · /maine · /pronosticurile · /clasament · /premii · /ajutor"
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user

    if chat.type != "private":
        me = (await context.bot.get_me()).username
        await update.message.reply_text(
            f"Salut! Ca să intri în concurs, scrie-mi în privat: @{me}")
        return

    row = db_one("SELECT * FROM users WHERE telegram_id=?", (user.id,))
    if row is None:
        db_run("""INSERT INTO users(telegram_id,username,full_name,approved,joined_at)
                  VALUES(?,?,?,0,?)""",
               (user.id, user.username or "", user.full_name, now_str()))
        # anunță adminul cu butoane
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Aprobă", callback_data=f"adm:ok:{user.id}"),
            InlineKeyboardButton("❌ Respinge", callback_data=f"adm:no:{user.id}")]])
        uname = f"@{user.username}" if user.username else "(fără username)"
        for aid in ADMIN_IDS:
            try:
                await context.bot.send_message(
                    aid, f"🆕 Cerere de înscriere:\n{user.full_name} {uname}\nID: {user.id}",
                    reply_markup=kb)
            except Exception as e:
                log.warning("Nu pot anunța adminul %s: %s", aid, e)
        await update.message.reply_text(
            "Cererea ta a fost trimisă. ⏳ Așteaptă aprobarea adminului.")
        return

    if not row["approved"]:
        await update.message.reply_text("⏳ Încă aștepți aprobarea adminului.")
        return

    await update.message.reply_text(WELCOME)
    await prompt_next(context.bot, chat.id, user.id)


async def cmd_ajutor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        WELCOME + "\n\n• /grupe — alege câștigătoarele grupelor deschise\n"
        "• Pronosticul pe un meci se poate schimba până începe meciul.")


async def cmd_premii(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎁 Premii\n\n" + get_config("prizes", "Premiile vor fi anunțate de admin."))


async def _require_player(update):
    """Verifică dacă userul e aprobat + și-a terminat înscrierea pentru meciuri."""
    uid = update.effective_user.id
    row = db_one("SELECT approved FROM users WHERE telegram_id=?", (uid,))
    if not row or not row["approved"]:
        await update.message.reply_text("⏳ Trebuie să fii aprobat de admin. Scrie /start.")
        return False
    gp = {r["kind"] for r in
          db_all("SELECT kind FROM global_predictions WHERE telegram_id=?", (uid,))}
    needed = ["champion", "topscorer"] + [f"group_{L}" for L in open_groups()]
    if any(k not in gp for k in needed):
        await update.message.reply_text(
            "Întâi termină pronosticurile obligatorii 👇")
        await prompt_next(update.get_bot(), update.effective_chat.id, uid)
        return False
    return True


async def show_window(update, context, win):
    if update.effective_chat.type != "private":
        await update.message.reply_text("Scrie-mi în privat ca să pariezi 🙂")
        return
    if not await _require_player(update):
        return
    uid = update.effective_user.id
    ms, kb = kb_match_list(win, uid)
    titlu = "📅 MECIURILE DE AZI (12:00 → 12:00)" if win == "a" \
        else "📅 MECIURILE DE MÂINE"
    if not ms:
        await update.message.reply_text(titlu + "\n\nNiciun meci în acest interval.")
        return
    await update.message.reply_text(
        titlu + "\n\n⚽️ = poți pronostica · 🔒 = echipe nestabilite · ⏱ = a început\n"
        "Apasă pe un meci ca să alegi scorul (1X2 + scor exact).",
        reply_markup=kb)


async def cmd_azi(update, context):
    await show_window(update, context, "a")


async def cmd_maine(update, context):
    await show_window(update, context, "m")


async def cmd_grupe(update, context):
    if update.effective_chat.type != "private":
        return
    uid = update.effective_user.id
    row = db_one("SELECT approved FROM users WHERE telegram_id=?", (uid,))
    if not row or not row["approved"]:
        await update.message.reply_text("⏳ Trebuie să fii aprobat. Scrie /start.")
        return
    await prompt_next(context.bot, update.effective_chat.id, uid)


async def cmd_pronosticurile(update, context):
    if update.effective_chat.type != "private":
        await update.message.reply_text("Scrie-mi în privat pentru pronosticurile tale.")
        return
    uid = update.effective_user.id
    row = db_one("SELECT approved FROM users WHERE telegram_id=?", (uid,))
    if not row or not row["approved"]:
        await update.message.reply_text("⏳ Trebuie să fii aprobat. Scrie /start.")
        return
    total, lines = score_user(uid)
    out = ["🎯 PRONOSTICURILE TALE\n"]
    out += lines if lines else ["(încă nimic ales)"]

    preds = db_all("SELECT match_id, pick FROM match_predictions WHERE telegram_id=?", (uid,))
    if preds:
        ids = [str(p["match_id"]) for p in preds]
        rows = db_all(f"SELECT * FROM matches WHERE id IN ({','.join('?'*len(ids))}) "
                      "ORDER BY kickoff", ids)
        pby = {p["match_id"]: p["pick"] for p in preds}
        upcoming = [r for r in rows if r["kickoff"] > now_str()]
        if upcoming:
            out.append("\n📌 Meciuri pariate (viitoare):")
            for r in upcoming:
                out.append(f"• {fmt_kick(r['kickoff'])} {r['home']}–{r['away']}: "
                           f"{pby[r['id']]}")
    out.append(f"\n💰 TOTAL: {fmt_pts(total)}p")
    await update.message.reply_text("\n".join(out))


async def cmd_clasament(update, context):
    await update.message.reply_text(render_leaderboard())


async def cmd_id(update, context):
    u = update.effective_user
    await update.message.reply_text(
        f"ID-ul tău: {u.id}\nID-ul acestui chat: {update.effective_chat.id}")


# ==============================================================================
#  COMENZI ADMIN
# ==============================================================================

def admin_only(func):
    async def wrap(update, context):
        if not is_admin(update.effective_user.id):
            return
        return await func(update, context)
    return wrap


@admin_only
async def cmd_setgrup(update, context):
    cid = update.effective_chat.id
    set_config("group_chat_id", cid)
    await update.message.reply_text(f"✅ Grup setat (ID {cid}). Aici postez la {DAILY_HOUR:02d}:00.")


@admin_only
async def cmd_aproba(update, context):
    if not context.args:
        await update.message.reply_text("Folosire: /aproba <id>")
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID invalid.")
        return
    await approve_user(context, uid, update.message)


async def approve_user(context, uid, reply_to=None):
    row = db_one("SELECT * FROM users WHERE telegram_id=?", (uid,))
    if not row:
        if reply_to:
            await reply_to.reply_text("Userul nu există (nu a dat /start botului).")
        return
    db_run("UPDATE users SET approved=1 WHERE telegram_id=?", (uid,))
    if reply_to:
        await reply_to.reply_text(f"✅ Aprobat: {row['full_name']}")
    try:
        await context.bot.send_message(uid, "🎉 Ai fost aprobat! Hai să începem 👇")
        await context.bot.send_message(uid, WELCOME)
        await prompt_next(context.bot, uid, uid)
    except Exception as e:
        log.warning("Nu pot scrie userului %s: %s", uid, e)


@admin_only
async def cmd_resping(update, context):
    if not context.args:
        await update.message.reply_text("Folosire: /resping <id>")
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        return
    db_run("DELETE FROM users WHERE telegram_id=?", (uid,))
    db_run("DELETE FROM global_predictions WHERE telegram_id=?", (uid,))
    db_run("DELETE FROM match_predictions WHERE telegram_id=?", (uid,))
    await update.message.reply_text("Participant scos.")


@admin_only
async def cmd_useri(update, context):
    rows = db_all("SELECT telegram_id, full_name, username, approved FROM users ORDER BY approved DESC, full_name")
    if not rows:
        await update.message.reply_text("Niciun participant.")
        return
    out = ["👥 PARTICIPANȚI\n"]
    for r in rows:
        st = "✅" if r["approved"] else "⏳"
        un = f"@{r['username']}" if r["username"] else ""
        out.append(f"{st} {r['full_name']} {un} — ID {r['telegram_id']}")
    await update.message.reply_text("\n".join(out))


@admin_only
async def cmd_scor(update, context):
    if len(context.args) < 2:
        await update.message.reply_text("Folosire: /scor <id_meci> 2-1")
        return
    try:
        mid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID meci invalid.")
        return
    sc = parse_score(" ".join(context.args[1:]))
    if not sc:
        await update.message.reply_text("Scor invalid. Ex: /scor 5 2-1")
        return
    m = db_one("SELECT * FROM matches WHERE id=?", (mid,))
    if not m:
        await update.message.reply_text("Nu există meciul cu acest ID. Vezi /lista")
        return
    db_run("UPDATE matches SET result_home=?, result_away=?, finished=1 WHERE id=?",
           (sc[0], sc[1], mid))
    await update.message.reply_text(
        f"✅ Rezultat salvat: {m['home']} {sc[0]}-{sc[1]} {m['away']}\n"
        "Punctele se actualizează automat la /clasament.")


@admin_only
async def cmd_anuleaza_scor(update, context):
    if not context.args:
        await update.message.reply_text("Folosire: /anuleaza_scor <id>")
        return
    try:
        mid = int(context.args[0])
    except ValueError:
        return
    db_run("UPDATE matches SET result_home=NULL, result_away=NULL, finished=0 WHERE id=?", (mid,))
    await update.message.reply_text("Rezultat șters.")


@admin_only
async def cmd_grupa(update, context):
    if len(context.args) < 2:
        await update.message.reply_text("Folosire: /grupa A Mexic")
        return
    letter = context.args[0].upper()
    if letter not in GROUPS:
        await update.message.reply_text("Grupă inexistentă (A–L).")
        return
    team = find_team(" ".join(context.args[1:]), GROUPS[letter])
    if not team:
        await update.message.reply_text(
            "Echipă negăsită. Opțiuni: " + ", ".join(GROUPS[letter]))
        return
    set_result(f"group_{letter}", team)
    await update.message.reply_text(f"✅ Câștigătoarea Grupei {letter}: {team}")


@admin_only
async def cmd_campioana(update, context):
    if not context.args:
        await update.message.reply_text("Folosire: /campioana Spania")
        return
    team = find_team(" ".join(context.args), ALL_TEAMS)
    if not team:
        await update.message.reply_text("Echipă negăsită. Scrie numele exact.")
        return
    set_result("champion", team)
    await update.message.reply_text(f"✅ Campioana mondială: {team}")


@admin_only
async def cmd_golgheter(update, context):
    if not context.args:
        await update.message.reply_text("Folosire: /golgheter Mbappe  (sau /golgheter ALTUL)")
        return
    raw = " ".join(context.args)
    if norm(raw) == "altul":
        set_result("topscorer", "ALTUL")
        await update.message.reply_text("✅ Golgheter setat: ALTUL (cineva din afara listei).")
        return
    player = find_player(raw)
    value = player or raw
    set_result("topscorer", value)
    extra = "" if player else " (⚠️ nu e în listă — câștigă cei cu 'ALTUL')"
    await update.message.reply_text(f"✅ Golgheter: {value}{extra}")


def set_result(kind, value):
    db_run("INSERT OR REPLACE INTO results(kind,value) VALUES(?,?)", (kind, value))


@admin_only
async def cmd_lista(update, context):
    rows = db_all("SELECT * FROM matches ORDER BY kickoff")
    out = ["📋 MECIURI (ID · oră · meci · rezultat)\n"]
    for m in rows:
        res = ""
        if m["finished"] and m["result_home"] is not None:
            res = f" [{m['result_home']}-{m['result_away']}]"
        lock = "" if m["teams_known"] else " 🔒"
        out.append(f"{m['id']}: {fmt_kick(m['kickoff'])} {m['home']}–{m['away']}{res}{lock}")
    text = "\n".join(out)
    for i in range(0, len(text), 3800):
        await update.message.reply_text(text[i:i + 3800])


@admin_only
async def cmd_echipe(update, context):
    if not context.args:
        await update.message.reply_text("Folosire: /echipe <id> Brazilia - Spania")
        return
    try:
        mid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID invalid.")
        return
    rest = " ".join(context.args[1:])
    sep = " - " if " - " in rest else (" vs " if " vs " in rest else None)
    if not sep:
        await update.message.reply_text("Format: /echipe 90 Brazilia - Spania")
        return
    home, away = [x.strip() for x in rest.split(sep, 1)]
    db_run("UPDATE matches SET home=?, away=?, teams_known=1 WHERE id=?", (home, away, mid))
    await update.message.reply_text(f"✅ Meci {mid}: {home} – {away} (deschis la pariuri)")


@admin_only
async def cmd_ora(update, context):
    if len(context.args) < 3:
        await update.message.reply_text("Folosire: /ora <id> 2026-07-09 23:00")
        return
    try:
        mid = int(context.args[0])
        ko = f"{context.args[1]} {context.args[2]}"
        datetime.strptime(ko, "%Y-%m-%d %H:%M")
    except ValueError:
        await update.message.reply_text("Dată invalidă. Format: 2026-07-09 23:00")
        return
    db_run("UPDATE matches SET kickoff=? WHERE id=?", (ko, mid))
    await update.message.reply_text(f"✅ Ora meciului {mid}: {fmt_kick(ko)}")


def _split_pipe(text, command):
    payload = text.split(maxsplit=1)
    if len(payload) < 2:
        return None
    return [p.strip() for p in payload[1].split("|")]


@admin_only
async def cmd_amical(update, context):
    parts = _split_pipe(update.message.text, "amical")
    if not parts or len(parts) < 3:
        await update.message.reply_text(
            "Folosire: /amical Italia | Brazilia | 2026-06-06 21:00")
        return
    home, away, ko = parts[0], parts[1], parts[2]
    try:
        datetime.strptime(ko, "%Y-%m-%d %H:%M")
    except ValueError:
        await update.message.reply_text("Dată invalidă. Format: 2026-06-06 21:00")
        return
    db_run("""INSERT INTO matches(stage,grp,home,away,kickoff,venue,teams_known,note)
              VALUES('amical',NULL,?,?,?,'Amical',1,'AMICAL')""", (home, away, ko))
    await update.message.reply_text(f"✅ Amical adăugat: {home} – {away} ({fmt_kick(ko)})")


@admin_only
async def cmd_adauga_meci(update, context):
    parts = _split_pipe(update.message.text, "adauga_meci")
    if not parts or len(parts) < 5:
        await update.message.reply_text(
            "Folosire:\n/adauga_meci grupe | A | Mexic | Cehia | 2026-06-11 22:00 | Mexico\n"
            "(grupa și locul pot fi '-')")
        return
    stage = parts[0] or "grupe"
    grp = None if parts[1] in ("", "-") else parts[1].upper()
    home, away, ko = parts[2], parts[3], parts[4]
    venue = parts[5] if len(parts) > 5 and parts[5] not in ("", "-") else ""
    try:
        datetime.strptime(ko, "%Y-%m-%d %H:%M")
    except ValueError:
        await update.message.reply_text("Dată invalidă. Format: 2026-06-11 22:00")
        return
    db_run("""INSERT INTO matches(stage,grp,home,away,kickoff,venue,teams_known,note)
              VALUES(?,?,?,?,?,?,1,'')""", (stage, grp, home, away, ko, venue))
    await update.message.reply_text(f"✅ Meci adăugat: {home} – {away} ({fmt_kick(ko)})")


@admin_only
async def cmd_sterge_meci(update, context):
    if not context.args:
        await update.message.reply_text("Folosire: /sterge_meci <id>")
        return
    try:
        mid = int(context.args[0])
    except ValueError:
        return
    db_run("DELETE FROM matches WHERE id=?", (mid,))
    db_run("DELETE FROM match_predictions WHERE match_id=?", (mid,))
    await update.message.reply_text("Meci șters.")


@admin_only
async def cmd_deschide_grupa(update, context):
    if not context.args:
        await update.message.reply_text("Folosire: /deschide_grupa C")
        return
    L = context.args[0].upper()
    if L not in GROUPS:
        await update.message.reply_text("Grupă inexistentă (A–L).")
        return
    og = open_groups()
    if L in og:
        await update.message.reply_text(f"Grupa {L} e deja deschisă.")
        return
    og.append(L)
    set_config("open_groups", ",".join(og))
    await update.message.reply_text(f"✅ Grupa {L} deschisă la vot.")
    # anunță participanții
    for u in db_all("SELECT telegram_id FROM users WHERE approved=1"):
        try:
            await context.bot.send_message(
                u["telegram_id"],
                f"🆕 S-a deschis Grupa {L} la vot! Scrie /grupe ca să alegi câștigătoarea.")
        except Exception:
            pass


@admin_only
async def cmd_premii_set(update, context):
    payload = update.message.text.split(maxsplit=1)
    if len(payload) < 2:
        await update.message.reply_text("Folosire: /premii_set <text>")
        return
    set_config("prizes", payload[1])
    await update.message.reply_text("✅ Premii actualizate.")


@admin_only
async def cmd_anunt(update, context):
    payload = update.message.text.split(maxsplit=1)
    if len(payload) < 2:
        await update.message.reply_text("Folosire: /anunt <text>")
        return
    msg = payload[1]
    n = 0
    for u in db_all("SELECT telegram_id FROM users WHERE approved=1"):
        try:
            await context.bot.send_message(u["telegram_id"], "📢 " + msg)
            n += 1
        except Exception:
            pass
    await update.message.reply_text(f"Trimis la {n} participanți.")


@admin_only
async def cmd_bonus(update, context):
    """ BACKUP: adaugă/scade puncte manual. /bonus <id sau @user> 3 motiv """
    if len(context.args) < 2:
        await update.message.reply_text("Folosire: /bonus <id sau @user> 3 motiv\n"
                                        "(poți pune și -3 ca să scazi)")
        return
    target = context.args[0]
    uid = None
    if target.startswith("@"):
        row = db_one("SELECT telegram_id FROM users WHERE LOWER(username)=?",
                     (target[1:].lower(),))
        uid = row["telegram_id"] if row else None
    else:
        try:
            uid = int(target)
        except ValueError:
            uid = None
    if uid is None:
        await update.message.reply_text("Participant negăsit. Folosește ID-ul (vezi /useri).")
        return
    try:
        pts = float(context.args[1])
    except ValueError:
        await update.message.reply_text("Puncte invalide.")
        return
    reason = " ".join(context.args[2:]) or "bonus admin"
    db_run("INSERT INTO bonus(telegram_id,points,reason,created_at) VALUES(?,?,?,?)",
           (uid, pts, reason, now_str()))
    await update.message.reply_text(
        f"✅ {'+' if pts >= 0 else ''}{fmt_pts(pts)}p pentru ID {uid} ({reason}).")


@admin_only
async def cmd_reset_pronosticuri(update, context):
    db_run("DELETE FROM match_predictions")
    await update.message.reply_text("✅ Toate pronosticurile pe meciuri au fost șterse.")


@admin_only
async def cmd_reset_rezultate(update, context):
    db_run("DELETE FROM results")
    db_run("UPDATE matches SET result_home=NULL, result_away=NULL, finished=0")
    await update.message.reply_text("✅ Toate rezultatele au fost șterse.")


@admin_only
async def cmd_reset_tot(update, context):
    db_run("DELETE FROM match_predictions")
    db_run("DELETE FROM global_predictions")
    db_run("DELETE FROM results")
    db_run("DELETE FROM bonus")
    db_run("UPDATE matches SET result_home=NULL, result_away=NULL, finished=0")
    set_config("open_groups", "A,B")
    await update.message.reply_text(
        "✅ Resetat TOT (pronosticuri, rezultate, bonus). Participanții AU RĂMAS — "
        "nu trebuie să intre din nou. Pot reîncepe cu /start.")


# ==============================================================================
#  CALLBACK-uri (butoane)
# ==============================================================================

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""
    uid = q.from_user.id

    if data == "noop":
        await q.answer()
        return

    # --- admin aprobă / respinge ---
    if data.startswith("adm:"):
        if not is_admin(uid):
            await q.answer("Doar adminul.", show_alert=True)
            return
        _, action, target = data.split(":")
        target = int(target)
        if action == "ok":
            await q.answer("Aprobat ✅")
            await q.edit_message_text(q.message.text + "\n\n✅ APROBAT")
            await approve_user(context, target)
        else:
            await q.answer("Respins ❌")
            db_run("DELETE FROM users WHERE telegram_id=?", (target,))
            await q.edit_message_text(q.message.text + "\n\n❌ RESPINS")
        return

    # --- campioana ---
    if data.startswith("champ:"):
        if db_one("SELECT 1 FROM global_predictions WHERE telegram_id=? AND kind='champion'", (uid,)):
            await q.answer("Ai ales deja, nu se mai poate schimba.", show_alert=True)
            return
        team = ALL_TEAMS[int(data.split(":")[1])]
        db_run("INSERT OR REPLACE INTO global_predictions VALUES(?,?,?)", (uid, "champion", team))
        await q.answer("Campioana aleasă ✅")
        await q.edit_message_text(f"1️⃣ Campioana ta: {team} ✅ (blocată)")
        await prompt_next(context.bot, q.message.chat_id, uid)
        return

    # --- golgheter ---
    if data == "ts:search":
        context.user_data["await"] = "ts_search"
        await q.answer()
        await q.edit_message_text("🔍 Scrie numele jucătorului (ex: salah):")
        return
    if data == "ts:other":
        if db_one("SELECT 1 FROM global_predictions WHERE telegram_id=? AND kind='topscorer'", (uid,)):
            await q.answer("Ai ales deja.", show_alert=True)
            return
        db_run("INSERT OR REPLACE INTO global_predictions VALUES(?,?,?)", (uid, "topscorer", "ALTUL"))
        context.user_data.pop("await", None)
        await q.answer("Ales: ALTUL ✅")
        await q.edit_message_text("2️⃣ Golgheterul tău: ALTUL ✅ (blocat)")
        await prompt_next(context.bot, q.message.chat_id, uid)
        return
    if data.startswith("ts:"):
        if db_one("SELECT 1 FROM global_predictions WHERE telegram_id=? AND kind='topscorer'", (uid,)):
            await q.answer("Ai ales deja.", show_alert=True)
            return
        player = TS_NAMES[int(data.split(":")[1])]
        db_run("INSERT OR REPLACE INTO global_predictions VALUES(?,?,?)", (uid, "topscorer", player))
        context.user_data.pop("await", None)
        await q.answer("Golgheter ales ✅")
        await q.edit_message_text(f"2️⃣ Golgheterul tău: {player} ✅ (blocat)")
        await prompt_next(context.bot, q.message.chat_id, uid)
        return

    # --- câștigătoare grupă ---
    if data.startswith("grp:"):
        _, letter, idx = data.split(":")
        kind = f"group_{letter}"
        if db_one("SELECT 1 FROM global_predictions WHERE telegram_id=? AND kind=?", (uid, kind)):
            await q.answer("Ai ales deja pentru această grupă.", show_alert=True)
            return
        team = GROUPS[letter][int(idx)]
        db_run("INSERT OR REPLACE INTO global_predictions VALUES(?,?,?)", (uid, kind, team))
        await q.answer("Ales ✅")
        await q.edit_message_text(f"Grupa {letter} — alegerea ta: {team} ✅ (blocată)")
        await prompt_next(context.bot, q.message.chat_id, uid)
        return

    # --- deschide tastatura de scor pt un meci ---
    if data.startswith("po:"):
        _, win, mid = data.split(":")
        mid = int(mid)
        m = db_one("SELECT * FROM matches WHERE id=?", (mid,))
        if not m:
            await q.answer("Meci inexistent.", show_alert=True)
            return
        if not m["teams_known"]:
            await q.answer("Echipele nu sunt încă stabilite.", show_alert=True)
            return
        if m["kickoff"] <= now_str():
            await q.answer("Meciul a început, nu mai poți pronostica.", show_alert=True)
            return
        pred = db_one("SELECT pick FROM match_predictions WHERE telegram_id=? AND match_id=?",
                      (uid, mid))
        cur = f"\nPronosticul tău acum: {pred['pick']}" if pred else ""
        await q.answer()
        await q.edit_message_text(
            f"⚽️ {m['home']} – {m['away']}\n{fmt_kick(m['kickoff'])} · {m['venue']}{cur}\n\n"
            "Alege rezultatul (1X2) sau scorul exact (+2p):",
            reply_markup=kb_match_scores(win, mid))
        return

    # --- salvează pronosticul ---
    if data.startswith("sp:"):
        _, win, mid, pick = data.split(":", 3)
        mid = int(mid)
        m = db_one("SELECT * FROM matches WHERE id=?", (mid,))
        if not m or not m["teams_known"]:
            await q.answer("Indisponibil.", show_alert=True)
            return
        if m["kickoff"] <= now_str():
            await q.answer("Meciul a început.", show_alert=True)
            return
        if pick == "alt":
            context.user_data["await"] = f"score:{mid}:{win}"
            await q.answer()
            await q.edit_message_text(
                f"✏️ {m['home']} – {m['away']}\nScrie scorul (ex: 4-2):")
            return
        db_run("INSERT OR REPLACE INTO match_predictions(telegram_id,match_id,pick) VALUES(?,?,?)",
               (uid, mid, pick))
        await q.answer(f"Salvat: {pick} ✅", show_alert=False)
        ms, kb = kb_match_list(win, uid)
        try:
            await q.edit_message_text(
                "✅ Pronostic salvat. Poți schimba până începe meciul.\n\n"
                "Apasă alt meci sau /pronosticurile.", reply_markup=kb)
        except Exception:
            pass
        return

    # --- înapoi la lista de meciuri ---
    if data.startswith("bl:"):
        win = data.split(":")[1]
        ms, kb = kb_match_list(win, uid)
        await q.answer()
        titlu = "📅 MECIURILE DE AZI" if win == "a" else "📅 MECIURILE DE MÂINE"
        await q.edit_message_text(titlu + "\n\nApasă pe un meci ca să pronostichezi.",
                                  reply_markup=kb)
        return

    await q.answer()


# ==============================================================================
#  TEXT LIBER (căutare golgheter / scor custom)
# ==============================================================================

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("await")
    if not state:
        await update.message.reply_text("Folosește meniul de comenzi. /ajutor")
        return
    uid = update.effective_user.id
    text = update.message.text.strip()

    if state == "ts_search":
        found = [n for n in TS_NAMES if norm(text) in norm(n)]
        context.user_data.pop("await", None)
        if not found:
            await update.message.reply_text(
                f"Niciun jucător găsit pentru '{text}'.",
                reply_markup=kb_topscorers_search([]))
        else:
            await update.message.reply_text(
                f"Rezultate pentru '{text}':", reply_markup=kb_topscorers_search(found))
        return

    if state.startswith("score:"):
        _, mid, win = state.split(":")
        mid = int(mid)
        sc = parse_score(text)
        if not sc:
            await update.message.reply_text("Scor invalid. Scrie ceva de forma 4-2:")
            return
        m = db_one("SELECT * FROM matches WHERE id=?", (mid,))
        if not m or m["kickoff"] <= now_str():
            context.user_data.pop("await", None)
            await update.message.reply_text("Meciul nu mai e disponibil.")
            return
        pick = f"{sc[0]}-{sc[1]}"
        db_run("INSERT OR REPLACE INTO match_predictions(telegram_id,match_id,pick) VALUES(?,?,?)",
               (uid, mid, pick))
        context.user_data.pop("await", None)
        await update.message.reply_text(
            f"✅ Pronostic salvat: {m['home']} {pick} {m['away']}.\n"
            "Mai vezi meciuri cu /azi sau /maine.")
        return


# ==============================================================================
#  MEMBRII NOI ÎN GRUP
# ==============================================================================

async def on_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    me = await context.bot.get_me()
    for member in update.message.new_chat_members:
        if member.id == me.id:
            continue
        await update.message.reply_text(
            f"👋 Bun venit, {member.full_name}!\n"
            f"Ca să intri în concurs, scrie-mi în privat: @{me.username} și apasă START. "
            "Apoi adminul te aprobă.")


# ==============================================================================
#  POSTARE ZILNICĂ LA 12:00
# ==============================================================================

async def daily_post(context: ContextTypes.DEFAULT_TYPE):
    gid = get_config("group_chat_id")
    if not gid:
        log.warning("group_chat_id nesetat — rulează /setgrup în grup.")
        return
    gid = int(gid)
    start, end = current_window()
    ms = matches_in(start, end)

    lines = [f"📅 MECIURILE ZILEI ({start.strftime('%d.%m')} {DAILY_HOUR:02d}:00 → "
             f"{end.strftime('%d.%m')} {DAILY_HOUR:02d}:00)\n"]
    if ms:
        for m in ms:
            res = ""
            if m["finished"] and m["result_home"] is not None:
                res = f"  [{m['result_home']}-{m['result_away']}]"
            tag = m["grp"] and f"Gr. {m['grp']}" or m["note"]
            lines.append(f"• {fmt_kick(m['kickoff'])} ({tag}) {m['home']} – {m['away']}{res}")
    else:
        lines.append("Niciun meci azi.")
    lines.append("\n👉 Pariați în privat la bot: /azi")

    try:
        await context.bot.send_message(gid, "\n".join(lines))
        await context.bot.send_message(gid, render_leaderboard())
    except Exception as e:
        log.warning("Eroare la postarea zilnică: %s", e)


# ==============================================================================
#  ERORI
# ==============================================================================

async def on_error(update, context):
    log.error("Eroare la procesare:", exc_info=context.error)


# ==============================================================================
#  MAIN
# ==============================================================================

def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    # utilizator
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("ajutor", cmd_ajutor))
    app.add_handler(CommandHandler("help", cmd_ajutor))
    app.add_handler(CommandHandler("azi", cmd_azi))
    app.add_handler(CommandHandler("maine", cmd_maine))
    app.add_handler(CommandHandler("grupe", cmd_grupe))
    app.add_handler(CommandHandler("pronosticurile", cmd_pronosticurile))
    app.add_handler(CommandHandler("clasament", cmd_clasament))
    app.add_handler(CommandHandler("premii", cmd_premii))
    app.add_handler(CommandHandler("id", cmd_id))

    # admin
    app.add_handler(CommandHandler("setgrup", cmd_setgrup))
    app.add_handler(CommandHandler("aproba", cmd_aproba))
    app.add_handler(CommandHandler("resping", cmd_resping))
    app.add_handler(CommandHandler("useri", cmd_useri))
    app.add_handler(CommandHandler("scor", cmd_scor))
    app.add_handler(CommandHandler("anuleaza_scor", cmd_anuleaza_scor))
    app.add_handler(CommandHandler("grupa", cmd_grupa))
    app.add_handler(CommandHandler("campioana", cmd_campioana))
    app.add_handler(CommandHandler("golgheter", cmd_golgheter))
    app.add_handler(CommandHandler("lista", cmd_lista))
    app.add_handler(CommandHandler("echipe", cmd_echipe))
    app.add_handler(CommandHandler("ora", cmd_ora))
    app.add_handler(CommandHandler("amical", cmd_amical))
    app.add_handler(CommandHandler("adauga_meci", cmd_adauga_meci))
    app.add_handler(CommandHandler("sterge_meci", cmd_sterge_meci))
    app.add_handler(CommandHandler("deschide_grupa", cmd_deschide_grupa))
    app.add_handler(CommandHandler("premii_set", cmd_premii_set))
    app.add_handler(CommandHandler("anunt", cmd_anunt))
    app.add_handler(CommandHandler("bonus", cmd_bonus))
    app.add_handler(CommandHandler("reset_pronosticuri", cmd_reset_pronosticuri))
    app.add_handler(CommandHandler("reset_rezultate", cmd_reset_rezultate))
    app.add_handler(CommandHandler("reset_tot", cmd_reset_tot))

    # butoane + text + membri noi
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(
        filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_members))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, on_text))

    app.add_error_handler(on_error)

    # postare zilnică la 12:00
    app.job_queue.run_daily(
        daily_post, time=dt_time(hour=DAILY_HOUR, minute=DAILY_MINUTE, tzinfo=TZ))

    log.info("Botul pornește...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
