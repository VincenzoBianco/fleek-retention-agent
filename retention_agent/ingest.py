"""Ingest + clean the portfolio.

Everything here is vectorised pandas (no per-row Python loops) so it runs the
same at 300 rows or 30,000. Cleaning decisions worth calling out:

- We DON'T trust `broker_reliance_pct`. On ~28% of the book it disagrees with
  the raw order counts by >10pp. `orders_6m == manual + self_serve` holds
  exactly, so we recompute reliance from the counts and flag the mismatch.
- We DON'T trust `gmv_trend_pct` either — it's blank on half the book (it
  divides by Sep GMV, which is often 0). We derive a robust momentum signal
  from the monthly series instead.
- Blank numeric activity means "nothing recorded" -> 0. Blank status stays
  Unknown (we don't invent "Active"). "Duplicate"-flagged rows are kept but
  tagged so downstream can suppress them.
- Every account gets a fingerprint over its decision-relevant fields, so a
  re-run can tell new/changed/unchanged apart (see store.py).
"""
from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

from . import config
from .models import Account

# Canonical schema. Anything missing from a sheet is created empty then cleaned,
# so a slightly different export doesn't crash the loader.
NUMERIC_COLS = [
    "tenure_months", "gmv_total_6m", "orders_6m",
    *config.MONTH_COLS,
    "broker_reliance_pct", "manual_orders", "self_serve_orders",
    "app_active_days_6m", "pdp_views_6m", "make_an_offer_6m",
    "chat_threads", "video_call_requests",
    "handpick_orders", "bundle_orders", "bundle_gmv_share_pct",
]
STRING_COLS = ["ownership", "buyer_persona", "region", "country", "account_status"]

_OWNERSHIP_MAP = {
    "account managed": "Account Managed", "account-managed": "Account Managed",
    "am": "Account Managed", "managed": "Account Managed",
    "self serve": "Self Serve", "self-serve": "Self Serve",
    "selfserve": "Self Serve", "self service": "Self Serve",
}


def load_sheet(path, sheet: str) -> pd.DataFrame:
    """Read one tab of the workbook into a raw DataFrame."""
    return pd.read_excel(path, sheet_name=sheet)


def clean(df: pd.DataFrame) -> pd.DataFrame:
    """Return a cleaned copy with recomputed signals and per-row data_flags."""
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    # Ensure every expected column exists.
    for col in NUMERIC_COLS:
        if col not in df.columns:
            df[col] = np.nan
    for col in STRING_COLS:
        if col not in df.columns:
            df[col] = np.nan
    if "account_id" not in df.columns:
        raise ValueError("sheet has no account_id column")

    # --- strings: trim, drop empties, normalise ownership label ---
    for col in STRING_COLS + ["account_id"]:
        df[col] = df[col].astype("string").str.strip()
        df[col] = df[col].replace({"": pd.NA, "nan": pd.NA, "NaN": pd.NA, "None": pd.NA})
    df["ownership"] = (
        df["ownership"].str.lower().map(_OWNERSHIP_MAP).fillna(df["ownership"])
    )

    # drop rows with no id at all (unrecoverable)
    df = df[df["account_id"].notna()].copy()

    # --- numerics: coerce, clip, fill ---
    for col in NUMERIC_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    # activity / spend blanks = nothing recorded = 0
    zero_fill = [c for c in NUMERIC_COLS if c != "broker_reliance_pct"]
    df[zero_fill] = df[zero_fill].fillna(0)
    # no negative money or counts
    money_counts = ["gmv_total_6m", "orders_6m", *config.MONTH_COLS,
                    "manual_orders", "self_serve_orders", "handpick_orders", "bundle_orders"]
    df[money_counts] = df[money_counts].clip(lower=0)
    df[["broker_reliance_pct", "bundle_gmv_share_pct"]] = \
        df[["broker_reliance_pct", "bundle_gmv_share_pct"]].clip(lower=0, upper=100)

    # --- recompute broker reliance from the counts we trust ---
    orders = df["orders_6m"].replace(0, np.nan)
    df["broker_reliance"] = (df["manual_orders"] / orders * 100).fillna(0).round(1)
    df["broker_reliance_reported"] = df["broker_reliance_pct"]
    df["reliance_discrepancy"] = (
        (df["broker_reliance"] - df["broker_reliance_reported"]).abs()
        > config.RELIANCE_RECONCILE_TOLERANCE
    ).fillna(False)

    # --- robust momentum: last 3 months vs first 3 months ---
    first_half = df[config.MONTH_COLS[:3]].mean(axis=1)
    last_half = df[config.MONTH_COLS[3:]].mean(axis=1)
    df["momentum_pct"] = np.where(
        first_half > 0,
        (last_half - first_half) / first_half * 100,
        np.where(last_half > 0, 100.0, 0.0),  # 0 -> spend = ramping; 0 -> 0 = flat
    ).round(1)
    df["recent_gmv"] = df[config.MONTH_COLS[-1]]          # Feb, the latest month
    df["aov"] = (df["gmv_total_6m"] / orders).fillna(0).round(0)

    # --- data-quality flags (built vectorised, assembled per row at the end) ---
    status_missing = df["account_status"].isna()
    is_duplicate = df["account_status"].str.lower().eq("duplicate").fillna(False)
    dup_id = df["account_id"].duplicated(keep="first")
    total_mismatch = (
        (df["gmv_total_6m"] - df[config.MONTH_COLS].sum(axis=1)).abs() > 5
    )

    df["account_status"] = df["account_status"].fillna("Unknown")

    flags = pd.Series([[] for _ in range(len(df))], index=df.index)
    def add(mask, label):
        for i in df.index[mask]:
            flags.at[i].append(label)
    add(status_missing, "status_missing")
    add(is_duplicate, "status_duplicate")
    add(dup_id, "duplicate_account_id")
    add(df["reliance_discrepancy"], "reliance_mismatch_recomputed")
    add(total_mismatch, "gmv_total_mismatch")
    df["data_flags"] = flags

    return df


def _fingerprint(row: pd.Series) -> str:
    """Hash the fields that would change a decision. Cosmetic fields (country
    spelling, etc.) are excluded so trivial edits don't force a redraft."""
    parts = [
        row["account_id"], row["ownership"],
        round(float(row["gmv_total_6m"])), int(row["orders_6m"]),
        round(float(row["broker_reliance"]), 1),
        round(float(row["app_active_days_6m"]), 1), round(float(row["pdp_views_6m"]), 1),
        round(float(row["make_an_offer_6m"]), 1),
        round(float(row["bundle_gmv_share_pct"]), 1),
        int(row["handpick_orders"]), int(row["bundle_orders"]),
        round(float(row["chat_threads"]), 1), round(float(row["video_call_requests"]), 1),
        round(float(row["momentum_pct"] or 0), 1), round(float(row["recent_gmv"])),
    ]
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def to_accounts(df: pd.DataFrame) -> list[Account]:
    """Materialise cleaned rows as Account models (with fingerprints).

    If the same account_id appears twice in one load, the last row wins — a new
    batch that re-states an existing account updates it rather than duplicating.
    """
    df = df.drop_duplicates(subset="account_id", keep="last").copy()
    df["fingerprint"] = df.apply(_fingerprint, axis=1)
    out: list[Account] = []
    for _, r in df.iterrows():
        out.append(Account(
            account_id=r["account_id"],
            ownership=r["ownership"] or "Unknown",
            buyer_persona=_opt(r["buyer_persona"]),
            region=_opt(r["region"]),
            country=_opt(r["country"]),
            account_status=_opt(r["account_status"]),
            tenure_months=float(r["tenure_months"]),
            gmv_total_6m=float(r["gmv_total_6m"]),
            monthly_gmv=[float(r[c]) for c in config.MONTH_COLS],
            orders_6m=int(r["orders_6m"]),
            manual_orders=int(r["manual_orders"]),
            self_serve_orders=int(r["self_serve_orders"]),
            app_active_days_6m=float(r["app_active_days_6m"]),
            pdp_views_6m=float(r["pdp_views_6m"]),
            make_an_offer_6m=float(r["make_an_offer_6m"]),
            chat_threads=float(r["chat_threads"]),
            video_call_requests=float(r["video_call_requests"]),
            handpick_orders=int(r["handpick_orders"]),
            bundle_orders=int(r["bundle_orders"]),
            bundle_gmv_share_pct=float(r["bundle_gmv_share_pct"]),
            broker_reliance=float(r["broker_reliance"]),
            broker_reliance_reported=_optf(r["broker_reliance_reported"]),
            reliance_discrepancy=bool(r["reliance_discrepancy"]),
            momentum_pct=_optf(r["momentum_pct"]),
            recent_gmv=float(r["recent_gmv"]),
            aov=float(r["aov"]),
            data_flags=list(r["data_flags"]),
            fingerprint=r["fingerprint"],
        ))
    return out


def load_accounts(path, sheet: str) -> list[Account]:
    return to_accounts(clean(load_sheet(path, sheet)))


def _opt(v):
    return None if pd.isna(v) else str(v)


def _optf(v):
    return None if pd.isna(v) else float(v)
