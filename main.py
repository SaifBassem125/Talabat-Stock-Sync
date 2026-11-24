import os
import time
import requests
import pyodbc
from decimal import Decimal
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()

# ==========================================================
# CONFIGURATION
# ==========================================================

CLIENT_ID = os.getenv("DH_CLIENT_ID")
CLIENT_SECRET = os.getenv("DH_CLIENT_SECRET")

DB_SERVER = os.getenv("DB_SERVER")
DB_NAME = os.getenv("DB_NAME")
DB_USERNAME = os.getenv("DB_USERNAME")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_DRIVER = os.getenv(
    "DB_DRIVER",
    "ODBC Driver 18 for SQL Server"
)

TOKEN_URL = os.getenv("TOKEN_URL")
BASE_URL = os.getenv("BASE_URL")

CHAIN_ID = os.getenv("CHAIN_ID")

# Run again 15 minutes after the previous cycle finishes.
INTERVAL_SECONDS = 15 * 60


# ==========================================================
# VALIDATE CONFIGURATION
# ==========================================================

def validate_configuration():
    required_values = {
        "DH_CLIENT_ID": CLIENT_ID,
        "DH_CLIENT_SECRET": CLIENT_SECRET,
        "DB_SERVER": DB_SERVER,
        "DB_NAME": DB_NAME,
        "DB_USERNAME": DB_USERNAME,
        "DB_PASSWORD": DB_PASSWORD,
    }

    missing = [name for name, value in required_values.items() if not value]

    if missing:
        raise ValueError(
            "Missing configuration in .env file: " + ", ".join(missing)
        )


# ==========================================================
# DATABASE CONNECTION
# ==========================================================

def get_db_connection():
    connection_string = (
        f"DRIVER={{{DB_DRIVER}}};"
        f"SERVER={DB_SERVER};"
        f"DATABASE={DB_NAME};"
        f"UID={DB_USERNAME};"
        f"PWD={DB_PASSWORD};"
        "Encrypt=yes;"
        "TrustServerCertificate=yes;"
        "Connection Timeout=30;"
    )

    return pyodbc.connect(connection_string)


# ==========================================================
# GENERATE ACCESS TOKEN
# ==========================================================

def generate_token(session):
    payload = {
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET
    }

    headers = {
        "Content-Type": "application/x-www-form-urlencoded"
    }

    response = session.post(
        TOKEN_URL,
        data=payload,
        headers=headers,
        timeout=30
    )

    response.raise_for_status()

    token = response.json()["access_token"]

    print("Access token generated successfully.")

    return token


# ==========================================================
# GET PRODUCTS FROM SQL SERVER
# ==========================================================

def get_products():
    query = """
        SELECT
            ItemID,
            ItemLookupCode,
            TalabatStoreID,
            TalabatItemCode,
            SnapShotQuantity,
            SnapShotPrice,
            ThresholdQuantity
        FROM dbo.ItemStoreSnapshotThreshold
        WHERE
            TalabatStoreID IS NOT NULL
            AND TalabatItemCode IS NOT NULL
            AND LTRIM(RTRIM(TalabatItemCode)) <> ''
    """

    connection = get_db_connection()

    try:
        cursor = connection.cursor()
        cursor.execute(query)
        rows = cursor.fetchall()

        print(f"Total products retrieved from SQL: {len(rows)}")

        return rows

    finally:
        connection.close()


# ==========================================================
# QUANTITY CALCULATION
# If SnapShotQuantity <= ThresholdQuantity, send 0.
# Otherwise, send SnapShotQuantity.
# ==========================================================

def calculate_quantity(snapshot_quantity, threshold_quantity):
    snapshot_quantity = (
        Decimal(str(snapshot_quantity))
        if snapshot_quantity is not None
        else Decimal("0")
    )

    threshold_quantity = Decimal(str(threshold_quantity))

    if snapshot_quantity <= threshold_quantity:
        return 0

    if snapshot_quantity == snapshot_quantity.to_integral_value():
        return int(snapshot_quantity)

    return float(snapshot_quantity)


# ==========================================================
# UPDATE ONE TALABAT VENDOR
# ==========================================================

def update_vendor(session, token, vendor_id, products):
    url = (
        f"{BASE_URL}/chains/{CHAIN_ID}"
        f"/vendors/{vendor_id}/catalog"
    )

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    product_data = {
        "products": products
    }

    response = session.put(
        url,
        headers=headers,
        json=product_data,
        timeout=60
    )

    print(f"\nVendor ID: {vendor_id}")
    print(f"Products sent: {len(products)}")
    print(f"Status Code: {response.status_code}")
    print(f"Response: {response.text}")

    response.raise_for_status()


# ==========================================================
# MAIN SYNC PROCESS
# ==========================================================

def sync_products():
    print("\n" + "=" * 65)
    print("TALABAT STOCK SYNC STARTED")
    print("=" * 65)

    rows = get_products()

    if not rows:
        print("No eligible products found in SQL.")
        return

    # Group products by Talabat vendor ID.
    vendors = defaultdict(list)

    for row in rows:
        vendor_id = str(row.TalabatStoreID)
        sku = str(row.TalabatItemCode).strip()

        price = float(row.SnapShotPrice)

        quantity = calculate_quantity(
            row.SnapShotQuantity,
            row.ThresholdQuantity
        )

        product = {
            "sku": sku,
            "price": price,
            "active": True,
            "maximum_sales_quantity": quantity
        }

        vendors[vendor_id].append(product)

    print(f"Total vendors found: {len(vendors)}")

    successful_vendors = 0
    failed_vendors = 0

    # Generate one token for the complete sync cycle.
    with requests.Session() as session:
        token = generate_token(session)

        for vendor_id, products in vendors.items():
            try:
                update_vendor(
                    session,
                    token,
                    vendor_id,
                    products
                )

                successful_vendors += 1
                print(f"Vendor {vendor_id} updated successfully.")

            except requests.exceptions.RequestException as error:
                failed_vendors += 1
                print(f"ERROR updating vendor {vendor_id}: {error}")

    print("\n" + "=" * 65)
    print("SYNC SUMMARY")
    print("=" * 65)
    print(f"Total products: {len(rows)}")
    print(f"Total vendors: {len(vendors)}")
    print(f"Successful vendors: {successful_vendors}")
    print(f"Failed vendors: {failed_vendors}")
    print("=" * 65)


# ==========================================================
# RUN AUTOMATICALLY EVERY 15 MINUTES
# ==========================================================

def main():
    validate_configuration()

    print("Talabat Automatic Stock Sync")
    print("Update interval: 15 minutes")
    print("The first sync will start immediately.")

    while True:
        start_time = time.monotonic()

        try:
            sync_products()

        except Exception as error:
            print("\nSYNC ERROR:")
            print(error)

        elapsed = time.monotonic() - start_time
        wait_time = max(0, INTERVAL_SECONDS - elapsed)

        print(
            f"\nNext sync in {wait_time / 60:.2f} minutes."
        )

        time.sleep(wait_time)


if __name__ == "__main__":
    main()
