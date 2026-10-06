"""
update_prices.py

Keeps the Banzeen fuel price feed correct. GitHub runs it for you, so no
computer needs to be on.

How a monthly update happens
  1. Bahrain's Fuel Price Committee announces new prices around the 1st of
     each month. They take effect on the 2nd.
  2. On the 1st to 5th of the month this script reads several news sites.
     It publishes only when at least two different sites agree on all four
     prices. One site alone is never trusted.
  3. If the sites cannot be read or do not agree, nothing changes. From the
     3rd of the month the workflow opens a GitHub issue, which emails you.
  4. You can always publish by hand: Actions tab, "Update Fuel Prices",
     "Run workflow", then type the four prices.

Files written
  v1/latest.json        current prices and full history (the app reads this)
  v1/history.json       the monthly history on its own
  v1/<year>/<month>.json one file per month

Only the Python standard library is used.
"""

import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    BAHRAIN = ZoneInfo("Asia/Bahrain")
except Exception:  # pragma: no cover, very old Python only
    BAHRAIN = timezone(timedelta(hours=3))

FUEL_KEYS = ["jayyid91", "mumtaz95", "super98", "diesel"]
FUEL_LABELS = {
    "jayyid91": "Jayyid 91",
    "mumtaz95": "Mumtaz 95",
    "super98": "Super 98",
    "diesel": "Diesel",
}

# Any price outside this range (BHD per litre) is always refused.
MIN_PRICE, MAX_PRICE = 0.05, 2.00
# A new month may not move a price more than this from the current one,
# which stops a misread number from being published.
MAX_MONTHLY_CHANGE = 0.40
# How many different websites must agree before publishing automatically.
MIN_CONFIRMATIONS = 2
# From this day of the month, a missing update is reported as a GitHub issue.
ALERT_FROM_DAY = 3

MONTHS = ["january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december"]

# Article addresses that follow a fixed pattern each month.
TEMPLATE_SOURCES = [
    "https://www.bizbahrain.com/fuel-price-committee-approves-fuel-prices-in-bahrain-for-{month}/",
    "https://www.gulf-insider.com/bahrain-fuel-prices-{month}-{year}/",
    "https://www.bahrain-confidential.com/bahrain-fuel-prices-{month}-{year}/",
    "https://lovin.co/bahrain/en/community/new-fuel-prices-for-{month}-{year}/",
]

# News searches used to discover more articles for the month.
SEARCH_FEEDS = [
    "https://www.bing.com/news/search?format=rss&q={query}",
]
SEARCH_QUERIES = [
    "Bahrain fuel prices {Month} {year}",
    "Fuel Price Committee Bahrain {Month}",
]
MAX_DISCOVERED_ARTICLES = 8

USER_AGENT = "Mozilla/5.0 (compatible; BanzeenPriceBot/1.0)"


# ---------------------------------------------------------------------------
# Reading prices out of an article
# ---------------------------------------------------------------------------

# Names used for each fuel. Each entry is a regex for the fuel name, which
# may include the octane number, for example "jayyid (91)" or "91 jayyid".
FUEL_NAMES = {
    "jayyid91": [r"jayyid\s*\(?\s*91\s*\)?", r"91\s*\(?\s*jayyid\s*\)?", r"jayyid"],
    "mumtaz95": [r"mumtaz\s*\(?\s*95\s*\)?", r"95\s*\(?\s*mumtaz\s*\)?", r"mumtaz"],
    "super98": [r"super\s*\(?\s*98\s*\)?", r"98\s*\(?\s*super\s*\)?"],
    "diesel": [r"diesel"],
}

# A price written in dinars, like "0.225" or "bd0.225". Exactly three
# decimals, so dollar amounts like "$0.60" are never read as a price.
DINAR = r"(?<![\d.$])(0\.\d{3})(?!\d)"
# A price written in fils, like "225 fils".
FILS = r"(?<![\d.])(\d{3})\s*fils"
# Text allowed between a fuel name and its price. No digits, except dollar
# amounts in brackets, which some sites add.
GAP = r"(?:[^\d$]|\$\s?\d+(?:\.\d+)?){0,60}?"


def page_text(raw_html):
    """Turn a web page into plain lower case text."""
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw_html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace(" ", " ")
    text = re.sub(r"\s+", " ", text)
    return text.lower()


def _first_price(text, name_pattern):
    """Price that belongs to one fuel name, or None.

    Two sentence shapes appear in the news:
      "jayyid (91): bd0.225 per litre"          price after the name
      "0.225 dinar per litre for jayyid (91)"   price before the name
    The second shape is checked first because it contains the word "for",
    which makes it unambiguous.
    """
    before = re.search(DINAR + GAP + r"\bfor\s+(?:the\s+)?" + name_pattern, text)
    if before:
        return float(before.group(1))
    after = re.search(name_pattern + GAP + DINAR, text)
    if after:
        return float(after.group(1))
    after_fils = re.search(name_pattern + GAP + FILS, text)
    if after_fils:
        return round(int(after_fils.group(1)) / 1000.0, 3)
    return None


def extract_prices(text):
    """All four prices from one article's text, or None if any is missing."""
    found = {}
    for key in FUEL_KEYS:
        value = None
        for pattern in FUEL_NAMES[key]:
            value = _first_price(text, pattern)
            if value is not None:
                break
        if value is None or not (MIN_PRICE <= value <= MAX_PRICE):
            return None
        found[key] = round(value, 3)
    return found


def mentions_month(text, month_index, year):
    """True when the article talks about the target month and year."""
    name = MONTHS[month_index - 1]
    return name in text and str(year) in text and "bahrain" in text


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def fetch(url, timeout=20):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        final_url = response.geturl()
        body = response.read().decode("utf-8", errors="ignore")
    return final_url, body


def domain_of(url):
    host = urllib.parse.urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def discover_articles(month_index, year):
    """Article links for the month, found through news search feeds."""
    month_name = MONTHS[month_index - 1]
    links = []
    for feed in SEARCH_FEEDS:
        for query in SEARCH_QUERIES:
            q = query.format(Month=month_name.title(), year=year)
            url = feed.format(query=urllib.parse.quote_plus(q))
            try:
                _, body = fetch(url)
            except Exception as error:
                log(f"search failed: {error}")
                continue
            for item in re.findall(r"(?s)<item>(.*?)</item>", body):
                title = re.search(r"(?s)<title>(.*?)</title>", item)
                link = re.search(r"(?s)<link>(.*?)</link>", item)
                if not title or not link:
                    continue
                title_text = html.unescape(title.group(1)).lower()
                if "bahrain" not in title_text:
                    continue
                if "fuel" not in title_text and "petrol" not in title_text:
                    continue
                href = html.unescape(link.group(1).strip())
                # Bing wraps the real address in a "url" parameter.
                params = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                if "url" in params:
                    href = params["url"][0]
                if href not in links:
                    links.append(href)
    return links[:MAX_DISCOVERED_ARTICLES]


def scrape_month(month_index, year, current_prices):
    """Prices for the month when at least two different sites agree."""
    month_name = MONTHS[month_index - 1]
    urls = [u.format(month=month_name, year=year) for u in TEMPLATE_SOURCES]
    for link in discover_articles(month_index, year):
        if link not in urls:
            urls.append(link)

    readings = {}  # domain -> prices
    for url in urls:
        domain = domain_of(url)
        if domain in readings:
            continue
        try:
            final_url, body = fetch(url)
        except Exception as error:
            log(f"{domain}: could not read ({error})")
            continue
        text = page_text(body)
        if not mentions_month(text, month_index, year):
            log(f"{domain}: page is not about {month_name.title()} {year}")
            continue
        prices = extract_prices(text)
        if not prices:
            log(f"{domain}: prices not found on the page")
            continue
        if current_prices and not plausible_change(current_prices, prices):
            log(f"{domain}: prices {prices} changed too much, ignored")
            continue
        log(f"{domain}: {prices}")
        readings[domain_of(final_url)] = prices

    best, votes = None, 0
    for candidate in readings.values():
        count = sum(1 for other in readings.values() if other == candidate)
        if count > votes:
            best, votes = candidate, count
    if votes >= MIN_CONFIRMATIONS:
        return best, sorted(d for d, p in readings.items() if p == best)
    log(f"not confirmed: {votes} site(s) agree, {MIN_CONFIRMATIONS} needed")
    return None, []


def plausible_change(old, new):
    for key in FUEL_KEYS:
        if key not in old or old[key] <= 0:
            continue
        if abs(new[key] - old[key]) / old[key] > MAX_MONTHLY_CHANGE:
            return False
    return True


# ---------------------------------------------------------------------------
# Feed files
# ---------------------------------------------------------------------------

def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def write_json(path, data):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def current_state():
    feed = load_json("v1/latest.json")
    if not feed:
        return None, None, []
    prices = {p["fuelType"]: p["pricePerLiter"] for p in feed.get("prices", [])}
    return prices, feed.get("effectiveDate"), feed.get("history", [])


def upsert_history(history, effective, prices):
    month = effective[:7]
    kept = [h for h in history if h.get("month") != month]
    kept.append({"month": month, "effectiveDate": effective, "prices": prices})
    kept.sort(key=lambda h: h["month"])
    return kept[-36:]


def publish(prices, effective, source, history):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    history = upsert_history(history, effective, prices)
    latest = {
        "apiVersion": 1,
        "country": "BH",
        "currency": "BHD",
        "effectiveDate": effective,
        "updatedAt": now,
        "source": source,
        "disclaimer": "Unofficial feed compiled from public announcements by "
                      "Bahrain's Fuel Price Committee. Not affiliated with any authority.",
        "prices": [{"fuelType": k, "pricePerLiter": prices[k]} for k in FUEL_KEYS],
        "history": history,
    }
    write_json("v1/latest.json", latest)
    write_json("v1/history.json", {
        "apiVersion": 1, "country": "BH", "currency": "BHD",
        "updatedAt": now, "months": history,
    })
    for point in history:
        year, month = point["month"].split("-")
        write_json(f"v1/{year}/{month}.json", {
            "apiVersion": 1, "country": "BH", "currency": "BHD", **point,
        })


# ---------------------------------------------------------------------------
# Hand entered prices from the "Run workflow" form
# ---------------------------------------------------------------------------

def manual_input(today):
    """Prices typed into the workflow form, or None when the form is empty."""
    raw = {key: os.environ.get("IN_" + key.upper(), "").strip() for key in FUEL_KEYS}
    if not any(raw.values()):
        return None
    prices = {}
    for key, value in raw.items():
        value = value.replace(",", ".")
        try:
            number = float(value)
        except ValueError:
            fail(f"{FUEL_LABELS[key]}: '{value}' is not a number. Type it like 0.225")
        if number >= 1:  # someone typed fils, for example 225
            number = number / 1000.0
        number = round(number, 3)
        if not (MIN_PRICE <= number <= MAX_PRICE):
            fail(f"{FUEL_LABELS[key]}: {number} BHD does not look like a real price")
        prices[key] = number

    effective = os.environ.get("IN_EFFECTIVE_DATE", "").strip()
    if effective:
        try:
            datetime.strptime(effective, "%Y-%m-%d")
        except ValueError:
            fail(f"Start date '{effective}' must look like 2026-10-02")
    else:
        effective = today.replace(day=2).isoformat()
    return prices, effective


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def log(message):
    print(message, file=sys.stderr)


def fail(message):
    log("ERROR: " + message)
    sys.exit(1)


def set_output(name, value):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def write_attention_note(month_name, year):
    title = f"Please confirm Bahrain fuel prices for {month_name.title()} {year}"
    body = (
        f"The price robot could not confirm the {month_name.title()} {year} prices "
        "from two news sites, so the app still shows last month's prices.\n\n"
        "To publish them yourself:\n"
        "1. Open the Actions tab of this repository.\n"
        "2. Choose \"Update Fuel Prices\" and press \"Run workflow\".\n"
        "3. Type the four prices, for example 0.225, and press the green button.\n\n"
        "The app picks up the new prices within a few hours. You can close this "
        "issue once the prices are published."
    )
    with open("attention.md", "w", encoding="utf-8") as handle:
        handle.write(body + "\n")
    set_output("needs_attention", "true")
    set_output("issue_title", title)


# ---------------------------------------------------------------------------

def main():
    today = datetime.now(BAHRAIN).date()
    prices_now, effective_now, history = current_state()

    manual = manual_input(today)
    if manual:
        prices, effective = manual
        publish(prices, effective, "manual", history)
        print(f"Published by hand: {prices}, starting {effective}")
        return

    target = today.replace(day=1)
    if effective_now and effective_now[:7] >= target.isoformat()[:7]:
        print(f"Already up to date for {target:%B %Y} (effective {effective_now}).")
        return

    prices, confirmed_by = scrape_month(target.month, target.year, prices_now)
    if prices:
        effective = target.replace(day=2).isoformat()
        publish(prices, effective, "auto", history)
        print(f"Published {prices}, starting {effective}, "
              f"confirmed by {', '.join(confirmed_by)}")
        return

    print(f"No confirmed prices for {target:%B %Y} yet. Nothing changed.")
    if today.day >= ALERT_FROM_DAY:
        write_attention_note(MONTHS[target.month - 1], target.year)


if __name__ == "__main__":
    main()
