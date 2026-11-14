"""Read public cinema listings. No login, seat selection or checkout."""
import json
import os
import re
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
BMS = "https://in.bookmyshow.com"
DIST = "https://www.district.in"
DIRECTORY = DIST + "/movies/cinemas-in-hyderabad"
VENUES = {
    "ALUC": ("ALLU Cinemas, Kokapet", "allu-cinemas-kokapet",
            r"\ballu cinemas?\b", r"kokapet|allu studios|narsingi"),
    "PRHN": ("Prasads Multiplex, Khairtabad", "prasads-multiplex-hyderabad",
            r"\bprasad['']?s?\s+(multiplex|cinemas?|imax|pcx)\b",
            r"ntr (marg|gardens)|khair[at]*bad|imax road|necklace road"),
}
DAY = re.compile(r'button "(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s*(\d{1,2})\s*([A-Za-z]{3})"(.*)')
CLOCK = r"\d{1,2}:\d{2} [AP]M"


def public_url(base, href):
    url = urljoin(base, href)
    if urlsplit(url).netloc != urlsplit(base).netloc or urlsplit(url).scheme != "https":
        raise ValueError("Unexpected booking link domain")
    return url


def _relay_url(target_url):
    """
    Optional single-request relay when direct GitHub runner IP gets HTTP 403.
    Supports:
      - CF_WORKER_RELAY_URL (your own free Cloudflare Worker, e.g. https://my-worker.workers.dev/?url=)
      - SCRAPERAPI_KEY (free 1,000 credits/month tier, 1 request per page)
      - SCRAPEDO_TOKEN (free 1,000 credits/month tier, 1 request per page)
    """
    worker_url = os.environ.get("CF_WORKER_RELAY_URL", "").strip()
    if worker_url:
        sep = "&" if "?" in worker_url else "?"
        return f"{worker_url}{sep}{urlencode({'url': target_url})}"
    scraper_key = os.environ.get("SCRAPERAPI_KEY", "").strip()
    if scraper_key:
        return f"https://api.scraperapi.com/?{urlencode({'api_key': scraper_key, 'url': target_url, 'country_code': 'in'})}"
    scrapedo_token = os.environ.get("SCRAPEDO_TOKEN", "").strip()
    if scrapedo_token:
        return f"https://api.scrape.do/?{urlencode({'token': scrapedo_token, 'url': target_url})}"
    return None


def visit(page, url):
    response = page.goto(url, wait_until="domcontentloaded", timeout=35000)
    status = response.status if response else 0
    body_head = ""
    try:
        body_head = page.title() + " " + page.inner_text("body")[:400]
    except Exception:
        pass

    is_blocked = (
        response is None
        or status >= 400
        or "Just a moment" in body_head
        or "Attention Required" in body_head
        or "Access Denied" in body_head
    )

    if is_blocked:
        relay = _relay_url(url)
        if relay:
            relay_resp = page.goto(relay, wait_until="domcontentloaded", timeout=45000)
            if relay_resp is not None and relay_resp.status < 400:
                html = page.content()
                page.goto("about:blank")
                page.set_content(html, wait_until="domcontentloaded")
                return
        raise RuntimeError(f"Page unavailable (HTTP {status if response else 'unknown'})")

    public_url(url, page.url)


def line_value(raw):
    raw = raw.strip()
    if raw.startswith('"'):
        try:
            return json.loads(raw)
        except ValueError:
            pass
    return raw.strip('"')


def parse_rows(snapshot, platform, venue, day, url):
    """Use movie/link groups from the visible accessibility tree."""
    title = lang = fmt = ""
    label = ""
    district_title_pending = False
    rows = []
    lines = snapshot.splitlines()
    for i, line in enumerate(lines):
        entry = line.strip().lstrip("- ").strip('"')
        link = re.search(r'link "(.*)"', entry)
        if link:
            label = link[1]
        if entry.startswith("/url:"):
            href = line_value(entry[5:])
            if platform == "BookMyShow" and "/movies/" in href and "/ET" in href:
                title = re.sub(r"\s*\((?:U|A)[^)]*\)$", "", label)
                lang = fmt = ""
            elif platform == "BookMyShow" and "languages=" in href:
                lang = label
            elif platform == "District" and re.search(r"-mV\d+", href, re.I):
                title = lang = fmt = ""
                district_title_pending = True
                continue
        val = line_value(entry.partition(":")[2]) if entry.startswith("generic:") else ""
        if platform == "District" and entry.startswith("img '") and "' logo" not in entry:
            title = entry.split("'")[1]
        if platform == "District" and val:
            if district_title_pending and "|" not in val:
                title = val
                district_title_pending = False
            if "|" in val and re.match(r"^(?:U|A)", val):
                lang = val.split("|", 1)[1].strip().split(" ")[0]
            elif i+1 < len(lines) and lines[i+1].strip().startswith("- list:"):
                lang = val
        if platform == "BookMyShow" and val.startswith(","):
            fmt = val.lstrip(", ")
        pattern = r"^button 'Book ("+CLOCK+r")'" if platform == "BookMyShow" else r"^button '("+CLOCK+r")([^']*)'"
        match = re.match(pattern, entry)
        if not match:
            continue
        if not title or not lang:
            raise ValueError("Showtime without a verified film and language")
        stamp = datetime.strptime(day.isoformat()+" "+match[1], "%Y-%m-%d %I:%M %p").replace(tzinfo=IST)
        indent = len(line) - len(line.lstrip())
        children = []
        for child in lines[i+1:]:
            if len(child) - len(child.lstrip()) <= indent:
                break
            children.append(child.strip())
        bookable = "[disabled]" not in entry
        target = url
        if platform == "District":
            seats = [x.partition("/url:")[2].strip() for x in children if "/url:" in x and "/movies/seat-layout/" in x]
            bookable = bookable and bool(seats)
            if seats:
                target = public_url(DIST, seats[0])
            detail = match[2].strip()
            pieces = re.findall(r'\b(?:2D|3D|4DX|IMAX|PCX|DOLBY CINEMA)\b', detail, re.I)
            format_name = " ".join(pieces)
        else:
            labels = [line_value(x.partition("generic:")[2]) for x in children if "generic:" in x]
            detail = " ".join(x for x in labels if x != match[1])
            format_name = fmt
        rows.append(dict(venue=venue, film=title, language=lang, format=format_name,
                        screen=detail, at=stamp.isoformat(), url=target, open=bookable,
                        platform=platform))
    return rows


def bms_calendar(page, venue, today):
    base = f"{BMS}/cinemas/hyderabad/{VENUES[venue][1]}/buytickets/{venue}/"
    visit(page, base + today.strftime("%Y%m%d"))
    page.get_by_role("button", name=re.compile(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s*\d")).first.wait_for()
    snap = page.get_by_role("main").aria_snapshot()
    dates = {}
    for day_num, month_abbr, state in DAY.findall(snap):
        val = f"{day_num} {month_abbr}"
        for year in [today.year, today.year + 1]:
            try:
                day = datetime.strptime(f"{val} {year}", "%d %b %Y").date()
            except ValueError:
                continue
            if today <= day < today + timedelta(days=365):
                dates[day] = "[disabled]" not in state
                break
    if not dates or not re.search(VENUES[venue][3], snap, re.I):
        raise ValueError("BookMyShow calendar or venue identity unverified")
    return {day: base + day.strftime("%Y%m%d") if active else None for day, active in dates.items()}


def bms_rows(page, venue, day, url):
    if page.url != url:
        visit(page, url)
    selected = day.strftime("%a %d %b").replace(" 0", " ")
    pressed = page.get_by_role("button", name=re.compile(r"^"+selected.replace(" ", r"\s*0?",1).replace(" ", r"\s*")+r"$",re.I), pressed=True)
    pressed.first.wait_for(timeout=10000)
    grid = page.get_by_role("grid").first
    grid.wait_for()
    records = {}
    for step in range(100):
        snapshot = grid.aria_snapshot()
        for row in parse_rows(snapshot, "BookMyShow", venue, day, url):
            key = (row["film"], row["language"], row["format"], row["at"], row["screen"])
            records[key] = row
        moved = grid.evaluate("""root => {
            const nodes = [root, ...root.querySelectorAll('*')];
            for(let p=root.parentElement;p;p=p.parentElement) nodes.push(p);
            const scroller=nodes.find(e=>['auto','scroll'].includes(getComputedStyle(e).overflowY)
                && e.clientHeight>0 && e.scrollHeight>e.clientHeight+2) || document.scrollingElement;
            const before=scroller.scrollTop;
            scroller.scrollTop += Math.max(1, Math.floor(scroller.clientHeight*0.8));
            return scroller.scrollTop>before+1;
        }""")
        if not moved:
            break
        page.wait_for_timeout(180)
    else:
        raise ValueError("Schedule did not finish loading")
    if not records:
        raise ValueError("No recognizable booking controls")
    return list(records.values())


def district_directory(page):
    visit(page, DIRECTORY)
    links = page.locator("a[href*='-CD']")
    links.first.wait_for(state="attached")
    found = {}
    for link in links.evaluate_all("els=>els.map(e=>[e.innerText,e.href])"):
        name, url = link
        for venue, (_, _, pattern, _) in VENUES.items():
            if re.search(pattern, name, re.I):
                found[venue] = public_url(DIST, url)
    return found


def district_calendar(page, venue, url, today):
    visit(page, url)
    main = page.get_by_role("main")
    main.locator("a[href*='fromdate=']").first.wait_for(state="attached")
    heading = " ".join(main.get_by_role("heading", level=2).all_text_contents())
    if not re.search(VENUES[venue][2], heading, re.I) or not re.search(VENUES[venue][3], main.inner_text(), re.I):
        raise ValueError("District venue name or address did not match")
    dates = {}
    for href in main.locator("a[href*='fromdate=']").evaluate_all("els=>els.map(e=>e.href)"):
        day = date.fromisoformat(parse_qs(urlsplit(href).query)["fromdate"][0])
        if day >= today:
            dates[day] = public_url(DIST, href)
    if not dates:
        raise ValueError("No recognizable District dates")
    return dates


def district_rows(page, venue, day, url):
    visit(page, url)
    if parse_qs(urlsplit(page.url).query).get("fromdate") != [day.isoformat()]:
        raise ValueError("District redirected away from requested date")
    main = page.get_by_role("main")
    main.get_by_role("button", name=re.compile(r"^"+CLOCK)).first.wait_for(timeout=7000)
    rows = parse_rows(main.aria_snapshot(), "District", venue, day, page.url)
    if not rows:
        raise ValueError("No recognizable District show controls")
    return rows