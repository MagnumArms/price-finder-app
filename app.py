import re
import time
import json
import urllib.parse
from dataclasses import dataclass
from typing import Optional, List, Dict, Any, Tuple

import requests
import pandas as pd
import streamlit as st
from bs4 import BeautifulSoup
from price_parser import Price

# ----------------------------
# Helpers
# ----------------------------

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
}

PRICE_SANITIZE_RE = re.compile(r"[^\d.,]")

@dataclass
class SiteConfig:
    name: str
    search_url_template: str  # must include {query}
    card_selector: str = ""   # CSS selector for each result item/card
    title_selector: str = ""
    price_selector: str = ""
    link_selector: str = ""
    currency_hint: str = ""   # optional like "GBP", "USD"
    max_results: int = 10

@dataclass
class FoundOffer:
    site: str
    title: str
    price: float
    currency: str
    url: str
    matched: bool

def normalize_space(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def wildcard_match(query: str, text: str) -> bool:
    # Wildcard either side of term: "*query*" => "contains query" (case-insensitive)
    q = normalize_space(query).lower()
    t = normalize_space(text).lower()
    return q in t

def parse_price(text: str) -> Tuple[Optional[float], Optional[str]]:
    if not text:
        return None, None
    p = Price.fromstring(text)
    if p.amount is None:
        cleaned = PRICE_SANITIZE_RE.sub("", text).replace(",", "")
        try:
            return float(cleaned), None
        except Exception:
            return None, None
    return float(p.amount), p.currency

def absolutize_url(base: str, maybe_relative: str) -> str:
    if not maybe_relative:
        return ""
    return urllib.parse.urljoin(base, maybe_relative)

def fetch_html(url: str, timeout_s: int) -> str:
    r = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout_s)
    r.raise_for_status()
    return r.text

def extract_jsonld_offers(soup: BeautifulSoup, base_url: str) -> List[FoundOffer]:
    offers: List[FoundOffer] = []
    scripts = soup.select('script[type="application/ld+json"]')
    for sc in scripts:
        try:
            data = json.loads(sc.get_text(strip=True))
        except Exception:
            continue

        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            try:
                offers.extend(_extract_offers_from_jsonld_node(node, base_url))
            except Exception:
                continue
    return offers

def _extract_offers_from_jsonld_node(node: Any, base_url: str) -> List[FoundOffer]:
    out: List[FoundOffer] = []

    def walk(obj: Any):
        if isinstance(obj, dict):
            if obj.get("@type") in ("Product", "Offer", "AggregateOffer"):
                yield obj
            for v in obj.values():
                yield from walk(v)
        elif isinstance(obj, list):
            for it in obj:
                yield from walk(it)

    for obj in walk(node):
        t = obj.get("@type")
        if t == "Product":
            title = normalize_space(obj.get("name") or "")
            offers_obj = obj.get("offers")
            if isinstance(offers_obj, dict):
                out.extend(_offers_from_offer_obj(offers_obj, title, base_url))
            elif isinstance(offers_obj, list):
                for oo in offers_obj:
                    if isinstance(oo, dict):
                        out.extend(_offers_from_offer_obj(oo, title, base_url))
        elif t in ("Offer", "AggregateOffer"):
            title = normalize_space(obj.get("name") or "")
            out.extend(_offers_from_offer_obj(obj, title, base_url))
    return out

def _offers_from_offer_obj(offer: Dict[str, Any], title: str, base_url: str) -> List[FoundOffer]:
    out: List[FoundOffer] = []
    currency = offer.get("priceCurrency") or ""
    url = absolutize_url(base_url, offer.get("url") or "") or base_url

    price_val = offer.get("price")
    if price_val is None:
        price_val = offer.get("lowPrice")

    try:
        price = float(price_val) if price_val is not None else None
    except Exception:
        price = None

    if price is not None:
        out.append(
            FoundOffer(
                site="",
                title=title or "(JSON-LD Product)",
                price=price,
                currency=currency or "",
                url=url,
                matched=True,
            )
        )
    return out

def scrape_site(query: str, cfg: SiteConfig, timeout_s: int, sleep_s: float = 0.0) -> List[FoundOffer]:
    if "{query}" not in cfg.search_url_template:
        raise ValueError(f"{cfg.name}: search_url_template must include {{query}}")

    encoded_q = urllib.parse.quote_plus(query.strip())
    search_url = cfg.search_url_template.replace("{query}", encoded_q)

    if sleep_s > 0:
        time.sleep(sleep_s)

    html = fetch_html(search_url, timeout_s=timeout_s)
    soup = BeautifulSoup(html, "lxml")

    found: List[FoundOffer] = []
    base_url = search_url

    # 1) Configured selectors
    cards = soup.select(cfg.card_selector) if cfg.card_selector else []
    if cards:
        for c in cards[: cfg.max_results]:
            title = ""
            if cfg.title_selector:
                t = c.select_one(cfg.title_selector)
                if t:
                    title = normalize_space(t.get_text(" ", strip=True))

            price_text = ""
            if cfg.price_selector:
                p = c.select_one(cfg.price_selector)
                if p:
                    price_text = normalize_space(p.get_text(" ", strip=True))

            link = ""
            if cfg.link_selector:
                a = c.select_one(cfg.link_selector)
                if a and a.get("href"):
                    link = absolutize_url(base_url, a.get("href"))

            amount, currency = parse_price(price_text)
            if amount is None:
                continue

            matched = wildcard_match(query, title) if title else True

            found.append(
                FoundOffer(
                    site=cfg.name,
                    title=title or "(no title found)",
                    price=amount,
                    currency=(currency or cfg.currency_hint or ""),
                    url=link or search_url,
                    matched=matched,
                )
            )

    # 2) JSON-LD fallback
    for o in extract_jsonld_offers(soup, base_url):
        o.site = cfg.name
        o.matched = wildcard_match(query, o.title) if o.title else True
        if cfg.currency_hint and not o.currency:
            o.currency = cfg.currency_hint
        found.append(o)

    # Filter + de-dup
    filtered = [x for x in found if x.matched or x.title in ("(no title found)", "(JSON-LD Product)")]
    dedup = {}
    for x in filtered:
        key = (x.url, x.price, x.currency)
        if key not in dedup:
            dedup[key] = x
    return list(dedup.values())

# ----------------------------
# Streamlit UI
# ----------------------------

st.set_page_config(page_title="Price Finder (multi-site)", layout="wide")
st.title("Multi-site Price Finder")
st.caption("Enter an item, add websites at runtime (URL template + selectors), and find the lowest price found.")

with st.sidebar:
    st.subheader("Search")
    query = st.text_input("Item to search for", placeholder="e.g., 'Bosch 18V drill'")

    st.subheader("Controls")
    timeout = st.number_input("Request timeout (seconds)", min_value=5, max_value=60, value=20, step=1)
    delay = st.number_input("Delay between sites (seconds)", min_value=0.0, max_value=5.0, value=0.3, step=0.1)
    run = st.button("Search prices", type="primary", use_container_width=True)

st.subheader("Websites to search (configure at run time)")

if "sites_df" not in st.session_state:
    st.session_state.sites_df = pd.DataFrame(
        [
            {
                "name": "ExampleStore",
                "search_url_template": "https://www.example.com/search?q={query}",
                "card_selector": ".product-card",
                "title_selector": ".title",
                "price_selector": ".price",
                "link_selector": "a",
                "currency_hint": "GBP",
                "max_results": 10,
            }
        ]
    )

edited = st.data_editor(
    st.session_state.sites_df,
    num_rows="dynamic",
    use_container_width=True,
    column_config={
        "name": st.column_config.TextColumn("Site name"),
        "search_url_template": st.column_config.TextColumn("Search URL template (must include {query})"),
        "card_selector": st.column_config.TextColumn("Result card selector (CSS)"),
        "title_selector": st.column_config.TextColumn("Title selector (CSS)"),
        "price_selector": st.column_config.TextColumn("Price selector (CSS)"),
        "link_selector": st.column_config.TextColumn("Link selector (CSS)"),
        "currency_hint": st.column_config.TextColumn("Currency hint (optional)"),
        "max_results": st.column_config.NumberColumn("Max results", min_value=1, max_value=50),
    },
)
st.session_state.sites_df = edited

if run:
    if not query.strip():
        st.error("Please enter an item to search for.")
        st.stop()

    results: List[FoundOffer] = []
    errors: List[str] = []

    for _, row in st.session_state.sites_df.fillna("").iterrows():
        try:
            cfg = SiteConfig(
                name=str(row["name"]).strip() or "Unnamed site",
                search_url_template=str(row["search_url_template"]).strip(),
                card_selector=str(row["card_selector"]).strip(),
                title_selector=str(row["title_selector"]).strip(),
                price_selector=str(row["price_selector"]).strip(),
                link_selector=str(row["link_selector"]).strip(),
                currency_hint=str(row["currency_hint"]).strip(),
                max_results=int(row["max_results"]) if str(row["max_results"]).strip() else 10,
            )

            if not cfg.search_url_template:
                continue

            site_offers = scrape_site(query, cfg, timeout_s=int(timeout), sleep_s=float(delay))
            results.extend(site_offers)

        except Exception as e:
            errors.append(f"{row.get('name', 'site')}: {e}")

    if errors:
        with st.expander("Errors (some sites may block scraping)"):
            for e in errors:
                st.write("•", e)

    if not results:
        st.warning("No prices found. Try adjusting selectors, or the site may block automated requests.")
        st.stop()

    df = pd.DataFrame([r.__dict__ for r in results])
    df = df[["site", "title", "price", "currency", "url", "matched"]].copy()
    df.sort_values(["price"], ascending=True, inplace=True)

    st.subheader("Lowest prices found")
    st.dataframe(df, use_container_width=True)

    best = df.iloc[0].to_dict()
    st.success(f"Best found: {best['price']} {best['currency']} — {best['title']} ({best['site']})")
    st.write("Link:", best["url"])
