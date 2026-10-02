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
        json.dump(list(deals), f, indent=2)

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

def extract_items_from_json(data):
    """Recursively traverse JSON structure to extract item dictionaries."""
    items = []
    
    if isinstance(data, dict):
        # Look for common Walmart JSON array containers
        if "itemStacks" in data:
            for stack in data.get("itemStacks", []):
                items.extend(stack.get("items", []))
        elif "items" in data and isinstance(data["items"], list):
            items.extend(data["items"])
        else:
            for key, val in data.items():
                items.extend(extract_items_from_json(val))
    elif isinstance(data, list):
        for elem in data:
            items.extend(extract_items_from_json(elem))
            
    return items

def parse_walmart_page(html):
    """Extract products directly from script tags or JSON state."""
    soup = BeautifulSoup(html, "html.parser")
    products = []

    # Strategy 1: Check __NEXT_DATA__
    script = soup.find("script", id="__NEXT_DATA__")
    if script and script.string:
        try:
            data = json.loads(script.string)
            extracted = extract_items_from_json(data)
            products.extend(extracted)
        except Exception as e:
            print(f"  [!] Failed parsing __NEXT_DATA__: {e}")

    # Strategy 2: Search for raw JSON blobs in inline script tags
    if not products:
        scripts = soup.find_all("script", type="application/json")
        for s in scripts:
            if s.string and "itemStacks" in s.string:
                try:
                    data = json.loads(s.string)
                    extracted = extract_items_from_json(data)
                    products.extend(extracted)
                except Exception:
                    continue

    return products

def scan_walmart():
    seen_deals = load_seen_deals()
    new_alerts = 0

    print("[*] Starting browser-impersonated scan via curl_cffi...")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.walmart.com/",
    }

    for cat_name, url in CATEGORIES.items():
        print(f"\nScanning category: {cat_name}...")
        try:
            response = cffi_requests.get(
                url,
                impersonate="chrome120",
                headers=headers,
                timeout=15
            )

            if response.status_code != 200:
                print(f"  [HTTP {response.status_code}] Blocked or missing page.")
                continue

            products = parse_walmart_page(response.text)
            print(f"  [HTTP 200] Extracted {len(products)} candidate items from JSON.")

            for prod in products:
                if not isinstance(prod, dict):
                    continue

                us_item_id = prod.get("usItemId") or prod.get("id")
                if not us_item_id:
                    continue

                title = prod.get("name") or prod.get("title") or "Unknown Product"
                price_info = prod.get("priceInfo", {})
                price = price_info.get("linePrice") if isinstance(price_info, dict) else ""
                
                canonical_url = prod.get("canonicalUrl", "")
                product_url = f"https://www.walmart.com{canonical_url}" if canonical_url else url

                if us_item_id not in seen_deals:
                    seen_deals.add(str(us_item_id))
                    
                    # Detect clearance status
                    badge_text = ""
                    badge = prod.get("badge")
                    if isinstance(badge, dict):
                        badge_text = badge.get("text", "")
                    
                    is_clearance = "clearance" in badge_text.lower() or "reduced" in badge_text.lower()

                    if is_clearance:
                        msg = (
                            f"<b>Clearance Deal Found!</b>\n\n"
                            f"<b>Title:</b> {title}\n"
                            f"<b>Price:</b> {price}\n"
                            f"<b>Link:</b> {product_url}"
                        )
                        send_telegram_alert(msg)
                        new_alerts += 1

        except Exception as e:
            print(f"  [!] Category fetch failed: {e}")

    save_seen_deals(seen_deals)
    print(f"\n[+] Saved {len(seen_deals)} total deal IDs to {SEEN_DEALS_FILE}")
    print(f"[*] Run complete. Total new alerts dispatched: {new_alerts}")

if __name__ == "__main__":
    scan_walmart()
