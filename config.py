"""Application configuration."""

import os

# ── Reasoning model (Google Gemini) ─────────────────────────────────────────
# Set the GEMINI_API_KEY environment variable before starting the app.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-3.5-flash"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


def get_llm_config() -> dict:
    """Active model config; environment variables override the values above."""
    return {
        "api_key": GEMINI_API_KEY.strip(),
        "model": os.getenv("GEMINI_MODEL", GEMINI_MODEL).strip(),
        "base_url": GEMINI_BASE_URL,
    }


# ── Approval rules ──────────────────────────────────────────────────────────
HIGH_VALUE_THRESHOLD = 10_000.0      # invoices at or above this get extra review
MAX_INGESTION_RETRIES = 1            # self-correction passes during capture
PRICE_TOLERANCE = 0.20               # >20% off catalogue price flags a warning

# ── Paths ───────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "inventory.db")
INVOICES_DIR = os.path.join(BASE_DIR, "data", "invoices")
AUDIT_DIR = os.path.join(BASE_DIR, "audit_logs")
