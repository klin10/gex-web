from flask import Flask, render_template, request, jsonify
from flask_caching import Cache
import yfinance as yf
import pandas as pd
import datetime
import numpy as np
from scipy.stats import norm
import math

app = Flask(__name__)
# Configure cache
cache = Cache(app, config={'CACHE_TYPE': 'SimpleCache'}) # Simple in-memory cache

# --- Black-Scholes Model Implementation ---
RISK_FREE_RATE = 0.03 # Placeholder for risk-free rate (e.g., 3%)

def calculate_time_to_expiration(exp_date_str):
    """Calculates time to expiration in years from a YYYY-MM-DD string."""
    exp_date = datetime.datetime.strptime(exp_date_str, '%Y-%m-%d').date()
    today = datetime.date.today()
    delta = exp_date - today
    return max(delta.days / 365.25, 1e-6) # Avoid division by zero or negative time

def black_scholes_gamma(S, K, T, r, sigma):
    """
    Calculates Black-Scholes Gamma for an option.
    S: Spot price
    K: Strike price
    T: Time to expiration (years)
    r: Risk-free rate
    sigma: Implied volatility
    """
    if sigma == 0 or T == 0: # Avoid division by zero if sigma or T is zero
        return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    # pdf is n(d1) in the formula n(d1) / (S * sigma * sqrt(T))
    gamma = norm.pdf(d1) / (S * sigma * math.sqrt(T))
    return gamma

# --- End Black-Scholes ---


# Helper function to get all valid future expiration dates
def get_all_future_expiration_dates(ticker_obj):
    expirations = ticker_obj.options
    if not expirations:
        return []

    today = datetime.date.today()
    valid_expirations = []
    for exp_str in expirations:
        try:
            exp_date = datetime.datetime.strptime(exp_str, '%Y-%m-%d').date()
            if exp_date >= today:
                valid_expirations.append(exp_str)
        except ValueError:
            continue

    return sorted(valid_expirations)


# Helper function to find the next upcoming options expiration date (will be used as default)
def get_next_expiration_date(ticker_obj):
    expirations = ticker_obj.options
    if not expirations:
        return None

    today = datetime.date.today()
    valid_expirations = []
    for exp_str in expirations:
        try:
            exp_date = datetime.datetime.strptime(exp_str, '%Y-%m-%d').date()
            if exp_date >= today:
                valid_expirations.append(exp_date)
        except ValueError:
            # Handle cases where date format might be different or invalid
            continue

    if not valid_expirations:
        return None

    return min(valid_expirations).strftime('%Y-%m-%d')

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/gex', methods=['GET'])
@cache.cached(timeout=1800, query_string=True) # Cache for 30 mins
def get_gex_data_detailed():
    ticker_symbol = request.args.get('ticker')
    selected_exp_date_str = request.args.get('expiration')

    if not ticker_symbol:
        return jsonify({"error": "Ticker symbol is required"}), 400

    try:
        stock_yf_ticker = yf.Ticker(ticker_symbol)
        company_name = stock_yf_ticker.info.get('longName', ticker_symbol.upper())

        all_exp_dates = get_all_future_expiration_dates(stock_yf_ticker)
        if not all_exp_dates:
            return jsonify({
                "error": f"No options expiration dates found for {ticker_symbol}",
                "ticker": company_name,
                "all_expiration_dates": []
            }), 404

        if selected_exp_date_str:
            if selected_exp_date_str not in all_exp_dates:
                return jsonify({"error": f"Selected expiration date {selected_exp_date_str} is not valid for {ticker_symbol}."}), 400
            exp_date_to_fetch = selected_exp_date_str
        else:
            exp_date_to_fetch = all_exp_dates[0] # Default to the first available (nearest)

        # --- Fetch Spot Price ---
        try:
            spot_price = stock_yf_ticker.history(period="1d")['Close'].iloc[-1]
            if pd.isna(spot_price): # Handle potential NaN if history is empty or problematic
                 return jsonify({"error": f"Could not retrieve current price for {ticker_symbol}. Price is NaN."}), 500
        except IndexError: # Handle case where history is empty
            return jsonify({"error": f"Could not retrieve current price for {ticker_symbol}. No history data."}), 500
        except Exception as e: # Catch other history-related errors
            print(f"Error fetching spot price for {ticker_symbol}: {e}")
            return jsonify({"error": f"Could not retrieve current price for {ticker_symbol}. Error: {str(e)}"}), 500


        # --- Fetch Option Chain for selected expiration ---
        opt_chain = stock_yf_ticker.option_chain(exp_date_to_fetch)
        calls_df = opt_chain.calls
        puts_df = opt_chain.puts

        time_to_expiration_T = calculate_time_to_expiration(exp_date_to_fetch)

        gex_data_by_strike = {} # Using dict to aggregate by strike

        # Process Calls
        for _, row in calls_df.iterrows():
            strike = float(row['strike'])
            oi = float(row.get('openInterest', 0))
            iv = float(row.get('impliedVolatility', 0))

            if oi == 0 or iv == 0: # Skip if no OI or IV (Gamma would be 0 or undefined)
                gamma = 0.0
            else:
                gamma = black_scholes_gamma(spot_price, strike, time_to_expiration_T, RISK_FREE_RATE, iv)

            # GEX Value ($ per 1% move in underlying) for this specific call option series
            # Formula: OI * 100 (shares/contract) * Gamma * SpotPrice^2 * 0.01
            call_gex_value = oi * 100 * gamma * (spot_price**2) * 0.01

            if strike not in gex_data_by_strike:
                gex_data_by_strike[strike] = {'call_gex': 0.0, 'put_gex': 0.0, 'net_gex': 0.0}
            gex_data_by_strike[strike]['call_gex'] += call_gex_value
            gex_data_by_strike[strike]['net_gex'] += call_gex_value

        # Process Puts
        for _, row in puts_df.iterrows():
            strike = float(row['strike'])
            oi = float(row.get('openInterest', 0))
            iv = float(row.get('impliedVolatility', 0))

            if oi == 0 or iv == 0:
                gamma = 0.0
            else:
                gamma = black_scholes_gamma(spot_price, strike, time_to_expiration_T, RISK_FREE_RATE, iv)

            # GEX Value ($ per 1% move in underlying) for this specific put option series
            # Formula: OI * 100 (shares/contract) * Gamma * SpotPrice^2 * 0.01
            # Note: In the net GEX formula, Put GEX is subtracted. So we calculate its magnitude here.
            put_gex_value = oi * 100 * gamma * (spot_price**2) * 0.01

            if strike not in gex_data_by_strike:
                gex_data_by_strike[strike] = {'call_gex': 0.0, 'put_gex': 0.0, 'net_gex': 0.0}
            gex_data_by_strike[strike]['put_gex'] += put_gex_value
            gex_data_by_strike[strike]['net_gex'] -= put_gex_value # Subtracting Put GEX

        # Convert dict to sorted list for response
        # And calculate total net GEX
        strike_data_list = []
        total_net_gex = 0.0
        total_call_gex = 0.0
        total_put_gex = 0.0

        for strike_price in sorted(gex_data_by_strike.keys()):
            data = gex_data_by_strike[strike_price]
            strike_data_list.append({
                "strike": strike_price,
                "call_gex_usd": round(data['call_gex'], 2),
                "put_gex_usd": round(data['put_gex'], 2),
                "net_gex_usd": round(data['net_gex'], 2)
            })
            total_net_gex += data['net_gex']
            total_call_gex += data['call_gex']
            total_put_gex += data['put_gex']

        return jsonify({
            "ticker": company_name,
            "all_expiration_dates": all_exp_dates,
            "selected_expiration_date": exp_date_to_fetch,
            "spot_price_used": round(spot_price, 2),
            "risk_free_rate_used": RISK_FREE_RATE,
            "time_to_expiration_years_used": round(time_to_expiration_T, 4),
            "gex_by_strike": strike_data_list,
            "total_net_gex_usd": round(total_net_gex, 2),
            "total_call_gex_usd": round(total_call_gex, 2),
            "total_put_gex_usd": round(total_put_gex, 2),
            "calculation_notes": [
                "GEX is Gamma Exposure in USD per 1% move in the underlying stock price.",
                "Formula per option: OI * 100 * Calculated_Gamma * SpotPrice^2 * 0.01.",
                "Calculated_Gamma is derived using Black-Scholes model.",
                "Net GEX = Call GEX - Put GEX."
            ]
        })

    except Exception as e:
        # Log the exception e for debugging
        import traceback
        print(f"Error processing /gex for {ticker_symbol} (exp: {selected_exp_date_str}): {e}\n{traceback.format_exc()}")
        return jsonify({"error": f"Failed to retrieve or process GEX data for {ticker_symbol}. Error: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(debug=True)
