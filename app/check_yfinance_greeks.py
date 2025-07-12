import yfinance as yf
import pandas as pd
import datetime

def get_next_expiration_date_for_check(ticker_obj):
    expirations = ticker_obj.options
    if not expirations:
        return None

    today = datetime.date.today()
    valid_expirations = []
    for exp_str in expirations:
        try:
            exp_date = datetime.datetime.strptime(exp_str, '%Y-%m-%d').date()
            if exp_date >= today: # Ensure it's a future or today's date
                # For testing, let's try to pick one that's not too close to avoid liquidity issues
                # and not too far to ensure data exists.
                # Simple heuristic: pick the first one that's at least a few days out if possible.
                if (exp_date - today).days > 5:
                     valid_expirations.append(exp_date)
        except ValueError:
            continue

    if not valid_expirations: # Fallback if no date is > 5 days out
        for exp_str in expirations:
            try:
                exp_date = datetime.datetime.strptime(exp_str, '%Y-%m-%d').date()
                if exp_date >= today:
                    valid_expirations.append(exp_date)
            except ValueError:
                continue

    if not valid_expirations:
        return None

    return min(valid_expirations).strftime('%Y-%m-%d')


def check_greeks(ticker_symbol="AAPL"):
    try:
        print(f"Fetching data for {ticker_symbol}...")
        stock = yf.Ticker(ticker_symbol)

        exp_date = get_next_expiration_date_for_check(stock)
        if not exp_date:
            print(f"No suitable options expiration dates found for {ticker_symbol}.")
            return

        print(f"Selected expiration date: {exp_date}")

        # Attempt to get current price
        try:
            spot_price = stock.history(period="1d")['Close'].iloc[-1]
            print(f"Current spot price: {spot_price}")
        except Exception as e:
            print(f"Could not fetch current spot price: {e}")
            spot_price = None


        opt_chain = stock.option_chain(exp_date)

        print("\n--- Call Options ---")
        if opt_chain.calls.empty:
            print("No call options data found.")
        else:
            print(f"Call options DataFrame shape: {opt_chain.calls.shape}")
            print(f"Call options columns: {opt_chain.calls.columns.tolist()}")
            # Display a few rows with relevant columns if they exist
            relevant_cols = ['strike', 'openInterest', 'impliedVolatility', 'delta', 'gamma', 'lastPrice']
            existing_cols = [col for col in relevant_cols if col in opt_chain.calls.columns]
            if existing_cols:
                print(opt_chain.calls[existing_cols].head())
                # Check for NaN/None in gamma
                if 'gamma' in opt_chain.calls.columns:
                    print(f"NaNs in call gamma: {opt_chain.calls['gamma'].isna().sum()} / {len(opt_chain.calls)}")
                if 'delta' in opt_chain.calls.columns:
                    print(f"NaNs in call delta: {opt_chain.calls['delta'].isna().sum()} / {len(opt_chain.calls)}")

        print("\n--- Put Options ---")
        if opt_chain.puts.empty:
            print("No put options data found.")
        else:
            print(f"Put options DataFrame shape: {opt_chain.puts.shape}")
            print(f"Put options columns: {opt_chain.puts.columns.tolist()}")
            if existing_cols: # existing_cols defined from calls check
                existing_put_cols = [col for col in relevant_cols if col in opt_chain.puts.columns]
                if existing_put_cols:
                    print(opt_chain.puts[existing_put_cols].head())
                     # Check for NaN/None in gamma
                    if 'gamma' in opt_chain.puts.columns:
                        print(f"NaNs in put gamma: {opt_chain.puts['gamma'].isna().sum()} / {len(opt_chain.puts)}")
                    if 'delta' in opt_chain.puts.columns:
                        print(f"NaNs in put delta: {opt_chain.puts['delta'].isna().sum()} / {len(opt_chain.puts)}")

        # Test a specific GEX calculation for one strike if data is available
        if not opt_chain.calls.empty and 'gamma' in opt_chain.calls.columns and 'openInterest' in opt_chain.calls.columns and spot_price is not None:
            sample_call = opt_chain.calls.dropna(subset=['gamma', 'openInterest']).iloc[0]
            call_oi = sample_call['openInterest']
            call_gamma = sample_call['gamma']
            strike = sample_call['strike']

            # Formula: OI * 100 * Gamma * SpotPrice^2 * 0.01
            # (This is $ GEX per 1% move in underlying, assuming Gamma is dDelta/dPrice)
            call_gex_value = call_oi * 100 * call_gamma * (spot_price**2) * 0.01
            print(f"\nSample Call GEX calculation for strike {strike}:")
            print(f"  OI: {call_oi}, Gamma: {call_gamma}, Spot: {spot_price}")
            print(f"  Calculated Call GEX Value (per 1% move): {call_gex_value}")

    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    check_greeks("AAPL")
    # You can also check other tickers like:
    # check_greeks("MSFT")
    # check_greeks("SPY") # SPY often has very liquid options
    # check_greeks("GOOG")
    # check_greeks("AMC") # Example of a meme stock, might have different liquidity patterns
    # check_greeks("TSLA")
    # check_greeks("VXX") # An ETN, options behave differently
    # check_greeks("GLD") # ETF for Gold
    # check_greeks("QQQ") # Nasdaq 100 ETF
    check_greeks("SPY")
