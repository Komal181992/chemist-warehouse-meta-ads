import os
import json
import ast
from urllib.parse import urlparse, parse_qs

import pandas as pd
import gspread
from apify_client import ApifyClient
from google.oauth2.service_account import Credentials


APIFY_TOKEN = os.environ["APIFY_TOKEN"]
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]

ACTOR_ID = "XtaWFhbtfxyzqrFmd"
GOOGLE_SHEET_ID = "18e3Aa1ZrKwrD27aAas31NpLZm2tsa0w8V84kHUi6eVc"



client = ApifyClient(APIFY_TOKEN)

runs_list = client.actor(ACTOR_ID).runs().list(limit=1, desc=True)
runs = runs_list.items

if not runs:
    raise Exception(f"No runs found for actor ID: {ACTOR_ID}")

last_run = runs[0]
dataset_id = last_run.default_dataset_id

items = list(client.dataset(dataset_id).iterate_items())

if not items:
    raise Exception("No data found in latest Apify dataset.")

df = pd.json_normalize(items)

keep_cols = [
    "ad_archive_id", "collation_id", "is_active",
    "start_date_formatted", "end_date_formatted", "total_active_time",
    "page_name", "snapshot.page_name",
    "snapshot.title", "snapshot.body.text", "snapshot.caption",
    "snapshot.link_description", "snapshot.cta_text", "snapshot.cta_type",
    "snapshot.display_format", "snapshot.link_url",
    "snapshot.cards", "snapshot.images", "snapshot.videos",
    "publisher_platform",
    "impressions_with_index.impressions_text",
    "impressions_with_index.impressions_index",
    "snapshot.page_like_count"
]

available_cols = [col for col in keep_cols if col in df.columns]
curated_df = df[available_cols].copy()

curated_df.columns = curated_df.columns.str.strip()
curated_df = curated_df.dropna(axis=1, how="all")
curated_df = curated_df.replace("", pd.NA)

if "total_active_time" in curated_df.columns:
    curated_df["active_hours"] = (curated_df["total_active_time"] / 3600).round(2)


def fix_missing_question(url):
    if pd.isna(url):
        return url
    if "utm_" in str(url) and "?" not in str(url):
        return str(url).replace("utm_", "?utm_", 1)
    return url


curated_df["fixed_url"] = curated_df["snapshot.link_url"].apply(fix_missing_question)


def parse_utm(url):
    if pd.isna(url):
        return pd.Series({
            "domain": None,
            "url_path": None,
            "utm_source": None,
            "utm_medium": None,
            "utm_campaign": None,
            "utm_term": None,
            "utm_content": None
        })

    parsed_url = urlparse(str(url))
    query = parse_qs(parsed_url.query)

    return pd.Series({
        "domain": parsed_url.netloc,
        "url_path": parsed_url.path,
        "utm_source": query.get("utm_source", [None])[0],
        "utm_medium": query.get("utm_medium", [None])[0],
        "utm_campaign": query.get("utm_campaign", [None])[0],
        "utm_term": query.get("utm_term", [None])[0],
        "utm_content": query.get("utm_content", [None])[0]
    })


utm_df = curated_df["fixed_url"].apply(parse_utm)
curated_df = pd.concat([curated_df, utm_df], axis=1)


def parse_campaign(campaign):
    if pd.isna(campaign):
        return pd.Series({
            "country": None,
            "retailer": None,
            "funding_type": None,
            "platform": None,
            "channel": None,
            "supplier_entity": None,
            "brand": None,
            "category_code": None,
            "campaign_period": None,
            "asset_code": None,
            "creative_format": None
        })

    parts = str(campaign).split("_")

    return pd.Series({
        "country": parts[0] if len(parts) > 0 else None,
        "retailer": parts[1] if len(parts) > 1 else None,
        "funding_type": parts[2] if len(parts) > 2 else None,
        "platform": parts[3] if len(parts) > 3 else None,
        "channel": parts[4] if len(parts) > 4 else None,
        "supplier_entity": parts[5] if len(parts) > 5 else None,
        "brand": parts[6] if len(parts) > 6 else None,
        "category_code": parts[7] if len(parts) > 7 else None,
        "campaign_period": parts[8] if len(parts) > 8 else None,
        "asset_code": parts[9] if len(parts) > 9 else None,
        "creative_format": parts[-1] if len(parts) > 0 else None
    })


campaign_df = curated_df["utm_campaign"].apply(parse_campaign)
curated_df = pd.concat([curated_df, campaign_df], axis=1)


def extract_first_card_value(cards, key):
    if isinstance(cards, list) and len(cards) > 0 and isinstance(cards[0], dict):
        return cards[0].get(key)
    return None


if "snapshot.cards" in curated_df.columns:
    curated_df["card_title"] = curated_df["snapshot.cards"].apply(lambda x: extract_first_card_value(x, "title"))
    curated_df["card_body"] = curated_df["snapshot.cards"].apply(lambda x: extract_first_card_value(x, "body"))
    curated_df["card_cta_text"] = curated_df["snapshot.cards"].apply(lambda x: extract_first_card_value(x, "cta_text"))
    curated_df["card_link_url"] = curated_df["snapshot.cards"].apply(lambda x: extract_first_card_value(x, "link_url"))
    curated_df["card_image_url"] = curated_df["snapshot.cards"].apply(lambda x: extract_first_card_value(x, "original_image_url"))
    curated_df["card_video_hd_url"] = curated_df["snapshot.cards"].apply(lambda x: extract_first_card_value(x, "video_hd_url"))
    curated_df["card_video_sd_url"] = curated_df["snapshot.cards"].apply(lambda x: extract_first_card_value(x, "video_sd_url"))


text_cols = [
    "funding_type", "supplier_entity", "brand",
    "creative_format", "utm_source", "utm_medium"
]

for col in text_cols:
    if col in curated_df.columns:
        curated_df[col] = curated_df[col].astype(str).str.strip().str.lower()

curated_df["funding_type_label"] = (
    curated_df["funding_type"]
    .fillna("Not Specified")
    .astype(str)
    .str.strip()
    .str.lower()
    .replace({
        "coop": "Coop",
        "house": "House",
        "o&o": "O&O",
        "oa": "O&O",
        "none": "Not Specified",
        "nan": "Not Specified",
        "<na>": "Not Specified",
        "": "Not Specified"
    })
)

curated_df["brand_label"] = curated_df["brand"].str.replace("-", " ", regex=False).str.title()
curated_df["supplier_label"] = curated_df["supplier_entity"].str.replace("-", " ", regex=False).str.title()

curated_df["ad_archive_id"] = curated_df["ad_archive_id"].astype(str)
curated_df["collation_id"] = curated_df["collation_id"].astype(str)

deduped_df = curated_df.drop_duplicates(subset=["ad_archive_id"])

final_cols = [
    "ad_archive_id", "collation_id", "is_active",
    "start_date_formatted", "end_date_formatted",
    "page_name", "snapshot.page_name",
    "snapshot.title", "snapshot.body.text",
    "snapshot.cta_text", "snapshot.display_format",
    "snapshot.link_url", "domain", "url_path",
    "utm_source", "utm_medium", "utm_campaign",
    "funding_type_label", "supplier_label", "brand_label",
    "campaign_period", "creative_format",
    "card_title", "card_body", "card_cta_text",
    "card_image_url", "card_video_hd_url",
    "publisher_platform"
]

final_df = deduped_df[[col for col in final_cols if col in deduped_df.columns]].copy()

final_df["snapshot_week"] = pd.Timestamp.now(
    tz="Australia/Melbourne"
).strftime("%Y-W%U")

final_df["snapshot_month"] = pd.Timestamp.now(
    tz="Australia/Melbourne"
).strftime("%Y-%m")


def clean_platforms(x):

    # Handle None
    if x is None:
        return None

    # Handle lists directly
    if isinstance(x, list):
        return ", ".join(map(str, x))

    # Handle strings
    if isinstance(x, str):

        try:
            parsed = ast.literal_eval(x)

            if isinstance(parsed, list):
                return ", ".join(map(str, parsed))

            return x

        except Exception:
            return x

    # Handle NaN safely
    try:
        if pd.isna(x):
            return None
    except Exception:
        pass

    return str(x)


final_df["publisher_platform_clean"] = final_df["publisher_platform"].apply(clean_platforms)

platforms = ["FACEBOOK", "INSTAGRAM", "AUDIENCE_NETWORK", "MESSENGER", "THREADS", "WHATSAPP"]

for platform in platforms:
    final_df[platform.lower()] = final_df["publisher_platform_clean"].str.contains(
        platform,
        case=False,
        na=False
    )


def classify_page(path):
    if pd.isna(path):
        return "Unknown"

    path = str(path).lower()

    if path == "/":
        return "Homepage"
    elif "/product/" in path or "/buy/" in path:
        return "SKU Product"
    elif "/shop-online/" in path:
        return "Brand/Category Page"
    elif "/search" in path:
        return "Search Page"
    elif "/store-locator" in path:
        return "Store Locator"
    elif "/plans" in path:
        return "Plans/Pricing Page"
    elif "/canvas_doc/" in path:
        return "Internal/App Page"
    elif "vaccination" in path or "event" in path:
        return "Health Service / Event Page"
    elif "sale" in path or "promo" in path or "mayhem" in path:
        return "Promo Landing Page"

    return "Other"


final_df["landing_page_type"] = final_df["url_path"].apply(classify_page)

final_df["is_official_cw"] = (
    final_df["page_name"]
    .astype(str)
    .str.lower()
    .str.contains("chemist warehouse", na=False)
)

final_df["mentions_cw_brand"] = (
    final_df["snapshot.title"].astype(str).str.lower().str.contains("chemist warehouse", na=False)
    |
    final_df["snapshot.body.text"].astype(str).str.lower().str.contains("chemist warehouse", na=False)
)

tga_keywords = [
    "cure", "fat loss", "weight loss",
    "prevent disease", "miracle", "rapid results"
]

pattern = "|".join(tga_keywords)

final_df["tga_risk_flag"] = (
    final_df["snapshot.title"].astype(str).str.lower().str.contains(pattern, na=False)
    |
    final_df["snapshot.body.text"].astype(str).str.lower().str.contains(pattern, na=False)
)

final_df["snapshot_date"] = pd.Timestamp.now(tz="Australia/Melbourne").date()

final_df["start_date_formatted_dt"] = pd.to_datetime(
    final_df["start_date_formatted"],
    errors="coerce",
    dayfirst=False
)

pipeline_run_datetime = pd.Timestamp.now(tz="Australia/Melbourne").tz_localize(None)

final_df["snapshot_run_datetime"] = pipeline_run_datetime

final_df["campaign_age_days"] = (
    final_df["snapshot_run_datetime"] - final_df["start_date_formatted_dt"]
).dt.days


def run_dq_checks(data):
    checks = {
        "duplicate_ad_archive_id": data["ad_archive_id"].duplicated(keep=False).sum(),
        "missing_page_name": data["page_name"].isna().sum(),
        "invalid_start_date": data["start_date_formatted_dt"].isna().sum(),
        "future_start_date": (data["start_date_formatted_dt"] > pd.Timestamp.now()).sum(),
        "blank_creative_text": (
            data["snapshot.title"].isna()
            & data["snapshot.body.text"].isna()
        ).sum()
    }

    dq = pd.DataFrame({
        "dq_check": list(checks.keys()),
        "failed_records": list(checks.values()),
        "total_records": len(data)
    })

    dq["failure_rate_pct"] = (
        dq["failed_records"] / dq["total_records"] * 100
    ).round(2)

    dq["pipeline_run_datetime"] = pd.Timestamp.now(tz="Australia/Melbourne").tz_localize(None)

    return dq


dq_summary = run_dq_checks(final_df)

critical_failures = dq_summary[
    dq_summary["dq_check"].isin([
        "duplicate_ad_archive_id",
        "invalid_start_date",
        "future_start_date"
    ])
]["failed_records"].sum()

if critical_failures > 0:
    print(dq_summary)
    raise Exception("Pipeline failed due to critical data quality issues.")

os.makedirs("output", exist_ok=True)

final_df.to_csv("output/meta_ads_final_updated.csv", index=False)
dq_summary.to_csv("output/meta_ads_dq_summary.csv", index=False)

print("CSV files exported successfully.")


def clean_for_sheets(data):
    cleaned = data.copy()

    for col in cleaned.columns:
        cleaned[col] = cleaned[col].apply(
            lambda x: json.dumps(x, default=str) if isinstance(x, (list, dict)) else x
        )

    cleaned = cleaned.replace([float("inf"), float("-inf")], pd.NA)

    # Replace real nulls
    cleaned = cleaned.where(pd.notnull(cleaned), "Not Specified")

    cleaned = cleaned.astype(str)

    # Replace blank-like text values
    cleaned = cleaned.replace({
        "": "Not Specified",
        "NaT": "Not Specified",
        "nan": "Not Specified",
        "NaN": "Not Specified",
        "<NA>": "Not Specified",
        "None": "Not Specified"
    })

    return cleaned

def upload_to_google_sheets(final_data, dq_data):
    service_account_info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]

    credentials = Credentials.from_service_account_info(
        service_account_info,
        scopes=scopes
    )

    gc = gspread.authorize(credentials)

    print("UPDATING SHEET ID:", GOOGLE_SHEET_ID)

    # Open exact Google Sheet using ID
    sheet = gc.open_by_key(GOOGLE_SHEET_ID)

    final_ws = sheet.worksheet("Final_Data")
    dq_ws = sheet.worksheet("DQ_Summary")

    final_clean = clean_for_sheets(final_data)
    dq_clean = clean_for_sheets(dq_data)

    # Get existing data
    existing_data = final_ws.get_all_values()

    # Add headers only once
   if len(existing_data) == 0:
    final_ws.append_row(final_clean.columns.tolist())

    # Append weekly snapshot rows
    final_ws.append_rows(final_clean.values.tolist())

    # Overwrite DQ summary
    dq_ws.clear()

    dq_ws.update(
        [dq_clean.columns.tolist()] + dq_clean.values.tolist()
    )

    print("Google Sheets updated successfully.")



upload_to_google_sheets(final_df, dq_summary)
print("Pipeline completed successfully.")

print("Pipeline completed successfully.")
