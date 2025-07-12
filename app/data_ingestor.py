import argparse
import yfinance as yf
import pandas as pd
import datetime
# from datetime import timezone # No longer needed
import os

# To use Flask app context and models
# from app.app import app # <-- REMOVING THIS
# We will create our own DB session
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import db, Ticker, Expiration, GEXStrikeData # Still need model definitions
from app.gex_calculator import GEXCalculator

def get_database_uri():
    """Constructs the database URI, mirroring app.py logic."""
    basedir = os.path.abspath(os.path.dirname(__file__))
    return 'sqlite:///' + os.path.join(basedir, 'gex_data.db')

def get_spot_price(yf_ticker_obj):
    try:
        spot = yf_ticker_obj.history(period="1d")['Close'].iloc[-1]
        if pd.isna(spot):
            print(f"Warning: Spot price for {yf_ticker_obj.ticker} is NaN.")
            return None
        return spot
    except IndexError:
        print(f"Warning: Could not retrieve current price for {yf_ticker_obj.ticker}. No history data.")
        return None
    except Exception as e:
        print(f"Warning: Error fetching spot price for {yf_ticker_obj.ticker}: {str(e)}")
        return None

def ingest_ticker_data(ticker_symbol, db_session):
    """
    Fetches option data for a ticker, calculates GEX, and stores it in the database
    using a provided database session.
    """
    print(f"Starting ingestion for ticker: {ticker_symbol.upper()}")

    yf_ticker = yf.Ticker(ticker_symbol)

    # 1. Get Ticker Info
    try:
        company_name = yf_ticker.info.get('longName', ticker_symbol.upper())
        current_price_for_info = yf_ticker.info.get('currentPrice')
    except Exception as e:
        print(f"Warning: Could not fetch full info for {ticker_symbol}. Error: {str(e)}. Using symbol as name.")
        company_name = ticker_symbol.upper()
        current_price_for_info = None

    ticker_orm = db_session.query(Ticker).filter_by(symbol=ticker_symbol.upper()).first()
    if not ticker_orm:
        print(f"Creating new ticker record for {ticker_symbol.upper()}")
        ticker_orm = Ticker(symbol=ticker_symbol.upper(), company_name=company_name)
        db_session.add(ticker_orm)
    else:
        print(f"Updating existing ticker record for {ticker_symbol.upper()}")
        ticker_orm.company_name = company_name
    ticker_orm.last_updated_info = datetime.datetime.utcnow()
    db_session.commit()

    # 2. Get expiration dates
    try:
        exp_date_strings = yf_ticker.options
        if not exp_date_strings:
            print(f"No options expiration dates found for {ticker_symbol} via yfinance.")
            return
    except Exception as e:
        print(f"Error fetching expiration dates for {ticker_symbol} from yfinance: {str(e)}")
        return

    print(f"Found {len(exp_date_strings)} expiration dates from yfinance for {ticker_symbol}.")

    calculator = GEXCalculator()

    # 3. For each expiration date:
    for exp_date_str in exp_date_strings:
        print(f"  Processing expiration: {exp_date_str}")

        try:
            exp_date_obj = datetime.datetime.strptime(exp_date_str, '%Y-%m-%d').date()
        except ValueError:
            print(f"    Invalid date format for expiration '{exp_date_str}'. Skipping.")
            continue

        if exp_date_obj < datetime.date.today():
            print(f"    Expiration date {exp_date_str} is in the past. Skipping.")
            continue

        expiration_orm = db_session.query(Expiration).filter_by(ticker_id=ticker_orm.id, date=exp_date_obj).first()
        if not expiration_orm:
            expiration_orm = Expiration(ticker_id=ticker_orm.id, date=exp_date_obj)
            db_session.add(expiration_orm)

        try:
            opt_chain = yf_ticker.option_chain(exp_date_str)
            calls_df = opt_chain.calls
            puts_df = opt_chain.puts
        except Exception as e:
            print(f"    Error fetching option chain for {exp_date_str}: {str(e)}. Skipping.")
            db_session.rollback()
            continue

        spot_price = None
        if current_price_for_info and ticker_orm.last_updated_info and \
           (datetime.datetime.utcnow() - ticker_orm.last_updated_info).total_seconds() < 300:
             spot_price = current_price_for_info

        if spot_price is None:
            spot_price = get_spot_price(yf_ticker)

        if spot_price is None:
            print(f"    Cannot calculate GEX for {exp_date_str} due to missing spot price. Skipping.")
            db_session.rollback()
            continue

        try:
            gex_results = calculator.calculate_gex_for_expiration(
                spot_price=spot_price,
                calls_df=calls_df,
                puts_df=puts_df,
                expiration_date_str=exp_date_str
            )
        except Exception as e:
            print(f"    Error calculating GEX for {exp_date_str}: {str(e)}. Skipping.")
            db_session.rollback()
            continue

        # --- Calculate Zero Gamma Level ---
        print(f"    Calculating zero gamma level...")
        zero_gamma_level = calculator.find_zero_gamma_level(
            current_spot_price=spot_price,
            calls_df=calls_df,
            puts_df=puts_df,
            expiration_date_str=exp_date_str
        )
        if zero_gamma_level is not None:
            print(f"    Found zero gamma level: {zero_gamma_level}")

        # --- Store results in DB ---
        expiration_orm.zero_gamma_level = zero_gamma_level # Store the found level
        db_session.query(GEXStrikeData).filter_by(expiration_id=expiration_orm.id).delete()

        # Need to commit here to get expiration_orm.id if it's new
        db_session.commit()

        for strike_data in gex_results['gex_by_strike']:
            gex_strike_orm = GEXStrikeData(
                expiration_id=expiration_orm.id,
                strike=strike_data['strike'],
                call_gex_usd=strike_data['call_gex_usd'],
                put_gex_usd=strike_data['put_gex_usd'],
                net_gex_usd=strike_data['net_gex_usd'],
                spot_price_at_calculation=gex_results['spot_price_used'],
                calculation_timestamp=datetime.datetime.utcnow()
            )
            db_session.add(gex_strike_orm)

        expiration_orm.last_fetched_gex = datetime.datetime.utcnow()
        db_session.commit()
        print(f"    Successfully processed and stored GEX data for {exp_date_str}.")

    print(f"Ingestion complete for ticker: {ticker_symbol.upper()}")


if __name__ == "__main__":
    # --- Standalone Database Setup ---
    engine = create_engine(get_database_uri())
    # The models are already defined in app/models.py and are based on db.Model from flask_sqlalchemy
    # which uses a declarative base. We can use these models with a standalone session.
    # The key is that the models are defined before being used by the session.
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
            db_session.rollback() # Rollback any partial changes for this ticker

    db_session.close()
    print("All specified tickers processed.")
