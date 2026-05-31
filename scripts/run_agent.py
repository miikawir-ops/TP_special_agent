"""
TP Special Agent — run_agent.py
Fetches transfer pricing & tax news, classifies with Gemini Flash,
maintains a 7-day rolling archive, generates Signal of the Day,
and renders tp_report.html for GitHub Pages.
"""
 
import os
import sys
import json
import time
import hashlib
import logging
import calendar
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
 
import feedparser
import requests
import pytz
from bs4 import BeautifulSoup
from dateutil import parser as dateparser
import google.generativeai as genai
from jinja2 import Environment, FileSystemLoader
 
HELSINKI = pytz.timezone("Europe/Helsinki")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("tp-agent")
 
GEMINI_API_KEY  = os.environ.get("GEMINI_API_KEY", "")
LOOKBACK_HOURS  = 48
ARCHIVE_DAYS    = 7
MAX_ITEMS       = 40
REQUEST_TIMEOUT = 15
 
REPO_ROOT    = Path(__file__).parent.parent
ARCHIVE_FILE = REPO_ROOT / "archive.json"
 
PUBLISHER_MAP = {
    "bloombergtax.com":           "Bloomberg Tax",
    "bloomberg.com":              "Bloomberg Tax",
    "reuters.com":                "Reuters",
    "ft.com":                     "Financial Times",
    "law360.com":                 "Law360",
    "taxnotes.com":               "Tax Notes",
    "mnetax.com":                 "MNE Tax",
    "internationaltaxreview.com": "Int'l Tax Review",
    "taxfoundation.org":          "Tax Foundation",
    "taxjustice.net":             "Tax Justice Network",
    "oecd.org":                   "OECD",
    "taxguru.in":                 "TaxGuru India",
    "transferpricingasia.com":    "TP Asia",
    "ibfd.org":                   "IBFD",
    "pwc.com":                    "PwC Tax",
    "deloitte.com":               "Deloitte Tax",
    "ey.com":                     "EY Tax",
    "kpmg.com":                   "KPMG Tax",
    "bdo.com":                    "BDO",
    "bdo.global":                 "BDO",
    "taxobservatory.eu":          "EU Tax Observatory",
    "eur-lex.europa.eu":          "EUR-Lex",
    "curia.europa.eu":            "CJEU",
    "ustaxcourt.gov":             "US Tax Court",
    "polity.org.za":              "Polity (SA)",
    "taxanalysts.org":            "Tax Analysts",
    "kluwertaxlawblog.com":       "Kluwer Tax Blog",
    "roedl.com":                  "RÖDL",
    "tpnews.ca":                  "TP News",
    "bakermckenzie.com":          "Baker McKenzie",
    "linklaters.com":             "Linklaters",
    "freshfields.com":            "Freshfields",
    "skadden.com":                "Skadden",
    "cliffordchance.com":         "Clifford Chance",
    "allenovery.com":             "Allen & Overy",
    "whitecase.com":              "White & Case",
}
 
TP_KEYWORDS = [
    # Court & regulatory signals
    "tax court", "tax tribunal", "tax ruling", "court decision",
    "tax case", "tax dispute", "judgment", "appellant",
    "new regulation", "new guidance", "new legislation",
    "OECD report", "consultation document",
 
    # Priority TP topic keywords
    "intangibles", "intangible property", "DEMPE",
    "business restructuring", "supply chain restructur",
    "control over risk", "assumption of risk",
    "permanent establishment", "PE attribution", "dependent agent",
    "valuation", "business valuation", "IP valuation",
    "arm's length", "arm's-length", "arms length",
 
    # General TP relevance
    "transfer pricing", "transfer price",
    "related party", "intra-group", "intercompany",
    "controlled transaction", "comparable",
    "pillar one", "pillar 1", "amount B", "amount A",
    "reallocation of profits",
    "BEPS", "OECD", "tax treaty", "double taxation",
    "country-by-country", "CbCR", "CbC",
    "advance pricing", "APA",
    "mutual agreement", "MAP",
    "DAC6", "DAC7", "ATAD",
    "diverted profits", "controlled foreign",
    "thin capitalisation", "thin capitalization",
    "profit shifting", "base erosion",
    "withholding tax", "royalt",
    "international tax", "corporate tax",
    "state aid", "tax avoidance",
]
 
LENSES = [
    "Intangibles & IP",
    "Business restructuring",
    "PE & attribution",
    "Court decisions",
    "Finance & treasury",
    "AI & digital economy",
    "Dispute resolution / MAP",
    "Amount B & nexus",
    "Documentation & CbCR",
    "General TP",
]
 
REGIONS = ["Global", "EU", "US", "APAC", "Nordic", "Other"]
 
RSS_FEEDS = [
    # ── Google News — core TP queries ────────────────────────────────────────
    {
        "name": "Google News — Transfer Pricing",
        "url": "https://news.google.com/rss/search?q=%22transfer+pricing%22&hl=en-US&gl=US&ceid=US:en",
        "open": True,
    },
    {
        "name": "Google News — TP Court",
        "url": "https://news.google.com/rss/search?q=%22transfer+pricing%22+court+ruling&hl=en-US&gl=US&ceid=US:en",
        "open": True,
    },
    {
        "name": "Google News — Intangibles TP",
        "url": "https://news.google.com/rss/search?q=%22transfer+pricing%22+intangibles&hl=en-US&gl=US&ceid=US:en",
        "open": True,
    },
    {
        "name": "Google News — PE Attribution",
        "url": "https://news.google.com/rss/search?q=%22permanent+establishment%22+%22transfer+pricing%22&hl=en-US&gl=US&ceid=US:en",
        "open": True,
    },
    {
        "name": "Google News — OECD Tax",
        "url": "https://news.google.com/rss/search?q=OECD+%22transfer+pricing%22&hl=en-US&gl=US&ceid=US:en",
        "open": True,
    },
 
    # ── Specialist feeds ─────────────────────────────────────────────────────
    {
        "name": "Tax Foundation",
        "url": "https://taxfoundation.org/feed/",
        "open": True,
    },
    {
        "name": "Tax Justice Network",
        "url": "https://taxjustice.net/feed/",
        "open": True,
    },
    {
        "name": "EU Tax Observatory",
        "url": "https://www.taxobservatory.eu/feed/",
        "open": True,
    },
    {
        "name": "TaxGuru India",
        "url": "https://taxguru.in/category/income-tax/feed/",
        "open": True,
    },
    {
        "name": "TP Asia",
        "url": "https://www.transferpricingasia.com/feed/",
        "open": True,
    },
    {
        "name": "MNE Tax",
        "url": "https://mnetax.com/feed",
        "open": True,
    },
 
    # ── Big Four ─────────────────────────────────────────────────────────────
    {
        "name": "Google News — KPMG TP",
        "url": "https://news.google.com/rss/search?q=KPMG+%22transfer+pricing%22&hl=en-US&gl=US&ceid=US:en",
        "open": True,
    },
    {
        "name": "Google News — PwC TP",
        "url": "https://news.google.com/rss/search?q=PwC+%22transfer+pricing%22&hl=en-US&gl=US&ceid=US:en",
        "open": True,
    },
    {
        "name": "Google News — Deloitte TP",
        "url": "https://news.google.com/rss/search?q=Deloitte+%22transfer+pricing%22&hl=en-US&gl=US&ceid=US:en",
        "open": True,
    },
    {
        "name": "Google News — EY TP",
        "url": "https://news.google.com/rss/search?q=%22Ernst+Young%22+%22transfer+pricing%22&hl=en-US&gl=US&ceid=US:en",
        "open": True,
    },
 
    # ── BDO ──────────────────────────────────────────────────────────────────
    {
        "name": "Google News — BDO TP",
        "url": "https://news.google.com/rss/search?q=BDO+%22transfer+pricing%22&hl=en-US&gl=US&ceid=US:en",
        "open": True,
    },
 
    # ── Law firms ────────────────────────────────────────────────────────────
    {
        "name": "Google News — Baker McKenzie TP",
        "url": "https://news.google.com/rss/search?q=%22Baker+McKenzie%22+%22transfer+pricing%22&hl=en-US&gl=US&ceid=US:en",
        "open": True,
    },
    {
        "name": "Google News — Linklaters TP",
        "url": "https://news.google.com/rss/search?q=Linklaters+%22transfer+pricing%22&hl=en-US&gl=US&ceid=US:en",
        "open": True,
    },
    {
        "name": "Google News — Freshfields TP",
        "url": "https://news.google.com/rss/search?q=Freshfields+%22transfer+pricing%22&hl=en-US&gl=US&ceid=US:en",
        "open": True,
    },
    {
        "name": "Google News — Skadden TP",
        "url": "https://news.google.com/rss/search?q=Skadden+%22transfer+pricing%22&hl=en-US&gl=US&ceid=US:en",
        "open": True,
    },
    {
        "name": "Google News — Clifford Chance TP",
        "url": "https://news.google.com/rss/search?q=%22Clifford+Chance%22+%22transfer+pricing%22&hl=en-US&gl=US&ceid=US:en",
        "open": True,
    },
    {
        "name": "Google News — White & Case TP",
        "url": "https://news.google.com/rss/search?q=%22White+%26+Case%22+%22transfer+pricing%22&hl=en-US&gl=US&ceid=US:en",
        "open": True,
    },
 
    # ── EY BorderCrossings podcast ────────────────────────────────────────────
    {
        "name": "EY BorderCrossings Podcast",
        "url": "https://feeds.libsyn.com/507593/rss",
        "open": True,
    },
 
    # ── OECD direct feed ─────────────────────────────────────────────────────
    {
        "name": "OECD Tax",
        "url": "https://www.oecd.org/tax/rss.xml",
        "open": True,
    },
]
 
# ── Helpers ───────────────────────────────────────────────────────────────────
 
def extract_publisher(title: str, url: str, feed_name: str, entry=None) -> str:
    if entry is not None:
        src = getattr(entry, "source", None)
        if src:
            src_title = getattr(src, "title", "")
            if src_title and len(src_title) < 60:
                return src_title
    if " - " in title:
        suffix = title.rsplit(" - ", 1)[-1].strip()
        for domain, name in PUBLISHER_MAP.items():
            if domain in suffix.lower():
                return name
        if 2 < len(suffix) < 50 and "." not in suffix and suffix[0].isupper():
            return suffix
    for domain, name in PUBLISHER_MAP.items():
        if domain in url.lower():
            return name
    if feed_name.startswith("Google News — "):
        return feed_name.replace("Google News — ", "GN: ")
    return feed_name
 
def item_id(url):
    return hashlib.md5(url.encode()).hexdigest()[:12]
 
def parse_dt(entry):
    struct = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if struct:
        try:
            return datetime.fromtimestamp(calendar.timegm(struct), tz=timezone.utc)
        except Exception:
            pass
    date_str = getattr(entry, "published", "") or getattr(entry, "updated", "")
    if date_str:
        try:
            dt = dateparser.parse(date_str)
            if dt:
                return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return None
 
def is_recent(entry):
    dt = parse_dt(entry)
    if dt is None:
        return True
    return dt >= datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
 
def is_tp_relevant(text):
    lower = text.lower()
    return any(kw.lower() in lower for kw in TP_KEYWORDS)
 
def hours_ago(pub_str):
    if not pub_str:
        return None
    try:
        dt = dateparser.parse(pub_str)
        if not dt:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
        h = int(delta.total_seconds() // 3600)
        if h < 1:
            return "just now"
        if h < 24:
            return f"{h}h ago"
        return f"{h // 24}d ago"
    except Exception:
        return None
 
def check_url_open(url):
    try:
        r = requests.head(url, timeout=REQUEST_TIMEOUT, allow_redirects=True,
                          headers={"User-Agent": "TP-Agent/1.0"})
        final = r.url.lower()
        if r.status_code in (401, 402, 403):
            return False
        if any(p in final for p in ["/subscribe", "/login", "/paywall", "/register"]):
            return False
        return r.status_code < 400
    except Exception:
        return False
 
# ── Fetch ─────────────────────────────────────────────────────────────────────
 
def fetch_rss_items():
    items = []
    for feed_cfg in RSS_FEEDS:
        try:
            log.info(f"Fetching: {feed_cfg['name']}")
            feed = feedparser.parse(feed_cfg["url"],
                                    request_headers={"User-Agent": "Mozilla/5.0 (compatible; TP-Agent/1.0)"})
            status    = getattr(feed, "status", "no-status")
            n_entries = len(feed.entries)
            bozo      = getattr(feed, "bozo", False)
            bozo_exc  = str(getattr(feed, "bozo_exception", "")) if bozo else ""
            log.info(f"  status={status} entries={n_entries} bozo={bozo} {bozo_exc[:80]}")
            before = len(items)
            for entry in feed.entries:
                title   = getattr(entry, "title",   "").strip()
                summary = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
                link    = getattr(entry, "link",    "").strip()
                pub_str = getattr(entry, "published", "") or getattr(entry, "updated", "")
                if not title or not link:
                    continue
                if not is_recent(entry):
                    continue
                if not is_tp_relevant(f"{title} {summary}"):
                    continue
                publisher = extract_publisher(title, link, feed_cfg["name"], entry)
                clean_title = title
                if " - " in title:
                    suffix = title.rsplit(" - ", 1)[-1].strip()
                    if "." in suffix or any(d in suffix.lower() for d in PUBLISHER_MAP):
                        clean_title = title.rsplit(" - ", 1)[0].strip()
                items.append({
                    "id":      item_id(link),
                    "title":   clean_title,
                    "summary": BeautifulSoup(summary, "lxml").get_text(" ", strip=True)[:600],
                    "url":     link,
                    "source":  publisher,
                    "pub":     pub_str,
                    "open":    feed_cfg["open"],
                })
            log.info(f"  -> {len(items)-before} TP items (from {n_entries} entries)")
        except Exception as e:
            log.warning(f"Feed failed {feed_cfg['name']}: {e}")
    log.info(f"Total fetched: {len(items)}")
    return items
 
def deduplicate(items):
    seen, out = set(), []
    for item in items:
        if item["id"] not in seen:
            seen.add(item["id"])
            out.append(item)
    return out
 
# ── Gemini classify ───────────────────────────────────────────────────────────
 
CLASSIFY_PROMPT = """You are a senior transfer pricing specialist and international tax lawyer.
 
Enrich each item. The importance score determines where it appears on the page.
 
── IMPORTANCE SCORING (critical — follow exactly) ──────────────────────────────
 
TIER 1 — importance 4 or 5 (shown prominently in main feed):
  5 = Landmark: OECD final report/guidance, Supreme Court/highest court ruling,
      new TP legislation enacted, landmark APA/MAP outcome
  4 = Major: significant court/tribunal ruling on TP, new country TP regulation,
      new tax treaty with TP implications, OECD consultation on TP topic,
      any article whose PRIMARY subject is:
        → intangibles / DEMPE / IP valuation
        → business restructuring / supply chain changes
        → PE & profit attribution / dependent agent
        → control over risk / assumption of risk
        → valuation of intra-group transactions
        → arm's length methodology
 
TIER 2 — importance 2 or 3 (shown in main feed, lower priority):
  3 = Notable: policy update, consultation document, notable practitioner analysis
      on TP topics, country guidance on documentation or reporting
  2 = Update: academic commentary, minor regulatory update, procedural guidance
 
TIER 3 — importance 1 (goes to archive only, never shown as a card):
  1 = Background: general tax news tangentially mentioning TP, broad tax reform
      articles where TP is not the primary subject
 
── LENS ASSIGNMENT ─────────────────────────────────────────────────────────────
Assign ONE lens (ONE of: {lenses}):
  "Intangibles & IP"       → IP ownership, royalties, DEMPE, licensing, brand, software, IP valuation
  "Business restructuring" → supply chain changes, function transfers, business model changes, exits
  "PE & attribution"       → permanent establishment, profit attribution, nexus, dependent agent
  "Court decisions"        → ANY ruling, judgment, tribunal decision, court case
  "Finance & treasury"     → intra-group loans, financial guarantees, cash pooling, financial transactions
  "AI & digital economy"   → digital services, AI-related TP, platform economy, data as intangible
  "Dispute resolution / MAP" → APAs, MAPs, arbitration, competent authority
  "Amount B & nexus"       → Amount B simplified approach, baseline distribution, nexus rules
  "Documentation & CbCR"  → TP documentation, master file, local file, CbC reporting, DAC6
  "General TP"             → catch-all only if no other lens fits
 
── OTHER FIELDS ────────────────────────────────────────────────────────────────
region: ONE of: Global, EU, US, APAC, Nordic, Other
ai_summary: 2 sentences. State specific facts (jurisdiction, company, amount if known).
            Never write generic summaries like "this article discusses transfer pricing".
discard: true ONLY if completely unrelated to TP or taxation.
 
Return ONLY valid JSON — no markdown, no preamble:
{{"items": [{{"id":"...","lens":"...","region":"...","importance":3,"ai_summary":"...","discard":false}}]}}
 
Items to classify:
{items_json}"""
 
def classify_with_gemini(items):
    if not GEMINI_API_KEY:
        log.error("GEMINI_API_KEY not set")
        sys.exit(1)
    genai.configure(api_key=GEMINI_API_KEY)
    model    = genai.GenerativeModel("gemini-2.5-flash")
    enriched = []
    for i in range(0, len(items), 15):
        batch = items[i: i + 15]
        batch_input = [{"id": it["id"], "title": it["title"], "summary": it["summary"]}
                       for it in batch]
        prompt = CLASSIFY_PROMPT.format(
            lenses=", ".join(LENSES),
            items_json=json.dumps(batch_input, ensure_ascii=False, indent=2),
        )
        try:
            log.info(f"Classifying batch {i//15 + 1} ({len(batch)} items)...")
            raw = None
            for attempt in range(3):
                try:
                    raw = model.generate_content(prompt).text.strip()
                    break
                except Exception as e:
                    if "429" in str(e) and attempt < 2:
                        wait = 35 * (attempt + 1)
                        log.warning(f"Quota hit, retrying in {wait}s...")
                        time.sleep(wait)
                    else:
                        raise
            if raw is None:
                raise Exception("All retries failed")
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            classified = {it["id"]: it for it in json.loads(raw).get("items", [])}
            for item in batch:
                meta = classified.get(item["id"], {})
                if meta.get("discard"):
                    continue
                item["lens"]       = meta.get("lens", "General TP")
                item["region"]     = meta.get("region", "Global")
                item["importance"] = int(meta.get("importance", 2))
                item["ai_summary"] = meta.get("ai_summary", item["summary"][:300])
                item["freshness"]  = hours_ago(item.get("pub", ""))
                enriched.append(item)
            time.sleep(1.5)
        except Exception as e:
            log.warning(f"Gemini batch failed: {e}")
            for item in batch:
                item["lens"]       = "General TP"
                item["region"]     = "Global"
                item["importance"] = 2
                item["ai_summary"] = item["summary"][:300] or item["title"]
                item["freshness"]  = hours_ago(item.get("pub", ""))
                enriched.append(item)
    return enriched
 
# ── Signal of the Day ─────────────────────────────────────────────────────────
 
SIGNAL_PROMPT = """You are a senior transfer pricing partner at a Big 4 firm.
 
Based on today's TP news items, write the single most important development
for a global transfer pricing professional.
 
Return ONLY valid JSON:
{{"headline":"One punchy sentence max 12 words","body":"Two sentences of professional analysis. What does this mean in practice?","lens":"one of: {lenses}","urgency":"high|medium|low"}}
 
Today's items:
{items_json}"""
 
def get_signal_of_day(items):
    if not items:
        return {"headline": "No major TP developments today",
                "body": "All monitored sources are quiet. Check back tomorrow.",
                "lens": "General TP", "urgency": "low"}
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-2.5-flash")
        top   = sorted(items, key=lambda x: -x.get("importance", 0))[:10]
        payload = [{"title": it["title"], "source": it["source"],
                    "lens": it.get("lens",""), "importance": it.get("importance",2)}
                   for it in top]
        raw = model.generate_content(
            SIGNAL_PROMPT.format(lenses=", ".join(LENSES),
                                 items_json=json.dumps(payload, ensure_ascii=False, indent=2))
        ).text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw)
    except Exception as e:
        log.warning(f"Signal of the Day failed: {e}")
        return {"headline": "TP intelligence gathered — see items below",
                "body": "Signal analysis unavailable. Items are classified below.",
                "lens": "General TP", "urgency": "low"}
 
# ── Archive ───────────────────────────────────────────────────────────────────
 
def load_archive():
    if ARCHIVE_FILE.exists():
        try:
            return json.loads(ARCHIVE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []
 
def save_archive(today_items, archive):
    today_date = datetime.now(HELSINKI).strftime("%Y-%m-%d")
    cutoff     = (datetime.now(timezone.utc) - timedelta(days=ARCHIVE_DAYS)).strftime("%Y-%m-%d")
    for item in today_items:
        item["archive_date"] = today_date
    existing_ids = {it["id"] for it in today_items}
    merged = list(today_items)
    for item in archive:
        if item["id"] not in existing_ids and item.get("archive_date","") >= cutoff:
            merged.append(item)
            existing_ids.add(item["id"])
    merged.sort(key=lambda x: x.get("archive_date",""), reverse=True)
    ARCHIVE_FILE.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"Archive: {len(merged)} items saved")
    return merged
 
def build_archive_by_day(archive):
    by_day = defaultdict(list)
    for item in archive:
        by_day[item.get("archive_date","unknown")].append(item)
    return sorted(by_day.items(), reverse=True)
 
def build_sparklines(archive):
    today = datetime.now(HELSINKI).date()
    days  = [(today - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    sparks = {lens: [0]*7 for lens in LENSES}
    for item in archive:
        d    = item.get("archive_date","")
        lens = item.get("lens","General TP")
        if d in days and lens in sparks:
            sparks[lens][days.index(d)] += 1
    return sparks
 
def build_region_counts(items):
    counts = {r: 0 for r in REGIONS}
    for item in items:
        r = item.get("region","Global")
        counts[r] = counts.get(r, 0) + 1
    return counts
 
def check_accessibility(items):
    log.info("Checking URL accessibility...")
    for item in items:
        item["accessible"] = check_url_open(item["url"]) if item.get("open", True) else False
    return items
 
# ── Render ────────────────────────────────────────────────────────────────────
 
def render_report(items, signal, archive, sparklines, region_counts):
    items.sort(key=lambda x: (-x.get("importance",0), x.get("source","")))
    main_items   = [it for it in items if it.get("importance", 1) >= 2][:MAX_ITEMS]
    archive_only = [it for it in items if it.get("importance", 1) < 2]
    log.info(f"Main feed: {len(main_items)} items | Archive-only: {len(archive_only)} items")
    by_lens = defaultdict(list)
    for item in main_items:
        by_lens[item.get("lens","General TP")].append(item)
    lens_order = [l for l in LENSES if l in by_lens]
    high_importance_count = sum(1 for it in main_items if it.get("importance",0) >= 4)
    now_helsinki  = datetime.now(HELSINKI)
    archive_by_day = build_archive_by_day(archive)
    today = now_helsinki.date()
    day_labels = {}
    for d, _ in archive_by_day:
        try:
            dt   = datetime.strptime(d, "%Y-%m-%d").date()
            diff = (today - dt).days
            if diff == 0:   day_labels[d] = "Today"
            elif diff == 1: day_labels[d] = "Yesterday"
            else:           day_labels[d] = dt.strftime("%A, %d %b")
        except Exception:
            day_labels[d] = d
    env = Environment(loader=FileSystemLoader(REPO_ROOT / "templates"), autoescape=True)
    return env.get_template("report.html").render(
        generated_at          = now_helsinki.strftime("%A, %d %B %Y · %H:%M") + " " + now_helsinki.strftime("%Z"),
        generated_date        = now_helsinki.strftime("%Y-%m-%d"),
        total_items           = len(main_items),
        high_importance_count = high_importance_count,
        by_lens               = by_lens,
        lens_order            = lens_order,
        importance_labels     = {5:"Landmark",4:"Major",3:"Notable",2:"Update",1:"Background"},
        signal                = signal,
        archive_by_day        = archive_by_day,
        day_labels            = day_labels,
        sparklines            = sparklines,
        region_counts         = region_counts,
        max_region            = max(region_counts.values()) or 1,
    )
 
# ── Main ──────────────────────────────────────────────────────────────────────
 
def main():
    log.info("TP Special Agent starting")
    raw_items     = deduplicate(fetch_rss_items())
    log.info(f"Unique items: {len(raw_items)}")
    enriched      = classify_with_gemini(raw_items) if raw_items else []
    enriched      = check_accessibility(enriched)
    signal        = get_signal_of_day(enriched)
    archive       = load_archive()
    archive       = save_archive(enriched, archive)
    sparklines    = build_sparklines(archive)
    region_counts = build_region_counts(enriched)
    html = render_report(enriched, signal, archive, sparklines, region_counts)
    out  = REPO_ROOT / "tp_report.html"
    out.write_text(html, encoding="utf-8")
    log.info(f"Report written: {len(html):,} bytes")
 
if __name__ == "__main__":
    main()