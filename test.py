import os
import requests
import pyodbc

from decimal import Decimal
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

# Test exactly one vendor and one SKU.
TEST_VENDOR_ID = os.getenv("TEST_VENDOR_ID")
TEST_SKU = os.getenv("TEST_SKU")


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
        "TOKEN_URL": TOKEN_URL,
        "BASE_URL": BASE_URL,
        "CHAIN_ID": CHAIN_ID,
        "TEST_VENDOR_ID": TEST_VENDOR_ID,
        "TEST_SKU": TEST_SKU,
    }

    missing = [
        name for name, value in required_values.items()
        if not value
    ]

    if missing:
        raise ValueError(
            "Missing configuration in .env file: "
            + ", ".join(missing)
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

    print("Access token generated successfully!")

    return token


# ==========================================================
# GET EXACTLY ONE PRODUCT FROM SQL SERVER
# ==========================================================

def get_product(vendor_id, sku):
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
            TalabatStoreID = ?
            AND TalabatItemCode = ?
    """

    connection = get_db_connection()

    try:
        cursor = connection.cursor()

        cursor.execute(
            query,
            int(vendor_id),
            sku
        )

        rows = cursor.fetchall()

        if len(rows) == 0:
            print("Product not found for this vendor and SKU.")
            return None

        if len(rows) > 1:
            raise ValueError(
                "Multiple SQL rows match this vendor and SKU. "
                "Test stopped to prevent an ambiguous update."
            )

        print("Exactly one matching product found.")

        return rows[0]

    finally:
        connection.close()


# ==========================================================
# QUANTITY CALCULATION
#
# If SnapShotQuantity <= ThresholdQuantity:
#     Send 0
# Otherwise:
#     Send SnapShotQuantity
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
# UPDATE ONE PRODUCT IN ONE TALABAT VENDOR
# ==========================================================

def update_one_product(session, token, vendor_id, product):
    url = (
        f"{BASE_URL.rstrip('/')}/chains/{CHAIN_ID}"
        f"/vendors/{vendor_id}/catalog"
    )

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    # Send exactly one product.
    product_data = {
        "products": [product]
    }

    print("\nSending one product to Talabat...")

    response = session.put(
        url,
        headers=headers,
        json=product_data,
        timeout=60
    )

    print("Status Code:", response.status_code)
    print("Response:", response.text)

    response.raise_for_status()

    return response


# ==========================================================
# ONE-TIME TEST
# ==========================================================

def test_one_product():
    vendor_id = TEST_VENDOR_ID
    sku = TEST_SKU.strip()

    print("\n" + "=" * 65)
    print("TALABAT ONE PRODUCT / ONE STORE TEST")
    print("=" * 65)

    print("Vendor ID:", vendor_id)
    print("Talabat SKU:", sku)

    # Retrieve the exact product.
    row = get_product(vendor_id, sku)

    if row is None:
        return

    # Calculate quantity.
    quantity = calculate_quantity(
        row.SnapShotQuantity,
        row.ThresholdQuantity
    )

    # Prepare API payload.
    product = {
        "sku": str(row.TalabatItemCode).strip(),
        "price": float(row.SnapShotPrice),
        "active": True,
        "maximum_sales_quantity": quantity
    }

    print("\nPRODUCT DETAILS")
    print("-" * 65)
    print("Item ID:", row.ItemID)
    print("Item Lookup Code:", row.ItemLookupCode)
    print("Vendor ID:", row.TalabatStoreID)
    print("Talabat SKU:", product["sku"])
    print("Snapshot Price:", row.SnapShotPrice)
    print("Snapshot Quantity:", row.SnapShotQuantity)
    print("Threshold Quantity:", row.ThresholdQuantity)
    print("Quantity to Talabat:", quantity)
    print("-" * 65)

    # Generate token and send one API request.
    with requests.Session() as session:
        token = generate_token(session)

        update_one_product(
            session,
            token,
            vendor_id,
            product
        )

    print("\n" + "=" * 65)
    print("TEST COMPLETED SUCCESSFULLY")
    print("=" * 65)


# ==========================================================
# MAIN - RUN ONCE ONLY
# ==========================================================

if __name__ == "__main__":
    try:
        validate_configuration()
        test_one_product()

    except Exception as error:
        print("\nTEST FAILED:")
        print(error)
