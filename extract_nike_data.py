import logging
import random
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from math import ceil
from threading import Lock
from urllib.parse import parse_qs, urlparse

import pandas as pd
# import requests
from bs4 import BeautifulSoup
from curl_cffi import requests as curl_cffi_requests
from tqdm.auto import tqdm

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("crawler.log"), logging.StreamHandler()],
)

# Database setup
DB_PATH = "nike_tracker.db"
URLS = [
    "https://www.nike.com/in/w/mens-shoes-nik1zy7ok",
    "https://www.nike.com/in/w/mens-clothing-6ymx6znik1",
    "https://www.nike.com/in/w/mens-accessories-equipment-awwpwznik1",
    "https://www.nike.com/in/w/womens-shoes-5e1x6zy7ok",
    "https://www.nike.com/in/w/womens-clothing-5e1x6z6ymx6",
    "https://www.nike.com/in/w/womens-accessories-equipment-5e1x6zawwpw",
    "https://www.nike.com/in/w/kids-shoes-v4dhzy7ok",
    "https://www.nike.com/in/w/kids-clothing-6ymx6zv4dh",
    "https://www.nike.com/in/w/kids-accessories-equipment-awwpwzv4dh",
]


def init_db():
    """Initialize SQLite database with required tables"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Create products table
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS products (
            product_code TEXT PRIMARY KEY,
            title TEXT,
            subtitle TEXT,
            category TEXT,
            image_url TEXT,
            url TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """
    )

    # Create price_history table
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_code TEXT,
            price REAL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (product_code) REFERENCES products(product_code)
        )
    """
    )

    conn.commit()
    conn.close()
    logging.info("Database initialized successfully")


def update_database(df):
    """Update database with new product data"""
    if df.empty:
        logging.warning("No data to update")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # timestamp=(datetime.now() + timedelta(hours=13)).strftime("%Y-%m-%d %H:%M:%S") # for testing

    try:
        columns_mapping = {
            "productCode": "product_code",
            "copy.title": "title",
            "copy.subTitle": "subtitle",
            "category": "category",
            "colorwayImages.portraitURL": "image_url",
            "pdpUrl.url": "url",
        }

        # Prepare data for products table with explicit column selection
        products_data = df[list(columns_mapping.keys())].copy()
        products_data.columns = list(columns_mapping.values())

        # Debug logging
        logging.info(f"Columns in products_data: {products_data.columns.tolist()}")

        # Update products table (manual upsert)
        for idx, row in products_data.iterrows():
            # Debug logging for first row
            if idx == 0:
                logging.info(f"Sample row data: {dict(row)}")

            cursor.execute(
                """
                INSERT OR REPLACE INTO products 
                (product_code, title, subtitle, category, image_url, url)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (
                    row["product_code"],
                    row["title"],
                    row["subtitle"],
                    row["category"],
                    row["image_url"],
                    row["url"],
                ),
            )

        # Prepare and insert price history
        for _, row in df.iterrows():
            cursor.execute(
                """
                INSERT INTO price_history (product_code, price, timestamp)
                VALUES (?, ?, ?)
            """,
                (row["productCode"], row["prices.currentPrice"], timestamp),
            )

        conn.commit()
        logging.info(
            f"Updated {len(products_data)} products and added {len(df)} price entries"
        )

    except Exception as e:
        logging.error(f"Error updating database: {str(e)}")
        # Additional error context
        if "row" in locals():
            logging.error(f"Failed row data: {dict(row)}")
        conn.rollback()

    finally:
        cursor.close()
        conn.close()


def get_headers(type="html"):
    """Return appropriate headers based on request type"""
    if type == "html":
        return {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "accept-language": "en-US,en;q=0.9",
            "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
        }
    else:  # API headers
        return {
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.9",
            "nike-api-caller-id": "nike:dotcom:browse:wall.client:2.0",
            "origin": "https://www.nike.com",
            "referer": "https://www.nike.com/",
            "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
        }


# Thread-safe progress bar update
progress_lock = Lock()


def extract_concept_ids(session, url):
    """Extract concept IDs from Nike category page"""
    try:
        with progress_lock:
            print(f"\nFetching HTML from: {url}")
        response = session.get(url, headers=get_headers("html"))
        soup = BeautifulSoup(response.text, "html.parser")

        meta_tag = soup.find("meta", {"name": "branch:deeplink:$deeplink_path"})
        if not meta_tag or not meta_tag.get("content"):
            with progress_lock:
                print(f"No concept IDs found for {url}")
            return None

        deeplink_path = meta_tag["content"]
        query_params = parse_qs(urlparse(deeplink_path).query)

        if "conceptid" not in query_params:
            with progress_lock:
                print(f"No concept IDs in meta tag for {url}")
            return None

        return query_params["conceptid"][0]

    except Exception as e:
        with progress_lock:
            print(f"Error extracting concept IDs for {url}: {str(e)}")
        return None


def parse_response(response_json):
    """Parse API response and extract product information"""
    try:
        products_df = pd.json_normalize(
            [
                i.get("products")[0]
                for i in response_json["productGroupings"]
                if i.get("products")
            ]
        )[
            [
                "productCode",
                "badgeLabel",
                "copy.title",
                "copy.subTitle",
                "prices.currency",
                "prices.currentPrice",
                "pdpUrl.url",
                "colorwayImages.portraitURL",
            ]
        ]

        # Add random price variation of ±10% (only to demonstrate how pricing graph would look like when actual prices change)
        # products_df["prices.currentPrice"] = products_df["prices.currentPrice"].apply(
        #     lambda x: x + (random.uniform(-0.1, 0.1) * x)
        # )

        return [products_df]
    except Exception as e:
        with progress_lock:
            print(f"Error parsing response: {str(e)}")
        return []


def fetch_nike_products(session, url, category):
    """Fetch all products for a given category URL"""
    try:
        headers = get_headers("api")

        # Get initial response
        initial_response = session.get(url, headers=headers)
        data = initial_response.json()

        total_products = data["pages"]["totalResources"]
        count = 100  # Maximum allowed count
        total_pages = ceil(total_products / count) - 1

        all_products = []
        next_page = data["pages"].get("next")

        # Parse and add initial products
        initial_products = parse_response(data)
        all_products.extend(initial_products)

        # Create progress bar for pagination
        with tqdm(
            total=total_pages,
            desc=f"Fetching {category}",
            leave=False,
            initial=1,
            position=0,  # Ensure proper positioning in parallel execution
        ) as pbar:
            while next_page:
                time.sleep(random.uniform(1, 3))  # Keep rate limiting

                next_url = f"https://api.nike.com{next_page}"

                response = session.get(next_url, headers=headers)
                data = response.json()

                new_products = parse_response(data)
                all_products.extend(new_products)

                next_page = data["pages"].get("next")

                if next_page:
                    with progress_lock:
                        pbar.update(1)

        if all_products:
            df = pd.concat(all_products, ignore_index=True)
            df["category"] = category
            return df

    except Exception as e:
        with progress_lock:
            print(f"Error fetching products for {category}: {str(e)}")

    return pd.DataFrame()


CONSUMER_CHANNEL_ID = "d9a5bc42-4b9c-4976-858a-f159cf99c647"


def construct_api_url(concept_ids, path):
    """Construct Nike API URL with concept IDs"""
    base_url = "https://api.nike.com/discover/product_wall/v1"
    params = {"marketplace": "IN", "language": "en-GB", "count": 100, "anchor": 0}

    url = (
        f"{base_url}/marketplace/{params['marketplace']}/language/{params['language']}"
    )
    url += f"/consumerChannelId/{CONSUMER_CHANNEL_ID}"
    url += f"?path={path}"
    url += f"&attributeIds={concept_ids}"
    url += f"&queryType=PRODUCTS&anchor={params['anchor']}&count={params['count']}"

    return url


def extract_path_from_url(url):
    """Extract the path component from Nike URL"""
    parsed = urlparse(url)
    return parsed.path.lstrip("/")


def process_single_url(url):
    """Process a single URL with its own session"""
    # session = requests.Session()
    session = curl_cffi_requests.Session()
    category = " ".join(url.split("/")[-1].split("-")[:-1])

    # Get concept IDs
    concept_ids = extract_concept_ids(session, url)
    if not concept_ids:
        return pd.DataFrame()

    # Extract path from URL
    path = extract_path_from_url(url)

    # Construct API URL
    api_url = construct_api_url(concept_ids, path)

    # Fetch products
    return fetch_nike_products(session, api_url, category)


"""Main function to run the crawler and update database"""
# Initialize database
init_db()

all_data = []
start_time = datetime.now()
logging.info(f"Starting crawler run at {start_time}")

# Create main progress bar for overall progress
with tqdm(total=len(URLS), desc="Processing categories", position=1) as main_pbar:
    with ThreadPoolExecutor(max_workers=16) as executor:
        future_to_url = {executor.submit(process_single_url, url): url for url in URLS}

        for future in as_completed(future_to_url):
            url = future_to_url[future]
            try:
                df = future.result()
                if not df.empty:
                    all_data.append(df)
            except Exception as e:
                logging.error(f"Error processing {url}: {str(e)}")
            finally:
                with progress_lock:
                    main_pbar.update(1)

# Combine all data and update database
if all_data:
    final_df = pd.concat(all_data, ignore_index=True)
    update_database(final_df)
    end_time = datetime.now()
    logging.info(
        f"Crawler run completed at {end_time}. Duration: {end_time - start_time}"
    )
else:
    logging.warning("No data collected in this run")
