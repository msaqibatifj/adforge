import os
import sys
import tempfile

os.environ["ADFORGE_FAKE_LLM"] = "1"
os.environ.setdefault("ADMIN_TOKEN", "test-admin")
if not os.environ.get("DATABASE_URL"):
    os.environ["SQLITE_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
