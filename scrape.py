<<<<<<< HEAD
import requests
from bs4 import BeautifulSoup
import time
from supabase import create_client, Client
import re
import os
import json
from dotenv import load_dotenv

# Selenium imports
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from json import JSONDecodeError

# --------------------
# Environment / Supabase
# --------------------
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TABLE_NAME = "books_nonfiction"

if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ ERROR: Missing Supabase credentials.")
    exit()

try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ Supabase client initialized.")
except Exception as e:
    print(f"❌ Failed to initialize Supabase: {e}")
    exit()

# --------------------
# Cookie & Session Setup
# --------------------
COOKIE_FILENAME = "goodreads_cookies.json"
session = requests.Session()
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/91.0.4472.124 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9'
}

def selenium_manual_login_and_save(url="https://www.goodreads.com/"):
    """Open Chrome, let the user log in manually, then save cookies to file."""
    print("🔓 Launching Chrome for manual Goodreads login...")
    service = Service(ChromeDriverManager().install())
    options = webdriver.ChromeOptions()
    driver = webdriver.Chrome(service=service, options=options)
    driver.maximize_window()
    driver.get(url)

    print("➡️ Please log in to Goodreads in the opened browser window.")
    input("✅ Press Enter here AFTER you have logged in successfully...")

    cookies = driver.get_cookies()
    out = []
    for c in cookies:
        out.append({
            "name": c.get("name"),
            "value": c.get("value"),
            "domain": c.get("domain", ""),
            "path": c.get("path", "/")
        })

    with open(COOKIE_FILENAME, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"✅ Saved {len(out)} cookies to {COOKIE_FILENAME}")
    driver.quit()

def load_cookies_into_session():
    """Load cookies into requests session. If file missing/invalid, trigger Selenium login."""
    def read_cookie_file():
        with open(COOKIE_FILENAME, "r", encoding="utf-8") as f:
            raw = f.read()
            if not raw.strip():
                raise JSONDecodeError("Empty cookie file", raw, 0)
            return json.loads(raw)

    try:
        cookies = read_cookie_file()
    except (FileNotFoundError, JSONDecodeError):
        selenium_manual_login_and_save()
        cookies = read_cookie_file()

    loaded = 0
    for ck in cookies:
        name = ck.get("name")
        value = ck.get("value")
        domain = ck.get("domain", "")
        path = ck.get("path", "/")
        if not name or value is None:
            continue
        # Set for multiple domain variants
        domain_variants = {domain.strip(), domain.lstrip(".").strip(),
                           "www.goodreads.com", "goodreads.com"}
        for d in domain_variants:
            if not d:
                continue
            try:
                session.cookies.set(name, value, domain=d, path=path)
            except Exception:
                pass
        loaded += 1
    print(f"✅ Loaded {loaded} cookies into session.")

def check_logged_in():
    """Return True if session appears logged in."""
    r = session.get("https://www.goodreads.com/", headers=HEADERS, timeout=10)
    if "sign_in" in r.url or "user/sign_in" in r.url:
        return False
    text = r.text.lower()
    return ("sign out" in text or "/user/show" in text or "my books" in text)

# --------------------
# Scraper Logic
# --------------------
def scrape_goodreads_self_improvement():
    print("\n🚀 Starting self-improvement shelf scraping...")

    # Ensure session is authenticated
    load_cookies_into_session()
    if not check_logged_in():
        print("⚠️ Not logged in after loading cookies. Retrying login...")
        selenium_manual_login_and_save()
        load_cookies_into_session()
        if not check_logged_in():
            print("❌ Could not log in. Will proceed without login (only first page available).")

    base_url = "https://www.goodreads.com/shelf/show/self-improvement"
    page = 1
    empty_streak = 0
    max_empty_streak = 3

    total_books_scraped = 0
    total_books_filtered = 0
    total_books_inserted = 0

    while empty_streak < max_empty_streak:
        url = f"{base_url}?page={page}"
        print(f"\n📄 Scraping Page {page}: {url}")

        try:
            response = session.get(url, headers=HEADERS, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')

            # detect login redirect
            if "sign_in" in response.url or "user/sign_in" in response.url:
                print("⚠️ Redirected to sign-in page. Stopping further scraping.")
                break

            book_elements = soup.find_all('div', class_='elementList')
            if not book_elements:
                print("   ⚠️ No books found on this page.")
                empty_streak += 1
                page += 1
                continue

            page_filtered = 0

            for book_element in book_elements:
                title_el = book_element.find('a', class_='bookTitle')
                author_el = book_element.find('a', class_='authorName')
                rating_el = book_element.find('span', class_='greyText', string=re.compile(r'avg rating'))

                if title_el and author_el and rating_el:
                    total_books_scraped += 1
                    title = title_el.get_text(strip=True)
                    author = author_el.get_text(strip=True)
                    rating_text = rating_el.get_text(strip=True)

                    avg_rating = float(re.search(r'avg rating ([\d.]+)', rating_text).group(1)) if re.search(r'avg rating ([\d.]+)', rating_text) else 0.0
                    ratings_count = int(re.search(r'([\d,]+) ratings', rating_text).group(1).replace(',', '')) if re.search(r'([\d,]+) ratings', rating_text) else 0

                    if avg_rating >= 3.8 and ratings_count >= 100_000:
                        total_books_filtered += 1
                        page_filtered += 1

                        print(f"   ✅ {title} by {author} — {avg_rating} stars, {ratings_count} ratings")

                        existing = supabase.from_(TABLE_NAME).select("id").eq("title", title).eq("author", author).execute()

                        if not existing.data:
                            insert_res = supabase.from_(TABLE_NAME).insert({
                                "title": title,
                                "author": author,
                                "avg_rating": avg_rating,
                                "ratings_count": ratings_count
                            }).execute()

                            if insert_res.data:
                                total_books_inserted += 1
                                print("      ✅ Inserted.")
                            else:
                                print("      ⚠️ Insert failed.")
                        else:
                            print("       Already exists.")

            if page_filtered == 0:
                empty_streak += 1
                print(f"    No new books passed filter. Streak: {empty_streak}/{max_empty_streak}")
            else:
                empty_streak = 0

            page += 1
            time.sleep(2)

        except Exception as e:
            print(f"❌ Error on page {page}: {e}")
            break

    print("\n📊 Scraping finished:")
    print(f"   Total scraped:  {total_books_scraped}")
    print(f"   Passed filter:  {total_books_filtered}")
    print(f"   Inserted:       {total_books_inserted}")

# --------------------
# Main
# --------------------
if __name__ == "__main__":
    scrape_goodreads_self_improvement()
=======
import requests
from bs4 import BeautifulSoup
import time
from supabase import create_client, Client
import re
import os
import json
from dotenv import load_dotenv

# Selenium imports
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from json import JSONDecodeError

# --------------------
# Environment / Supabase
# --------------------
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TABLE_NAME = "books_nonfiction"

if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ ERROR: Missing Supabase credentials.")
    exit()

try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ Supabase client initialized.")
except Exception as e:
    print(f"❌ Failed to initialize Supabase: {e}")
    exit()

# --------------------
# Cookie & Session Setup
# --------------------
COOKIE_FILENAME = "goodreads_cookies.json"
session = requests.Session()
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/91.0.4472.124 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9'
}

def selenium_manual_login_and_save(url="https://www.goodreads.com/"):
    """Open Chrome, let the user log in manually, then save cookies to file."""
    print("🔓 Launching Chrome for manual Goodreads login...")
    service = Service(ChromeDriverManager().install())
    options = webdriver.ChromeOptions()
    driver = webdriver.Chrome(service=service, options=options)
    driver.maximize_window()
    driver.get(url)

    print("➡️ Please log in to Goodreads in the opened browser window.")
    input("✅ Press Enter here AFTER you have logged in successfully...")

    cookies = driver.get_cookies()
    out = []
    for c in cookies:
        out.append({
            "name": c.get("name"),
            "value": c.get("value"),
            "domain": c.get("domain", ""),
            "path": c.get("path", "/")
        })

    with open(COOKIE_FILENAME, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"✅ Saved {len(out)} cookies to {COOKIE_FILENAME}")
    driver.quit()

def load_cookies_into_session():
    """Load cookies into requests session. If file missing/invalid, trigger Selenium login."""
    def read_cookie_file():
        with open(COOKIE_FILENAME, "r", encoding="utf-8") as f:
            raw = f.read()
            if not raw.strip():
                raise JSONDecodeError("Empty cookie file", raw, 0)
            return json.loads(raw)

    try:
        cookies = read_cookie_file()
    except (FileNotFoundError, JSONDecodeError):
        selenium_manual_login_and_save()
        cookies = read_cookie_file()

    loaded = 0
    for ck in cookies:
        name = ck.get("name")
        value = ck.get("value")
        domain = ck.get("domain", "")
        path = ck.get("path", "/")
        if not name or value is None:
            continue
        # Set for multiple domain variants
        domain_variants = {domain.strip(), domain.lstrip(".").strip(),
                           "www.goodreads.com", "goodreads.com"}
        for d in domain_variants:
            if not d:
                continue
            try:
                session.cookies.set(name, value, domain=d, path=path)
            except Exception:
                pass
        loaded += 1
    print(f"✅ Loaded {loaded} cookies into session.")

def check_logged_in():
    """Return True if session appears logged in."""
    r = session.get("https://www.goodreads.com/", headers=HEADERS, timeout=10)
    if "sign_in" in r.url or "user/sign_in" in r.url:
        return False
    text = r.text.lower()
    return ("sign out" in text or "/user/show" in text or "my books" in text)

# --------------------
# Scraper Logic
# --------------------
def scrape_goodreads_self_improvement():
    print("\n🚀 Starting self-improvement shelf scraping...")

    # Ensure session is authenticated
    load_cookies_into_session()
    if not check_logged_in():
        print("⚠️ Not logged in after loading cookies. Retrying login...")
        selenium_manual_login_and_save()
        load_cookies_into_session()
        if not check_logged_in():
            print("❌ Could not log in. Will proceed without login (only first page available).")

    base_url = "https://www.goodreads.com/shelf/show/self-improvement"
    page = 1
    empty_streak = 0
    max_empty_streak = 3

    total_books_scraped = 0
    total_books_filtered = 0
    total_books_inserted = 0

    while empty_streak < max_empty_streak:
        url = f"{base_url}?page={page}"
        print(f"\n📄 Scraping Page {page}: {url}")

        try:
            response = session.get(url, headers=HEADERS, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')

            # detect login redirect
            if "sign_in" in response.url or "user/sign_in" in response.url:
                print("⚠️ Redirected to sign-in page. Stopping further scraping.")
                break

            book_elements = soup.find_all('div', class_='elementList')
            if not book_elements:
                print("   ⚠️ No books found on this page.")
                empty_streak += 1
                page += 1
                continue

            page_filtered = 0

            for book_element in book_elements:
                title_el = book_element.find('a', class_='bookTitle')
                author_el = book_element.find('a', class_='authorName')
                rating_el = book_element.find('span', class_='greyText', string=re.compile(r'avg rating'))

                if title_el and author_el and rating_el:
                    total_books_scraped += 1
                    title = title_el.get_text(strip=True)
                    author = author_el.get_text(strip=True)
                    rating_text = rating_el.get_text(strip=True)

                    avg_rating = float(re.search(r'avg rating ([\d.]+)', rating_text).group(1)) if re.search(r'avg rating ([\d.]+)', rating_text) else 0.0
                    ratings_count = int(re.search(r'([\d,]+) ratings', rating_text).group(1).replace(',', '')) if re.search(r'([\d,]+) ratings', rating_text) else 0

                    if avg_rating >= 3.8 and ratings_count >= 100_000:
                        total_books_filtered += 1
                        page_filtered += 1

                        print(f"   ✅ {title} by {author} — {avg_rating} stars, {ratings_count} ratings")

                        existing = supabase.from_(TABLE_NAME).select("id").eq("title", title).eq("author", author).execute()

                        if not existing.data:
                            insert_res = supabase.from_(TABLE_NAME).insert({
                                "title": title,
                                "author": author,
                                "avg_rating": avg_rating,
                                "ratings_count": ratings_count
                            }).execute()

                            if insert_res.data:
                                total_books_inserted += 1
                                print("      ✅ Inserted.")
                            else:
                                print("      ⚠️ Insert failed.")
                        else:
                            print("       Already exists.")

            if page_filtered == 0:
                empty_streak += 1
                print(f"    No new books passed filter. Streak: {empty_streak}/{max_empty_streak}")
            else:
                empty_streak = 0

            page += 1
            time.sleep(2)

        except Exception as e:
            print(f"❌ Error on page {page}: {e}")
            break

    print("\n📊 Scraping finished:")
    print(f"   Total scraped:  {total_books_scraped}")
    print(f"   Passed filter:  {total_books_filtered}")
    print(f"   Inserted:       {total_books_inserted}")

# --------------------
# Main
# --------------------
if __name__ == "__main__":
    scrape_goodreads_self_improvement()
>>>>>>> ea06f5525dee1ad2eaf082ee1980d147aeee5f2c
