import json
import os
import re
import requests
from bs4 import BeautifulSoup

# --- ENVIRONMENT CONFIGURATION ---
SCRAPER_API_KEY = os.getenv("SCRAPER_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Filtering Criteria
MIN_DISCOUNT_PERCENT = 35  # Alert threshold
REQUIRE_WALMART_SELLER = True  # Ignore 3rd-party marketplace sellers
SEEN_DEALS_FILE = "seen_deals.json"

# Targets on Walmart.ca (Structured as dictionaries)
CATEGORIES_TO_SCRAPE = [
    {
        "name": "General Clearance",
        "url": "https://www.walmart.ca/en/clearance/N-101",
    },
    {
        "name": "Toys Clearance",
        "url": "https://www.walmart.ca/en/toys/clearance/N-1081",
    },
    {
        "name": "Toys Search Clearance",
        "url": "https://www.walmart.ca/en/search?q=clearance+toys",
    },
]


def load_seen_deals():
    if os.path.exists(SEEN_DEALS_FILE):
        try:
            with open(SEEN_DEALS_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_seen_deals(seen_set):
    with open(SEEN_DEALS_FILE, "w") as f:
        json.dump(list(seen_set), f)


def send_telegram_alert(title, current_price, original_price, discount, url):
    message = (
        f"🚨 <b>WALMART.CA CLEARANCE DROP!</b> 🚨\n\n"
        f"📦 <b>Item:</b> {title}\n"
        f"💰 <b>Clearance Price:</b> ${current_price:.2f} CAD\n"
        f"🏷️ <b>Was Price:</b> ${original_price:.2f} CAD\n"
        f"🔥 <b>Discount:</b> {discount:.0f}% OFF\n\n"
        f"🛒 <a href='{url}'><b>[CLICK HERE TO BUY ON WALMART.CA]</b></a>"
    )

    telegram_url = (
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        requests.post(telegram_url, json=payload, timeout=10)
    except Exception as e:
        print(f"Failed to send Telegram message: {e}")


def fetch_walmart_page(target_url):
    """Passes request through ScraperAPI with render=true to process client JS."""
    payload = {
        "api_key": SCRAPER_API_KEY,
        "url": target_url,
        "country_code": "ca",
        "render": "true",
    }
    try:
        response = requests.get(
            "http://api.scraperapi.com", params=payload, timeout=60
        )
        if response.status_code == 200:
            return response.text
        print(f"ScraperAPI status code: {response.status_code}")
    except Exception as e:
        print(f"Network error fetching page: {e}")
    return None


def run_scanner():
    if not all([SCRAPER_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]):
        print("ERROR: Missing required environment variables.")
        return

    seen_deals = load_seen_deals()

    for category in CATEGORIES_TO_SCRAPE:
        print(f"Scanning category: {category['name']}...")
        html = fetch_walmart_page(category["url"])
        if not html:
            continue

        soup = BeautifulSoup(html, "html.parser")

        # Fallback multi-selector strategy to catch variations in Walmart layout
        product_cards = soup.find_all(
            "div", {"data-item-id": True}
        ) or soup.find_all("div", {"data-automation-id": "product-tile"})

        if not product_cards:
            # Fallback search for anchor elements leading to product pages
            product_cards = [
                a.parent
                for a in soup.find_all("a", href=re.compile(r"/ip/"))
                if a.parent
            ]

        print(f"Found {len(product_cards)} candidate product cards.")

        for card in product_cards:
            try:
                # Extract URL & ID
                link_elem = card.find("a", href=re.compile(r"/ip/"))
                if not link_elem or "href" not in link_elem.attrs:
                    continue

                relative_url = link_elem["href"]
                item_id_match = re.search(r"/ip/(?:.*/)?(\d+)", relative_url)
                item_id = (
                    item_id_match.group(1)
                    if item_id_match
                    else card.get("data-item-id")
                )

                if not item_id or item_id in seen_deals:
                    continue

                item_url = (
                    f"https://www.walmart.ca{relative_url}"
                    if relative_url.startswith("/")
                    else relative_url
                )

                # Extract Title
                title_elem = card.find(
                    "span", {"data-automation-id": "product-title"}
                ) or card.find("p")
                if not title_elem:
                    continue
                title = title_elem.text.strip()

                # Extract Prices via regex matching on inner card text
                card_text = card.get_text(separator=" ")

                now_match = re.search(r"(?:Now|Price)\s*\$([\d\.]+)", card_text)
                was_match = re.search(r"was\s*\$([\d\.]+)", card_text, re.IGNORECASE)

                if not now_match or not was_match:
                    continue

                current_price = float(now_match.group(1))
                original_price = float(was_match.group(1))

                if original_price <= current_price or original_price == 0:
                    continue

                discount = (
                    (original_price - current_price) / original_price
                ) * 100.0

                # Seller Validation
                if (
                    REQUIRE_WALMART_SELLER
                    and "Sold by" in card_text
                    and "Walmart" not in card_text
                ):
                    continue

                if discount >= MIN_DISCOUNT_PERCENT:
                    print(
                        f"DEAL FOUND: {title} (-{discount:.0f}%) -> ${current_price:.2f}"
                    )
                    send_telegram_alert(
                        title, current_price, original_price, discount, item_url
                    )
                    seen_deals.add(item_id)

            except Exception as err:
                print(f"Error parsing product card: {err}")
                continue

    save_seen_deals(seen_deals)


if __name__ == "__main__":
    run_scanner()
