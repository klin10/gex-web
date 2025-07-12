import yfinance as yf
import pandas as pd
import datetime
from sqlalchemy.orm import Session
from .models import Ticker, Expiration, GEXStrikeData
from .gex_calculator import GEXCalculator

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

def ingest_ticker_data(ticker_symbol: str, db_session: Session):
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

        db_session.query(GEXStrikeData).filter_by(expiration_id=expiration_orm.id).delete()

        db_session.commit()

        oi_map = {}
        for _, row in calls_df.iterrows():
            strike = float(row['strike'])
            oi = row.get('openInterest', 0)
            if strike not in oi_map:
                oi_map[strike] = {'call_oi': 0, 'put_oi': 0}
            if not pd.isna(oi):
                oi_map[strike]['call_oi'] += int(oi)

        for _, row in puts_df.iterrows():
            strike = float(row['strike'])
            oi = row.get('openInterest', 0)
            if strike not in oi_map:
                oi_map[strike] = {'call_oi': 0, 'put_oi': 0}
            if not pd.isna(oi):
                oi_map[strike]['put_oi'] += int(oi)

        for gex_data in gex_results['gex_by_strike']:
            strike = gex_data['strike']
            oi_data = oi_map.get(strike, {'call_oi': 0, 'put_oi': 0})
            gex_strike_orm = GEXStrikeData(
                expiration_id=expiration_orm.id,
                strike=strike,
                call_gex_usd=gex_data['call_gex_usd'],
                put_gex_usd=gex_data['put_gex_usd'],
                net_gex_usd=gex_data['net_gex_usd'],
                call_oi=oi_data['call_oi'],
                put_oi=oi_data['put_oi'],
                spot_price_at_calculation=gex_results['spot_price_used'],
                calculation_timestamp=datetime.datetime.utcnow()
            )
            db_session.add(gex_strike_orm)

        expiration_orm.last_fetched_gex = datetime.datetime.utcnow()
        db_session.commit()
        print(f"    Successfully processed and stored GEX data for {exp_date_str}.")

    print(f"Ingestion complete for ticker: {ticker_symbol.upper()}")
