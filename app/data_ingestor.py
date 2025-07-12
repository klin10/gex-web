import argparse
import yfinance as yf
import datetime
from datetime import timezone # Added import
from sqlalchemy.orm.exc import NoResultFound

# To use Flask app context and models
from app.app import app # Import the Flask app instance
from app.models import db, Ticker, Expiration, GEXStrikeData
from app.gex_calculator import GEXCalculator

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

def ingest_ticker_data(ticker_symbol):
    """
    Fetches option data for a ticker, calculates GEX, and stores it in the database.
    """
    with app.app_context(): # Essential for db operations
        print(f"Starting ingestion for ticker: {ticker_symbol.upper()}")

        yf_ticker = yf.Ticker(ticker_symbol)

        # 1. Get Ticker Info (or create Ticker record)
        try:
            company_name = yf_ticker.info.get('longName', ticker_symbol.upper())
            current_price_for_info = yf_ticker.info.get('currentPrice') # More direct if available
        except Exception as e:
            print(f"Warning: Could not fetch full info for {ticker_symbol}. Error: {str(e)}. Using symbol as name.")
            company_name = ticker_symbol.upper()
            current_price_for_info = None

        ticker_orm = db.session.query(Ticker).filter_by(symbol=ticker_symbol.upper()).first()
        if not ticker_orm:
            print(f"Creating new ticker record for {ticker_symbol.upper()}")
            ticker_orm = Ticker(symbol=ticker_symbol.upper(), company_name=company_name)
            db.session.add(ticker_orm)
        else:
            print(f"Updating existing ticker record for {ticker_symbol.upper()}")
            ticker_orm.company_name = company_name
        ticker_orm.last_updated_info = datetime.datetime.now(datetime.timezone.utc)
        db.session.commit() # Commit to get ticker_orm.id if new

        # 2. Get all future expiration dates from yfinance
        try:
            exp_date_strings = yf_ticker.options
            if not exp_date_strings:
                print(f"No options expiration dates found for {ticker_symbol} via yfinance.")
                return
        except Exception as e:
            print(f"Error fetching expiration dates for {ticker_symbol} from yfinance: {str(e)}")
            return

        print(f"Found {len(exp_date_strings)} expiration dates from yfinance for {ticker_symbol}.")

        # Instantiate GEXCalculator
        # The risk_free_rate is hardcoded in GEXCalculator for now.
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

            # Get or create Expiration record
            expiration_orm = db.session.query(Expiration).filter_by(ticker_id=ticker_orm.id, date=exp_date_obj).first()
            if not expiration_orm:
                expiration_orm = Expiration(ticker_id=ticker_orm.id, date=exp_date_obj)
                db.session.add(expiration_orm)

            # Fetch option chain for this expiration
            try:
                opt_chain = yf_ticker.option_chain(exp_date_str)
                calls_df = opt_chain.calls
                puts_df = opt_chain.puts
            except Exception as e:
                print(f"    Error fetching option chain for {exp_date_str}: {str(e)}. Skipping this expiration.")
                db.session.rollback() # Rollback adding expiration_orm if chain fetch fails
                continue

            # Fetch current spot price (can fluctuate, so fetch per expiration or once per run)
            # For simplicity, let's try to use the one from yf_ticker.info if recent,
            # otherwise fetch fresh.
            spot_price = None
            if current_price_for_info and ticker_orm.last_updated_info and \
               (datetime.datetime.now(datetime.timezone.utc) - ticker_orm.last_updated_info).total_seconds() < 300: # 5 mins
                 spot_price = current_price_for_info

            if spot_price is None: # Fetch fresh if info price is old or missing
                spot_price = get_spot_price(yf_ticker)

            if spot_price is None:
                print(f"    Cannot calculate GEX for {exp_date_str} due to missing spot price. Skipping.")
                db.session.rollback()
                continue

            # Calculate GEX using GEXCalculator
            try:
                gex_results = calculator.calculate_gex_for_expiration(
                    spot_price=spot_price,
                    calls_df=calls_df,
                    puts_df=puts_df,
                    expiration_date_str=exp_date_str
                )
            except Exception as e:
                print(f"    Error calculating GEX for {exp_date_str}: {str(e)}. Skipping this expiration.")
                db.session.rollback()
                continue

            # Delete old GEXStrikeData for this expiration
            db.session.query(GEXStrikeData).filter_by(expiration_id=expiration_orm.id).delete()

            # Add new GEXStrikeData
            for strike_data in gex_results['gex_by_strike']:
                gex_strike_orm = GEXStrikeData(
                    expiration_id=expiration_orm.id, # Will be set after commit if expiration_orm is new
                    strike=strike_data['strike'],
                    call_gex_usd=strike_data['call_gex_usd'],
                    put_gex_usd=strike_data['put_gex_usd'],
                    net_gex_usd=strike_data['net_gex_usd'],
                    spot_price_at_calculation=gex_results['spot_price_used'],
                    calculation_timestamp=datetime.datetime.now(datetime.timezone.utc)
                )
                db.session.add(gex_strike_orm)

            expiration_orm.last_fetched_gex = datetime.datetime.now(datetime.timezone.utc)
            db.session.commit() # Commit per expiration
            print(f"    Successfully processed and stored GEX data for {exp_date_str}.")

        print(f"Ingestion complete for ticker: {ticker_symbol.upper()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GEX Data Ingestor for specified stock tickers.")
    parser.add_argument("tickers", metavar="TICKER", type=str, nargs="+",
                        help="One or more stock ticker symbols to ingest (e.g., AAPL MSFT SPY).")

    args = parser.parse_args()

    # Need to import pandas for get_spot_price type hint and usage
    import pandas

    for ticker_sym in args.tickers:
        ingest_ticker_data(ticker_sym)

    print("All specified tickers processed.")
