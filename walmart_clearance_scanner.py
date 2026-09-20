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

# Targets on Walmart.ca
TARGET_CATEGORIES = [
    {
        "name": "General Clearance",
        "url": "https://www.walmart.ca/en/shop/clearance/6000204800999",
    },
    {
        "name": "Toys Clearance",
        "url": "https://www.walmart.ca/en/cp/toys/10011?facet=special_offers%3AClearance",
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
    """Passes the request through ScraperAPI with Canadian residential proxy rendering."""
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
        print(f"ScraperAPI returned status code: {response.status_code}")
    except Exception as e:
        print(f"Network error fetching page: {e}")
    return None


def run_scanner():
    if not all([SCRAPER_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]):
        print("ERROR: Missing required environment variables.")
        return

    seen_deals = load_seen_deals()

    for category in TARGET_CATEGORIES:
        print(f"Scanning category: {category['name']}...")
        html = fetch_walmart_page(category["url"])
        if not html:
            continue

        soup = BeautifulSoup(html, "html.parser")

        # Walmart renders product cards using data attributes
        product_cards = soup.find_all("div", {"data-item-id": True})

        for card in product_cards:
            try:
                item_id = card.get("data-item-id")
                if item_id in seen_deals:
                    continue

                title_elem = card.find("span", {"data-automation-id": "product-title"})
                if not title_elem:
                    continue
                title = title_elem.text.strip()

                link_elem = card.find("a", href=True)
                if not link_elem:
                    continue
                
                item_url = (
                    f"https://www.walmart.ca{link_elem['href']}"
                    if link_elem["href"].startswith("/")
                    else link_elem["href"]
                )

                current_price_elem = card.find("div", {"data-automation-id": "product-price"})
                was_price_elem = card.find("div", {"class": re.compile(".*was.*")})

                if not current_price_elem or not was_price_elem:
                    continue

                current_price = float(re.sub(r"[^\d.]", "", current_price_elem.text))
                original_price = float(re.sub(r"[^\d.]", "", was_price_elem.text))

                if original_price <= current_price or original_price == 0:
                    continue

                discount = ((original_price - current_price) / original_price) * 100

                # Seller Validation
                seller_text = card.get_text()
                if REQUIRE_WALMART_SELLER and "Sold by" in seller_text and "Walmart" not in seller_text:
                    continue

                if discount >= MIN_DISCOUNT_PERCENT:
                    print(f"DEAL FOUND: {title} (-{discount:.0f}%)")
                    send_telegram_alert(title, current_price, original_price, discount, item_url)
                    seen_deals.add(item_id)

            except Exception as e:
                continue

    save_seen_deals(seen_deals)


if __name__ == "__main__":
    run_scanner()
