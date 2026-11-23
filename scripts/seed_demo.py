"""Manually (re)seed demo data:  python -m scripts.seed_demo   (needs DATABASE_URL or uses local SQLite)"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import db
from app.seed import seed
db.init(); seed(); print("seeded demo_user_1 and demo_user_2")
