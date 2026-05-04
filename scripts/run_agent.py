"""
TP Special Agent — run_agent.py
Fetches transfer pricing & tax news, classifies with Gemini Flash,
renders tp_report.html for GitHub Pages.
"""
 
import os
import sys
import json
import time
import hashlib
import logging
import calendar
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
 
# ── Configuration ────────────────────────────────────────────────────────────
 
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
LOOKBACK_HOURS = 72        # 3 days — catches items across weekend gaps
MAX_ITEMS      = 40        # cap total items on page
REQUEST_TIMEOUT = 15       # seconds per HTTP request
 
# TP/tax keyword filter — article must contain at least one of these.
# Broad enough to catch TP-relevant content from specialist feeds
# whose headlines don't always use the exact phrase "transfer pricing".
TP_KEYWORDS = [
    "transfer pricing", "transfer price",
    "arm's length", "arm's-length", "arms length",
    "BEPS", "pillar two", "pillar 2", "GloBE", "UTPR", "STTR",
    "OECD", "tax treaty", "double taxation",
    "permanent establishment",
    "intangibles", "DEMPE",
    "country-by-country", "CbCR", "CbC",
    "advance pricing", "APA",
    "mutual agreement", "MAP",
    "DAC6", "DAC7", "ATAD",
    "diverted profits", "controlled foreign",
    "related party", "intra-group", "intercompany",
    "tax dispute", "tax court", "tax ruling", "tax case",
    "thin capitalisation", "thin capitalization",
    "profit shifting", "base erosion",
    "withholding tax", "royalt",
    "international tax", "corporate tax",
    "state aid", "tax avoidance",
]
 
# Focus lenses (secondary tags — TP is always the universe)
LENSES = [
    "Intangibles & IP",
    "Business restructuring",
    "Finance & treasury",
    "PE & attribution",
    "AI & digital economy",
    "Court decisions",
    "Pillar Two / GloBE",
    "Dispute resolution / MAP",
    "Documentation & CbCR",
    "General TP",
]
 
# ── RSS Feed Sources ──────────────────────────────────────────────────────────
# All sources are TP/tax-specialist — no general news that needs heavy filtering.
# Where a feed supports query params, TP terms are baked in.
 
RSS_FEEDS = [
    # OECD
    {
        "name": "OECD Tax",
        "url": "https://www.oecd.org/tax/rss.xml",
        "open": True,
    },
    # EU Tax Observatory
    {
        "name": "EU Tax Observatory",
        "url": "https://www.taxobservatory.eu/feed/",
        "open": True,
    },
    # MNE Tax — specialist TP/international tax news
    {
        "name": "MNE Tax",
        "url": "https://mnetax.com/feed",
        "open": True,
    },
    # Tax Foundation — international tax coverage
    {
        "name": "Tax Foundation",
        "url": "https://taxfoundation.org/feed/",
        "open": True,
    },
    # Transfer Pricing Asia
    {
        "name": "TP Asia",
        "url": "https://www.transferpricingasia.com/feed/",
        "open": True,
    },
    # International Tax Review
    {
        "name": "Int'l Tax Review",
        "url": "https://www.internationaltaxreview.com/rss/",
        "open": True,
    },
    # Tax Justice Network
    {
        "name": "Tax Justice Network",
        "url": "https://taxjustice.net/feed/",
        "open": True,
    },
    # IBFD (public news feed — not paywalled articles)
    {
        "name": "IBFD News",
        "url": "https://www.ibfd.org/rss/news",
        "open": True,
    },
    # Kluwer International Tax Blog
    {
        "name": "Kluwer Int'l Tax",
        "url": "http://kluwertaxlawblog.com/feed/",
        "open": True,
    },
    # Tax Notes (free news feed)
    {
        "name": "Tax Notes",
        "url": "https://www.taxnotes.com/rss/feed.xml",
        "open": True,
    },
]
 
# ── Court decisions & additional specialist feeds ─────────────────────────────
# International court decisions are the core intelligence target.
# Covers CJEU (EU), US Tax Court, UK FTT/UT, German BFH, OECD dispute tracker,
# plus EUR-Lex for new EU tax legislation.
# KHO (Finland) deliberately excluded — covered through other channels.
 
EXTRA_RSS_FEEDS = [
    {
        # EUR-Lex RSS — EU legal acts: direct taxation subject (SUBDOM 12.10)
        # Covers new Directives, Decisions, Commission proposals on tax.
        "name": "EUR-Lex Tax",
        "url": (
            "https://eur-lex.europa.eu/search.html"
            "?scope=EURLEX&type=advanced"
            "&rss=true&locale=en"
            "&SUBDOM_CODED=12.10"
        ),
        "open": True,
    },
    {
        # CJEU — Court of Justice of the EU, recent judgments RSS
        # Covers all CJEU judgments; TP filter applied downstream.
        # Many landmark TP cases (Fiat, Amazon, Apple, Engie) come from here.
        "name": "CJEU Judgments",
        "url": "https://curia.europa.eu/jcms/jcms/Jo2_7052/en/",
        "open": True,
    },
    {
        # US Tax Court — official opinions RSS feed
        # US TP decisions (Coca-Cola, Medtronic, Amazon, Whirlpool) are
        # globally influential and widely covered by TP practitioners.
        "name": "US Tax Court",
        "url": "https://www.ustaxcourt.gov/USTCWeb/rss/opinions.aspx",
        "open": True,
    },
    {
        # Tax Analysts — TP court decisions tracker (free feed)
        # Aggregates major TP case outcomes across jurisdictions.
        "name": "Tax Analysts TP",
        "url": "https://www.taxanalysts.org/rss/transfer-pricing",
        "open": True,
    },
    {
        # Kluwer International Tax Blog — covers CJEU, UK FTT/UT, German BFH
        # and other European court decisions with practitioner commentary.
        "name": "Kluwer Int'l Tax",
        "url": "https://kluwertaxlawblog.com/feed/",
        "open": True,
    },
    {
        # TaxGuru — India ITAT and High Court TP decisions
        # India is one of the most active TP litigation jurisdictions globally.
        "name": "TaxGuru India TP",
        "url": "https://taxguru.in/category/income-tax/transfer-pricing/feed/",
        "open": True,
    },
    {
        # Bloomberg Tax — TP news feed (headlines open, articles may require sub)
        # Paywall check handles article-level gating automatically.
        "name": "Bloomberg Tax",
        "url": "https://news.bloombergtax.com/transfer-pricing/rss",
        "open": True,
    },
    {
        # Wolters Kluwer CCH — international tax & TP case law digest
        "name": "CCH Tax",
        "url": "https://www.cchgroup.com/rss/tax-news",
        "open": True,
    },
]
 
 
# ── Helpers ───────────────────────────────────────────────────────────────────
 
def item_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:12]
 
 
def is_recent_struct(time_struct) -> bool:
    """Handle feedparser's published_parsed / updated_parsed (a time.struct_time)."""
    try:
        if time_struct is None:
            return True
        # calendar.timegm converts UTC struct_time → UTC timestamp
        ts = calendar.timegm(time_struct)
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
        return dt >= cutoff
    except Exception:
        return True
 
 
def is_recent_str(date_str: str) -> bool:
    """Fallback: parse a date string."""
    try:
        if not date_str:
            return True
        dt = dateparser.parse(date_str)
        if dt is None:
            return True
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
        return dt >= cutoff
    except Exception:
        return True
 
 
def is_recent(entry) -> bool:
    """
    Check if a feedparser entry is within the lookback window.
    Prefers the pre-parsed struct (published_parsed / updated_parsed)
    which feedparser guarantees is in UTC. Falls back to string parsing.
    """
    struct = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if struct is not None:
        return is_recent_struct(struct)
    date_str = getattr(entry, "published", "") or getattr(entry, "updated", "")
    return is_recent_str(date_str)
 
 
def is_tp_relevant(text: str) -> bool:
    lower = text.lower()
    return any(kw.lower() in lower for kw in TP_KEYWORDS)
 
 
def check_url_open(url: str) -> bool:
    """HEAD request to check if URL is openly accessible (no paywall redirect)."""
    try:
        r = requests.head(url, timeout=REQUEST_TIMEOUT, allow_redirects=True,
                          headers={"User-Agent": "TP-Agent/1.0"})
        # Paywall sites often redirect to /subscribe or return 401/403
        final = r.url.lower()
        if r.status_code in (401, 403, 402):
            return False
        if any(p in final for p in ["/subscribe", "/login", "/paywall", "/register"]):
            return False
        return r.status_code < 400
    except Exception:
        return False
 
 
# ── Fetch RSS ─────────────────────────────────────────────────────────────────
 
def fetch_rss_items() -> list[dict]:
    items = []
    all_feeds = RSS_FEEDS + EXTRA_RSS_FEEDS
    for feed_cfg in all_feeds:
        try:
            log.info(f"Fetching RSS: {feed_cfg['name']}")
            feed = feedparser.parse(
                feed_cfg["url"],
                request_headers={"User-Agent": "TP-Agent/1.0 (research bot)"},
            )
            for entry in feed.entries:
                title   = getattr(entry, "title", "").strip()
                summary = (getattr(entry, "summary", "") or
                           getattr(entry, "description", "") or "")
                link    = getattr(entry, "link", "").strip()
                pub_str = getattr(entry, "published", "") or getattr(entry, "updated", "")
 
                if not title or not link:
                    continue
                if not is_recent(entry):          # uses struct-first logic
                    continue
 
                combined = f"{title} {summary}"
                if not is_tp_relevant(combined):
                    continue
 
                items.append({
                    "id":      item_id(link),
                    "title":   title,
                    "summary": BeautifulSoup(summary, "lxml").get_text(" ", strip=True)[:600],
                    "url":     link,
                    "source":  feed_cfg["name"],
                    "pub":     pub_str,
                    "open":    feed_cfg["open"],
                })
            log.info(f"  {feed_cfg['name']}: {len([i for i in items if i['source']==feed_cfg['name']])} items")
        except Exception as e:
            log.warning(f"RSS fetch failed for {feed_cfg['name']}: {e}")
    log.info(f"RSS: {len(items)} TP-relevant items fetched")
    return items
 
 
# ── Deduplicate ───────────────────────────────────────────────────────────────
 
def deduplicate(items: list[dict]) -> list[dict]:
    seen = set()
    out  = []
    for item in items:
        if item["id"] not in seen:
            seen.add(item["id"])
            out.append(item)
    return out
 
 
# ── Gemini classification ─────────────────────────────────────────────────────
 
CLASSIFY_PROMPT = """You are a senior transfer pricing specialist and international tax lawyer.
 
You will receive a batch of news items about transfer pricing and international taxation.
Your job is to enrich each item with:
1. A lens tag (which aspect of TP/tax this touches)
2. An importance score (1-5, where 5 = landmark development)
3. A 2-sentence professional summary in English
 
CRITICAL RULES:
- Every item is already confirmed to be about transfer pricing or international taxation.
  That is the universe. Do not question this.
- Lens must be ONE of: {lenses}
- Importance scoring guide:
    5 = OECD final report, landmark court ruling, new Pillar Two legislation
    4 = significant country guidance, major case decision, new treaty
    3 = consultation document, policy update, notable case
    2 = academic commentary, minor guidance, procedural update
    1 = general news, background article
- If an item has no summary provided, write one based on the title alone.
- Discard any item that is genuinely not about TP or tax (return discard:true).
 
Return ONLY valid JSON — no markdown, no preamble. Format:
{{
  "items": [
    {{
      "id": "...",
      "lens": "...",
      "importance": 3,
      "ai_summary": "...",
      "discard": false
    }}
  ]
}}
 
Items to classify:
{items_json}
"""
 
 
def classify_with_gemini(items: list[dict]) -> list[dict]:
    if not GEMINI_API_KEY:
        log.error("GEMINI_API_KEY not set")
        sys.exit(1)
 
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-2.0-flash")
 
    # Process in batches of 15 to stay within token limits
    batch_size = 15
    enriched   = []
 
    for i in range(0, len(items), batch_size):
        batch = items[i : i + batch_size]
        batch_input = [
            {"id": it["id"], "title": it["title"], "summary": it["summary"]}
            for it in batch
        ]
 
        prompt = CLASSIFY_PROMPT.format(
            lenses=", ".join(LENSES),
            items_json=json.dumps(batch_input, ensure_ascii=False, indent=2),
        )
 
        try:
            log.info(f"Classifying batch {i//batch_size + 1} ({len(batch)} items)…")
            response = model.generate_content(prompt)
            raw      = response.text.strip()
 
            # Strip markdown fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
 
            result = json.loads(raw)
            classified = {it["id"]: it for it in result.get("items", [])}
 
            for item in batch:
                meta = classified.get(item["id"], {})
                if meta.get("discard"):
                    continue
                item["lens"]       = meta.get("lens", "General TP")
                item["importance"] = int(meta.get("importance", 2))
                item["ai_summary"] = meta.get("ai_summary", item["summary"][:300])
                enriched.append(item)
 
            time.sleep(1.5)  # rate-limit courtesy pause
 
        except Exception as e:
            log.warning(f"Gemini classification failed for batch: {e}")
            # Fall back: include items without AI enrichment
            for item in batch:
                item["lens"]       = "General TP"
                item["importance"] = 2
                item["ai_summary"] = item["summary"][:300] or item["title"]
                enriched.append(item)
 
    return enriched
 
 
# ── Paywall check ─────────────────────────────────────────────────────────────
 
def check_accessibility(items: list[dict]) -> list[dict]:
    log.info("Checking URL accessibility…")
    for item in items:
        if not item.get("open", True):
            item["accessible"] = False
        else:
            item["accessible"] = check_url_open(item["url"])
    return items
 
 
# ── Render HTML ───────────────────────────────────────────────────────────────
 
def render_report(items: list[dict]) -> str:
    # Sort: importance desc, then by source
    items.sort(key=lambda x: (-x.get("importance", 0), x.get("source", "")))
    items = items[:MAX_ITEMS]
 
    # Group by lens
    from collections import defaultdict
    by_lens: dict[str, list] = defaultdict(list)
    for item in items:
        by_lens[item.get("lens", "General TP")].append(item)
 
    # Order lenses by highest importance item within each
    lens_order = sorted(
        by_lens.keys(),
        key=lambda l: -max(i.get("importance", 0) for i in by_lens[l])
    )
 
    # Compute stat — done in Python, not Jinja2 (bug fix)
    high_importance_count = sum(
        1 for item in items if item.get("importance", 0) >= 4
    )
 
    # Proper Helsinki time via pytz (handles EET/EEST automatically)
    now_helsinki = datetime.now(HELSINKI)
 
    env = Environment(
        loader=FileSystemLoader(Path(__file__).parent.parent / "templates"),
        autoescape=True,
    )
    template = env.get_template("report.html")
 
    return template.render(
        generated_at=now_helsinki.strftime("%A, %d %B %Y · %H:%M") + " " + now_helsinki.strftime("%Z"),
        generated_date=now_helsinki.strftime("%Y-%m-%d"),
        total_items=len(items),
        high_importance_count=high_importance_count,
        by_lens=by_lens,
        lens_order=lens_order,
        importance_labels={5: "Landmark", 4: "Major", 3: "Notable", 2: "Update", 1: "Background"},
    )
 
 
# ── Main ──────────────────────────────────────────────────────────────────────
 
def main():
    log.info("── TP Special Agent starting ──")
 
    all_items = deduplicate(fetch_rss_items())
 
    log.info(f"Total unique TP items: {len(all_items)}")
 
    if not all_items:
        log.warning("No items found — writing empty report")
 
    enriched = classify_with_gemini(all_items) if all_items else []
    enriched = check_accessibility(enriched)
 
    html = render_report(enriched)
 
    out = Path(__file__).parent.parent / "tp_report.html"
    out.write_text(html, encoding="utf-8")
    log.info(f"Report written → {out}")
 
 
if __name__ == "__main__":
    main()
