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
MIN_DISCOUNT_PERCENT = 25  # Minimum discount percentage to alert
SEEN_DEALS_FILE = "seen_deals.json"

# Target Clearance Endpoints on Walmart.ca
# 4 URLs with retailer=Walmart filter = 32 credits/day at 8 runs/day (under 1,000 monthly credits)
CATEGORIES_TO_SCRAPE = [
    {
        "name": "LEGO Clearance",
        "url": (
            "https://www.walmart.ca/en/search?q=lego&facet=special_offers%3AClearance%7C%7Cretailer%3AWalmart"
        ),
    },
    {
        "name": "Dolls Clearance",
        "url": (
            "https://www.walmart.ca/en/search?q=dolls&facet=special_offers%3AClearance%7C%7Cretailer%3AWalmart"
        ),
    },
    {
        "name": "Board Games Clearance",
        "url": (
            "https://www.walmart.ca/en/search?q=board+games&facet=special_offers%3AClearance%7C%7Cretailer%3AWalmart"
        ),
    },
    {
        "name": "Action Figures Clearance",
        "url": (
            "https://www.walmart.ca/en/search?q=action+figures&facet=special_offers%3AClearance%7C%7Cretailer%3AWalmart"
        ),
    },
    {
        "name": "Toddler & Educational Clearance",
        "url": (
            "https://www.walmart.ca/en/search?q=toddler+toys&facet=special_offers%3AClearance%7C%7Cretailer%3AWalmart"
        ),
    },
]

def load_seen_deals():
    """Loads previously alerted deal IDs from seen_deals.json."""
    if os.path.exists(SEEN_DEALS_FILE):
        try:
            with open(SEEN_DEALS_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_seen_deals(seen_set):
    """Saves updated deal IDs back to seen_deals.json."""
    with open(SEEN_DEALS_FILE, "w") as f:
        json.dump(list(seen_set), f)


def send_telegram_alert(title, current_price, original_price, discount, url):
    """Formats and sends Telegram alert messages."""
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
        response = requests.post(telegram_url, json=payload, timeout=10)
        if response.status_code == 200:
            print(f"Telegram alert sent for: {title}")
        else:
            print(
                f"Telegram API response error: {response.status_code} - {response.text}"
            )
    except Exception as e:
        print(f"Failed to send Telegram message: {e}")


def fetch_walmart_page(target_url):
    """Passes request through ScraperAPI.

    Note: 'render': 'false' consumes 1 API credit per call (vs 5 credits with
    render='true').
    """
    payload = {
        "api_key": SCRAPER_API_KEY,
        "url": target_url,
        "country_code": "ca",
        "render": "false",
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

        # Primary card selector with fallback to product URLs (/ip/)
        product_cards = soup.find_all(
            "div", {"data-item-id": True}
        ) or soup.find_all("div", {"data-automation-id": "product-tile"})

        if not product_cards:
            product_cards = [
                a.parent
                for a in soup.find_all("a", href=re.compile(r"/ip/"))
                if a.parent
            ]

        print(f"Found {len(product_cards)} candidate product cards.")

        for card in product_cards:
            try:
                # Extract Product Link & Unique Item ID
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

                # Extract Product Title
                title_elem = card.find(
                    "span", {"data-automation-id": "product-title"}
                ) or card.find("p")
                if not title_elem:
                    continue
                title = title_elem.text.strip()

                card_text = card.get_text(separator=" ")

                # Exclude explicitly out-of-stock items
                if "out of stock" in card_text.lower():
                    continue

                # Enhanced Price Parsing (Catches 'Now', 'Was', and 'You Save')
                now_match = re.search(
                    r"(?:Now|Price)?\s*\$([\d\.]+)", card_text, re.IGNORECASE
                )
                was_match = re.search(
                    r"was\s*\$([\d\.]+)", card_text, re.IGNORECASE
                )

                current_price = float(now_match.group(1)) if now_match else None
                original_price = None

                if was_match:
                    original_price = float(was_match.group(1))
                else:
                    # Fallback: Calculate original price using "You save $X.XX"
                    save_match = re.search(
                        r"save\s*\$([\d\.]+)", card_text, re.IGNORECASE
                    )
                    if save_match and current_price:
                        savings = float(save_match.group(1))
                        original_price = current_price + savings

                # Ensure both prices were extracted
                if not current_price or not original_price:
                    continue

                if original_price <= current_price or original_price == 0:
                    continue

                discount = (
                    (original_price - current_price) / original_price
                ) * 100.0

                # Alert Evaluation
                if discount >= MIN_DISCOUNT_PERCENT:
                    print(
                        f"DEAL FOUND: {title} (-{discount:.0f}%) ->"
                        f" ${current_price:.2f}"
                    )
                    send_telegram_alert(
                        title,
                        current_price,
                        original_price,
                        discount,
                        item_url,
                    )
                    seen_deals.add(item_id)

            except Exception as err:
                print(f"Error parsing product card: {err}")
                continue

    save_seen_deals(seen_deals)


if __name__ == "__main__":
    run_scanner()
