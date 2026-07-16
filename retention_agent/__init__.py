"""Load .env into the environment on package import, so ANTHROPIC_API_KEY (and
RETENTION_MODEL) can live in a .env file rather than only a shell export. Runs
for any `from retention_agent... import ...`, before LLM() reads the key.
Optional — if python-dotenv isn't installed we just use the real environment.
"""
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass
