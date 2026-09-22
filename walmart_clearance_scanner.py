import os
import re
import json
import requests
from bs4 import BeautifulSoup

# ==========================================
# CONFIGURATION
# ==========================================
SCRAPER_API_KEY = os.environ.get("SCRAPER_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

MIN_DISCOUNT_PERCENT = 24.0
SEEN_DEALS_FILE = "seen_deals.json"

CATEGORIES_TO_SCRAPE = [
    {
        "name": "All Toys Clearance (Page 1)",
        "url": "https://www.walmart.ca/en/search?q=toys&page=1&facet=special_offers:Clearance||retailer:Walmart"
    },
    {
        "name": "All Toys Clearance (Page 2)",
        "url": "https://www.walmart.ca/en/search?q=toys&page=2&facet=special_offers:Clearance||retailer:Walmart"
    },
    {
        "name": "LEGO Clearance",
        "url": "https://www.walmart.ca/en/search?q=lego&facet=special_offers:Clearance||retailer:Walmart"
    },
    {
        "name": "Dolls & Playsets Clearance",
        "url": "https://www.walmart.ca/en/search?q=dolls&facet=special_offers:Clearance||retailer:Walmart"
    },
    {
        "name": "Vehicles & Hot Wheels Clearance",
        "url": "https://www.walmart.ca/en/search?q=vehicles&facet=special_offers:Clearance||retailer:Walmart"
    }
]

# ==========================================
# HELPER FUNCTIONS
# ==========================================
def load_seen_deals():
    """Loads existing deal IDs from seen_deals.json if present."""
    if os.path.exists(SEEN_DEALS_FILE):
        try:
            with open(SEEN_DEALS_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception as e:
            print(f"[!] Warning: Failed to load {SEEN_DEALS_FILE}: {e}")
    return set()

def save_seen_deals(seen_ids):
    """Saves updated deal IDs to seen_deals.json."""
    try:
        with open(SEEN_DEALS_FILE, "w", encoding="utf-8") as f:
            json.dump(list(seen_ids), f, indent=2)
        print(f"[+] Saved {len(seen_ids)} total deal IDs to {SEEN_DEALS_FILE}")
    except Exception as e:
        print(f"[!] Error saving {SEEN_DEALS_FILE}: {e}")

def send_telegram_alert(message):
    """Sends notification to Telegram group or chat."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[!] Telegram credentials missing. Skipping notification.")
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False
    }
    try:
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code == 200:
            print("[+] Telegram alert sent successfully!")
        else:
            print(f"[!] Telegram API error ({res.status_code}): {res.text}")
    except Exception as e:
        print(f"[!] Exception while sending Telegram alert: {e}")

def parse_prices_from_text(card_text):
    """
    Extracts 'Now' price and calculates original 'Was' price using both
    explicit strikethroughs and 'Now $X + You save $Y' fallbacks.
    """
    now_price = None
    was_price = None

    # Extract Now Price
    now_match = re.search(r'Now\s*\$?([0-9]+\.?[0-9]*)', card_text, re.IGNORECASE)
    if now_match:
        now_price = float(now_match.group(1))
    else:
        # Generic price extraction fallback
        price_match = re.search(r'\$([0-9]+\.[0-9]{2})', card_text)
        if price_match:
            now_price = float(price_match.group(1))

    # Try finding explicit Was / Regular Price
    was_match = re.search(r'(?:Was|Strikethrough|Comp)\s*\$?([0-9]+\.?[0-9]*)', card_text, re.IGNORECASE)
    if was_match:
        was_price = float(was_match.group(1))

    # Fallback: Calculate Was Price via "You save $X.XX"
    if now_price and not was_price:
        save_match = re.search(r'You\s*save\s*\$?([0-9]+\.?[0-9]*)', card_text, re.IGNORECASE)
        if save_match:
            saved_amount = float(save_match.group(1))
            was_price = now_price + saved_amount

    # Calculate Discount
    if now_price and was_price and was_price > now_price:
        discount_pct = ((was_price - now_price) / was_price) * 100.0
    else:
        discount_pct = 0.0

    return now_price, was_price, round(discount_pct, 1)

# ==========================================
# MAIN EXECUTION
# ==========================================
def main():
    if not SCRAPER_API_KEY:
        print("[!] SCRAPER_API_KEY environment variable is missing. Exiting.")
        return

    seen_deals = load_seen_deals()
    new_deals_found = 0

    print(f"[*] Starting scan across {len(CATEGORIES_TO_SCRAPE)} category endpoints...")

    for category in CATEGORIES_TO_SCRAPE:
        cat_name = category["name"]
        target_url = category["url"]

        print(f"\nScanning category: {cat_name}...")

        # ScraperAPI Payload Configuration
        scraper_api_url = "http://api.scraperapi.com"
        params = {
            "api_key": SCRAPER_API_KEY,
            "url": target_url,
            "render": "false",
            "country_code": "ca"
        }

        try:
            resp = requests.get(scraper_api_url, params=params, timeout=60)
            if resp.status_code != 200:
                print(f"  [!] Failed to fetch URL (Status {resp.status_code}): {target_url}")
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            
            # Locate product tiles on Walmart Canada
            product_cards = soup.find_all("div", {"data-item-id": True})
            if not product_cards:
                # Alternative fallback selector for Walmart Canada DOM
                product_cards = soup.find_all("div", {"class": lambda x: x and "sans-serif" in x and "mb1" in x})

            print(f"  Found {len(product_cards)} candidate product cards.")

            for card in product_cards:
                # Retrieve item unique ID
                item_id = card.get("data-item-id")
                if not item_id:
                    link_tag = card.find("a", href=True)
                    if link_tag:
                        id_match = re.search(r'/([0-9]{8,15})', link_tag["href"])
                        if id_match:
                            item_id = id_match.group(1)

                if not item_id:
                    continue

                card_text = card.get_text(separator=" ")

                # Parse pricing structure
                now_price, was_price, discount_pct = parse_prices_from_text(card_text)

                # Diagnostic output
                if discount_pct > 0:
                    print(f"    [Card ID: {item_id}] Now: ${now_price} | Was: ${was_price} | Savings: {discount_pct}%")

                # Filter Rule 1: Minimum Discount Threshold
                if discount_pct < MIN_DISCOUNT_PERCENT:
                    continue

                # Filter Rule 2: Already Processed Deal Check
                if item_id in seen_deals:
                    print(f"    [Skipped] Item ID {item_id} already sent previously.")
                    continue

                # Extract Title and Link
                title = "Walmart Clearance Item"
                title_tag = card.find("span", {"data-automation-id": "product-title"}) or card.find("a")
                if title_tag:
                    title = title_tag.get_text(strip=True)

                link = f"https://www.walmart.ca/en/ip/{item_id}"
                link_tag = card.find("a", href=True)
                if link_tag and link_tag["href"].startswith("http"):
                    link = link_tag["href"]
                elif link_tag and link_tag["href"].startswith("/"):
                    link = f"https://www.walmart.ca{link_tag['href']}"

                # Format and Dispatch Alert
                alert_msg = (
                    f"🚨 *WALMART CLEARANCE DEAL FOUND!* 🚨\n\n"
                    f"📦 *Product:* {title}\n"
                    f"💰 *Now Price:* ${now_price:.2f}\n"
                    f"🏷️ *Was Price:* ${was_price:.2f}\n"
                    f"🔥 *Discount:* {discount_pct}%\n\n"
                    f"🔗 [View Product on Walmart.ca]({link})"
                )

                print(f"    [!] MATCH FOUND: {title} ({discount_pct}% off). Sending Telegram alert...")
                send_telegram_alert(alert_msg)

                # Track Seen Deal
                seen_deals.add(item_id)
                new_deals_found += 1

        except Exception as e:
            print(f"  [!] Error parsing category {cat_name}: {e}")

    # Persist updated deal list to repo
    save_seen_deals(seen_deals)
    print(f"\n[*] Run complete. Total new alerts dispatched: {new_deals_found}")

if __name__ == "__main__":
    main()
