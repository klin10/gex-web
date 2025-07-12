import argparse
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.tasks import ingest_ticker_data # Import the reusable task

def get_database_uri():
    """Constructs the database URI, mirroring app.py logic."""
    basedir = os.path.abspath(os.path.dirname(__file__))
    return 'sqlite:///' + os.path.join(basedir, 'gex_data.db')

if __name__ == "__main__":
    # --- Standalone Database Setup ---
    engine = create_engine(get_database_uri())
    Session = sessionmaker(bind=engine)
    db_session = Session()
    # --- End Standalone Setup ---

    parser = argparse.ArgumentParser(description="GEX Data Ingestor for specified stock tickers.")
    parser.add_argument("tickers", metavar="TICKER", type=str, nargs="+",
                        help="One or more stock ticker symbols to ingest (e.g., AAPL MSFT SPY).")

    args = parser.parse_args()

    for ticker_sym in args.tickers:
        try:
            ingest_ticker_data(ticker_sym, db_session)
        except Exception as e:
            print(f"!!! An unexpected error occurred while processing {ticker_sym}: {e}")
            db_session.rollback()

    db_session.close()
    print("All specified tickers processed.")
