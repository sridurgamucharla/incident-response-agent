"""Phase 0 sanity check: DB connectivity + which API keys are filled in."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, text

from agent.config import get_settings

s = get_settings()
ok = True

for name, url in [("shoplite_db", s.shoplite_database_url), ("dejavu_db", s.dejavu_database_url)]:
    try:
        with create_engine(url).connect() as conn:
            db = conn.execute(text("select current_database()")).scalar()
        print(f"[OK]   {name}: connected (current_database={db})")
    except Exception as e:
        ok = False
        print(f"[FAIL] {name}: {type(e).__name__}: {str(e).splitlines()[0]}")

for key in ["hindsight_api_key", "hindsight_base_url", "gemini_api_key", "gemini_model", "gemini_fallback_models"]:
    print(f"[{'OK' if getattr(s, key) else 'TODO'}] {key.upper()} {'set' if getattr(s, key) else 'is empty'}")

sys.exit(0 if ok else 1)
