# scripts/init_supabase.py
import os
import sys
import logging
from dotenv import load_dotenv

# Add project root to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from src.repositories.stock_list_repo import StockListRepository
from src.repositories.cron_job_repo import CronJobRepository

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    load_dotenv()
    
    dsn = os.getenv("SUPABASE_DATABASE_URL")
    if not dsn:
        print("Error: SUPABASE_DATABASE_URL is not set in .env")
        print("Please add it to your .env file:")
        print("SUPABASE_DATABASE_URL=postgresql://postgres:password@db.xxx.supabase.co:5432/postgres")
        sys.exit(1)
        
    print(f"Connecting to Supabase... (DSN: ...{dsn[-10:]})")
    
    print("\n1. Initializing stock_watchlist table...")
    if StockListRepository.ensure_table():
        print("✅ stock_watchlist table initialized (with user_id support).")
    else:
        print("❌ Failed to initialize stock_watchlist table.")
        
    print("\n2. Initializing cron_jobs table...")
    if CronJobRepository.ensure_table():
        print("✅ cron_jobs table initialized.")
    else:
        print("❌ Failed to initialize cron_jobs table.")

if __name__ == "__main__":
    main()
