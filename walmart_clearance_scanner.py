import os
import json
import time
import requests
from playwright.sync_api import sync_playwright

# Configuration
SEEN_DEALS_FILE = "seen_deals.json"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
MIN_DISCOUNT_PERCENT = 25.0

CATEGORIES = [
    {
        "name": "Toys Main Category",
        "url": "https://www.walmart.ca/en/browse/toys/10011?facet=retailer:Walmart&page=1"
    },
    {
        "name": "Building Sets & LEGO",
        "url": "https://www.walmart.ca/en/browse/toys/building-sets-blocks/10011-20108?facet=retailer:Walmart"
    },
    {
        "name": "Dolls & Dollhouses",
        "url": "https://www.walmart.ca/en/browse/toys/dolls-dollhouses/10011-20109?facet=retailer:Walmart"
    },
    {
        "name": "Action Figures",
        "url": "https://www.walmart.ca/en/browse/toys/action-figures-playsets/10011-20107?facet=retailer:Walmart"
    }
]

def load_seen_deals():
    if os.path.exists(SEEN_DEALS_FILE):
        try:
            with open(SEEN_DEALS_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def save_seen_deals(deals):
    try:
        with open(SEEN_DEALS_FILE, "w", encoding="utf-8") as f:
            json.dump(list(deals), f, indent=2)
        print(f"[+] Saved {len(deals)} total deal IDs to {SEEN_DEALS_FILE}")
    except Exception as e:
        print(f"[!] Error saving {SEEN_DEALS_FILE}: {e}")

def send_telegram_alert(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("    [!] Missing Telegram secrets, skipping alert.")
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
            print("    [+] Telegram alert sent successfully!")
        else:
            print(f"    [!] Telegram error ({res.status_code}): {res.text}")
    except Exception as e:
        print(f"    [!] Exception sending Telegram alert: {e}")

def extract_items_from_json(data):
    items = []
    if isinstance(data, dict):
        if "itemStacks" in data:
            for stack in data.get("itemStacks", []):
                items.extend(stack.get("items", []))
        elif "items" in data and isinstance(data["items"], list):
            items.extend(data["items"])
        else:
            for val in data.values():
                items.extend(extract_items_from_json(val))
    elif isinstance(data, list):
        for elem in data:
            items.extend(extract_items_from_json(elem))
    return items

def scan_walmart():
    seen_deals = load_seen_deals()
    new_alerts = 0

    print("[*] Launching Playwright with network interception...")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
            ]
        )

        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="en-CA",
        )

        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = context.new_page()

        for category in CATEGORIES:
            cat_name = category["name"]
            url = category["url"]
            intercepted_products = []

            # Handle background JSON responses
            def handle_response(response):
                try:
                    if "application/json" in response.headers.get("content-type", ""):
                        # Intercept search or page data payloads
                        if "search" in response.url or "browse" in response.url or "graphql" in response.url:
                            data = response.json()
                            extracted = extract_items_from_json(data)
                            if extracted:
                                intercepted_products.extend(extracted)
                except Exception:
                    pass

            page.on("response", handle_response)

            print(f"\nScanning category: {cat_name}...")
            try:
                page.goto(url, timeout=45000, wait_until="networkidle")
                
                # Scroll down slightly to trigger lazy-loaded catalog requests
                page.evaluate("window.scrollBy(0, 1000);")
                time.sleep(3)

                print(f"  [HTTP 200] Intercepted {len(intercepted_products)} raw items via API responses.")

                for prod in intercepted_products:
                    if not isinstance(prod, dict):
                        continue

                    us_item_id = prod.get("usItemId") or prod.get("id") or prod.get("skuId")
                    if not us_item_id or str(us_item_id) in seen_deals:
                        continue

                    title = prod.get("name") or prod.get("title") or "Walmart Product"
                    price_info = prod.get("priceInfo", {})
                    
                    now_price = None
                    was_price = None
                    
                    if isinstance(price_info, dict):
                        current_price_obj = price_info.get("currentPrice", {})
                        if isinstance(current_price_obj, dict):
                            now_price = current_price_obj.get("price")
                        
                        was_price_obj = price_info.get("wasPrice", {})
                        if isinstance(was_price_obj, dict):
                            was_price = was_price_obj.get("price")

                    if now_price and was_price and was_price > now_price:
                        discount_pct = round(((was_price - now_price) / was_price) * 100.0, 1)
                    else:
                        discount_pct = 0.0

                    if discount_pct >= MIN_DISCOUNT_PERCENT:
                        canonical_url = prod.get("canonicalUrl", "")
                        product_url = f"https://www.walmart.ca{canonical_url}" if canonical_url.startswith("/") else f"https://www.walmart.ca/en/ip/{us_item_id}"

                        alert_msg = (
                            f"🚨 *WALMART DEAL FOUND (≥{MIN_DISCOUNT_PERCENT:.0f}% OFF)* 🚨\n\n"
                            f"📦 *Product:* {title}\n"
                            f"💰 *Now Price:* ${now_price:.2f}\n"
                            f"🏷️ *Was Price:* ${was_price:.2f}\n"
                            f"🔥 *Discount:* {discount_pct}%\n\n"
                            f"🔗 [View Product on Walmart.ca]({product_url})"
                        )

                        print(f"    [!] MATCH FOUND: {title} ({discount_pct}% off). Sending alert...")
                        send_telegram_alert(alert_msg)

                        seen_deals.add(str(us_item_id))
                        new_alerts += 1

            except Exception as e:
                print(f"  [!] Exception during category scan: {e}")

            # Remove listener for next category iteration
            page.remove_listener("response", handle_response)

        browser.close()

    save_seen_deals(seen_deals)
    print(f"\n[*] Run complete. Total new alerts dispatched: {new_alerts}")

if __name__ == "__main__":
    scan_walmart()
