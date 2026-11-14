import argparse
import json
import os
import re
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright

from sources import IST, VENUES, bms_calendar, bms_rows, district_calendar, district_directory, district_rows

STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = { runtime: {} };
Object.defineProperty(navigator, 'languages', { get: () => ['en-IN', 'en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
"""

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def log(message):
    print(datetime.now(IST).isoformat(timespec="seconds"), message, flush=True)


def database(path):
    db = sqlite3.connect(path, timeout=20)
    db.executescript("""
    CREATE TABLE IF NOT EXISTS sources (source TEXT PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS days (source TEXT, day TEXT, baseline INTEGER, PRIMARY KEY(source,day));
    CREATE TABLE IF NOT EXISTS inventory (source TEXT, day TEXT, key TEXT, active INTEGER,
        PRIMARY KEY(source,day,key));
    CREATE TABLE IF NOT EXISTS events (key TEXT PRIMARY KEY, at TEXT, payload TEXT, status TEXT);
    CREATE TABLE IF NOT EXISTS health (scope TEXT PRIMARY KEY, failures INTEGER);
    """)
    return db


def calendar(db, source, dates):
    with db:
        first = not db.execute("SELECT 1 FROM sources WHERE source=?", (source,)).fetchone()
        for day, url in dates.items():
            db.execute("INSERT OR IGNORE INTO days VALUES(?,?,?)",(source,str(day),int(first and bool(url))))
        db.execute("INSERT OR IGNORE INTO sources VALUES(?)",(source,))


def norm(s):
    return re.sub(r"[^\w]+","",s.casefold())


def record(db, source, day, rows):
    day = str(day)
    with db:
        baseline, = db.execute("SELECT baseline FROM days WHERE source=? AND day=?", (source, day)).fetchone()
        explicitly_closed = set()
        db.execute("UPDATE inventory SET active=0 WHERE source=? AND day=?", (source, day))
        for r in rows:
            detail = (r["format"]+ " "+r["screen"]).upper()
            fmt = " ".join(re.findall(r"\b(?:2D|3D|IMAX|PCX|DOLBY CINEMA|4DX)\b",detail))
            fmt = " ".join(sorted(set(fmt.split())))
            key = "|".join([r["venue"],norm(r["film"]),norm(r["language"]),r["at"],fmt])
            db.execute("INSERT OR REPLACE INTO inventory VALUES(?,?,?,?)",(source,day,key,r["open"]))
            if not r["open"]:
                explicitly_closed.add(key)
                continue
            if datetime.fromisoformat(r["at"]) <= datetime.now(IST):
                continue
            old = db.execute("SELECT payload,status FROM events WHERE key=?",(key,)).fetchone()
            if old:
                payload = json.loads(old[0])
                payload["links"][r["platform"]] = r["url"]
                db.execute("UPDATE events SET payload=?, status=CASE WHEN status='closed' THEN 'pending' ELSE status END WHERE key=?",
                    (json.dumps(payload),key))
            else:
                payload = dict(r,links={r["platform"]:r["url"]})
                db.execute("INSERT INTO events VALUES(?,?,?,?)",
                    (key,r["at"],json.dumps(payload),"baseline" if baseline else "pending"))
        for key in explicitly_closed:
            if not db.execute("SELECT 1 FROM inventory WHERE key=? AND active=1",(key,)).fetchone():
                db.execute("UPDATE events SET status='closed' WHERE key=? AND status='baseline'",(key,))
        db.execute("UPDATE days SET baseline=0 WHERE source=? AND day=?", (source,day))


def telegram(method, data):
    token = os.environ.get("TELEGRAM_BOT_TOKEN","").strip()
    if not re.fullmatch(r"\d+:[\w-]+",token):
        raise ValueError("Set TELEGRAM_BOT_TOKEN in .env")
    req = Request("https://api.telegram.org/bot"+token+"/"+method,
        data=json.dumps(data).encode(),headers={"Content-Type":"application/json"})
    try:
        with urlopen(req,timeout=20) as response:
            answer = json.load(response)
    except HTTPError as e:
        raise RuntimeError(f"Telegram returned HTTP {e.code}") from None
    except (URLError, TimeoutError):
        raise RuntimeError("Telegram connection failed") from None
    if not answer.get("ok"):
        raise RuntimeError("Telegram rejected the request")
    return answer["result"]


def send(text):
    chat = os.environ.get("TELEGRAM_CHAT_ID","").strip()
    if not chat:
        raise ValueError("Set TELEGRAM_CHAT_ID in .env")
    telegram("sendMessage",{"chat_id":chat,"text":text,"link_preview_options":{"is_disabled":True}})


def deliver(db, notify=send):
    batches = defaultdict(list)
    for key, at, raw in db.execute("SELECT key,at,payload FROM events WHERE status='pending' ORDER BY at,key"):
        if datetime.fromisoformat(at) <= datetime.now(IST):
            with db:
                db.execute("UPDATE events SET status='expired' WHERE key=?", (key,))
            continue
        if not db.execute("SELECT 1 FROM inventory WHERE key=? AND active=1",(key,)).fetchone():
            continue
        r = json.loads(raw)
        batches[(r["venue"],r["film"],at[:10])].append((key,r))
    for (venue, film, day), items in batches.items():
        priority = re.search(r"dune|avengers|spider.man|marvel|fantastic four|x.men|black panther",film,re.I)
        head = f"Showtime Bell · {'★ ' if priority else ''}{film} — {VENUES[venue][0]}\n{day} · IST"
        lines, keys = [], []
        def flush():
            notify(head+"\n"+"\n".join(lines))
            with db:
                db.executemany("UPDATE events SET status='sent' WHERE key=?",[(k,) for k in keys])
        for key,r in items:
            detail = " · ".join(dict.fromkeys(filter(None,[r["language"],r["format"],r["screen"]])))
            line = datetime.fromisoformat(r["at"]).strftime("%I:%M %p")+" · "+detail+"\n"+"\n".join(
                f"{p}: {url}" for p,url in r["links"].items())
            if lines and len(head)+sum(map(len,lines))+len(line)+len(lines)>3500:
                flush()
                lines,keys=[],[]
            lines.append(line)
            keys.append(key)
        flush()


def health(db,scope,errors,live):
    if not live:
        return
    row = db.execute("SELECT failures FROM health WHERE scope=?",(scope,)).fetchone()
    count = (row[0] if row else 0)+1 if errors else 0
    if count == 3:
        send(f"Coverage update — {scope}\nThree checks could not verify all listings. Last good data is retained. Check the watcher logs.")
    with db:
        db.execute("INSERT OR REPLACE INTO health VALUES(?,?)",(scope,count))


def cycle(browser,db,inspect=False):
    today = datetime.now(IST).date()
    context = browser.new_context(
        timezone_id="Asia/Kolkata",
        locale="en-IN",
        user_agent=USER_AGENT,
        viewport={"width": 1366, "height": 900},
        extra_http_headers={
            "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
            "Sec-Ch-Ua": '"Chromium";v="131", "Not_A Brand";v="24"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Linux"',
        },
    )
    context.add_init_script(STEALTH_INIT_SCRIPT)
    page = context.new_page()
    page.set_default_timeout(15000)
    failures = 0
    try:
        try:
            district = district_directory(page)
            log("District directory matched: "+(", ".join(district) or "neither target cinema"))
        except Exception as e:
            district = None
            log("District directory unavailable: "+str(e).splitlines()[0])
        for platform in ["BookMyShow","District"]:
            for venue in VENUES:
                scope = platform+"/"+venue
                errors = False
                try:
                    if platform == "District":
                        if district is None:
                            raise ValueError("Directory check failed")
                        if venue not in district:
                            if not inspect:
                                calendar(db,scope,{})
                            health(db,scope,False,not inspect)
                            continue
                        dates = district_calendar(page,venue,district[venue],today)
                    else:
                        dates = bms_calendar(page,venue,today)
                    if not inspect:
                        calendar(db,scope,dates)
                    for day,url in dates.items():
                        if url is None:
                            if not inspect:
                                record(db,scope,day,[])
                            continue
                        try:
                            read = bms_rows if platform == "BookMyShow" else district_rows
                            rows = read(page,venue,day,url)
                            log(f"{scope} {day}: {sum(r['open'] for r in rows)} enabled shows")
                            if inspect:
                                for r in rows:
                                    print(json.dumps(r,ensure_ascii=False))
                            else:
                                record(db,scope,day,rows)
                        except Exception as e:
                            errors = True
                            log(f"{scope} {day}: unknown - "+str(e).splitlines()[0])
                except Exception as e:
                    errors = True
                    log(f"{scope}: unknown - "+str(e).splitlines()[0])
                failures += int(errors)
                health(db,scope,errors,not inspect)
    finally:
        context.close()
    if not inspect:
        deliver(db)
        with db:
            cutoff = today.isoformat()
            db.execute("DELETE FROM events WHERE substr(at,1,10) < ?",(cutoff,))
            for table in ("inventory","days"):
                db.execute(f"DELETE FROM {table} WHERE day < ?",(cutoff,))
    return failures


def state_path(directory):
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "watch.sqlite3"
    legacy = directory / "showtime.db"
    if not target.exists() and legacy.exists():
        # Copy the existing history, including any committed WAL transactions.
        with sqlite3.connect(legacy) as old, sqlite3.connect(target) as new:
            old.backup(new)
    return target


def browser_options():
    options = {
        "headless": os.environ.get("BROWSER_HEADLESS", "true").lower() != "false",
    }
    executable = os.environ.get("CHROMIUM_PATH", "").strip()
    if executable:
        options["executable_path"] = executable
    else:
        options["channel"] = "chromium"
    proxy_server = (
        os.environ.get("BROWSER_PROXY_SERVER", "").strip()
        or os.environ.get("HTTPS_PROXY", "").strip()
        or os.environ.get("HTTP_PROXY", "").strip()
    )
    if proxy_server:
        proxy = {"server": proxy_server}
        username = os.environ.get("BROWSER_PROXY_USERNAME", "").strip()
        password = os.environ.get("BROWSER_PROXY_PASSWORD", "").strip()
        if username:
            proxy["username"] = username
        if password:
            proxy["password"] = password
        options["proxy"] = proxy
    return options


def main():
    parser = argparse.ArgumentParser(description="Showtime Bell — Hyderabad cinema ticket alerts")
    parser.add_argument("command",choices=["watch","inspect","once","test-alert","chat-id"])
    mode = parser.parse_args().command
    if mode=="test-alert":
        send("Showtime Bell delivery test — Hyderabad cinema alerts are connected. This message contains no ticket opening.")
        print("Telegram accepted your test. Check your phone.")
        return 0
    if mode=="chat-id":
        updates = telegram("getUpdates",{"limit":100})
        for chat in sorted({u["message"]["chat"]["id"] for u in updates if u.get("message",{}).get("chat",{}).get("type")=="private"}):
            print(chat)
        if not updates:
            print("Open your bot in Telegram, send /start, then retry.")
        return 0
    inspect = mode=="inspect"
    state_dir = Path(os.environ.get("STATE_DIR", "/data" if os.path.exists("/data") else "."))
    db_path = state_path(state_dir)
    db = database(db_path)

    launch_kwargs = browser_options()
    launch_kwargs.setdefault(
        "args",
        [
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
        ],
    )
    poll_seconds = float(os.environ.get("POLL_SECONDS", "180") or "180")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**launch_kwargs)
        try:
            if inspect:
                failures = cycle(browser,db,inspect=True)
                return 1 if failures else 0
            if mode=="once":
                failures = cycle(browser,db,inspect=False)
                return 1 if failures else 0
            log("Showtime Bell started. Press Ctrl+C to stop.")
            while True:
                start = time.time()
                cycle(browser,db)
                elapsed = time.time() - start
                time.sleep(max(1.0, poll_seconds - elapsed))
        finally:
            browser.close()
            db.close()


if __name__ == "__main__":
    sys.exit(main())