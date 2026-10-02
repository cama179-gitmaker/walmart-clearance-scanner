import json
import re
import os
import requests
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

# Configuration
SEEN_DEALS_FILE = "seen_deals.json"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

CATEGORIES = {
    "Toys Main Category": "https://www.walmart.com/browse/toys/4171",
    "Building Sets & LEGO": "https://www.walmart.com/browse/toys/lego-building-sets/4171_4186",
    "Action Figures": "https://www.walmart.com/browse/toys/action-figures/4171_4172",
}

def load_seen_deals():
    if os.path.exists(SEEN_DEALS_FILE):
        try:
            with open(SEEN_DEALS_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def save_seen_deals(deals):
    with open(SEEN_DEALS_FILE, "w") as f:
        json.dumps(list(deals), f)

def send_telegram_alert(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[!] Missing Telegram secrets, skipping alert.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"[!] Telegram send error: {e}")

def parse_next_data(html):
    """Extract products directly from Walmart's __NEXT_DATA__ JSON script tag."""
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        return []

    try:
        data = json.loads(script.string)
        # Traverse Next.js search state
        item_stacks = (
            data.get("props", {})
            .get("pageProps", {})
            .get("initialData", {})
            .get("searchResult", {})
            .get("itemStacks", [])
        )
        
        products = []
        for stack in item_stacks:
            for item in stack.get("items", []):
                if item.get("__typename") == "Product":
                    products.append(item)
        return products
    except Exception as e:
        print(f"  [!] Error parsing JSON state: {e}")
        return []

def scan_walmart():
    seen_deals = load_seen_deals()
    new_alerts = 0

    print("[*] Starting browser-impersonated scan via curl_cffi...")

    for cat_name, url in CATEGORIES.items():
        print(f"\nScanning category: {cat_name}...")
        try:
            # Impersonate Chrome 120 TLS fingerprint
            response = cffi_requests.get(
                url,
                impersonate="chrome120",
                headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Referer": "https://www.walmart.com/",
                },
                timeout=15
            )

            if response.status_code != 200:
                print(f"  [HTTP {response.status_code}] Failed to fetch page.")
                continue

            products = parse_next_data(response.text)
            print(f"  [HTTP 200] Extracted {len(products)} products from JSON.")

            for prod in products:
                us_item_id = prod.get("usItemId") or prod.get("id")
                title = prod.get("name") or "Unknown Product"
                price_info = prod.get("priceInfo", {}).get("linePrice", "")
                product_url = f"https://www.walmart.com{prod.get('canonicalUrl', '')}"

                if us_item_id and us_item_id not in seen_deals:
                    seen_deals.add(us_item_id)
                    # Check if marked as clearance/reduced
                    is_clearance = prod.get("badge", {}).get("text", "").lower() == "clearance"
                    
                    if is_clearance:
                        msg = f"<b>Clearance Deal Found!</b>\n\n<b>Title:</b> {title}\n<b>Price:</b> {price_info}\n<b>Link:</b> {product_url}"
                        send_telegram_alert(msg)
                        new_alerts += 1

        except Exception as e:
            print(f"  [!] Category fetch failed: {e}")

    save_seen_deals(seen_deals)
    print(f"\n[+] Saved {len(seen_deals)} total deal IDs to {SEEN_DEALS_FILE}")
    print(f"[*] Run complete. Total new alerts dispatched: {new_alerts}")

if __name__ == "__main__":
    scan_walmart()
