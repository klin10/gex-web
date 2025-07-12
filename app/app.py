from flask import Flask, render_template, request, jsonify
from flask_caching import Cache
import yfinance as yf
import pandas as pd
import datetime

app = Flask(__name__)
# Configure cache
cache = Cache(app, config={'CACHE_TYPE': 'SimpleCache'}) # Simple in-memory cache

# Helper function to find the next upcoming options expiration date
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
@cache.cached(timeout=3600, query_string=True) # Cache for 1 hour, vary on query string (ticker)
def get_gex_data_cached():
    ticker_symbol = request.args.get('ticker')
    if not ticker_symbol:
        return jsonify({"error": "Ticker symbol is required"}), 400

    try:
        stock = yf.Ticker(ticker_symbol)

        # Get company name
        company_name = stock.info.get('longName', ticker_symbol.upper())

        # Get the closest (or next) expiration date for options
        # yfinance returns a tuple of expiration dates
        exp_date = get_next_expiration_date(stock)
        if not exp_date:
            return jsonify({"error": f"No options expiration dates found for {ticker_symbol}"}), 404

        # Fetch options data for that expiration date
        opt_chain = stock.option_chain(exp_date)

        calls = opt_chain.calls
        puts = opt_chain.puts

        # Simplified GEX calculation: (Total Call OI) - (Total Put OI)
        # Each contract represents 100 shares
        # A more accurate GEX would involve Gamma and Delta of each option

        # Ensure 'openInterest' column exists and is numeric
        if 'openInterest' not in calls.columns or 'openInterest' not in puts.columns:
            return jsonify({"error": f"Open interest data not available for {ticker_symbol} on {exp_date}"}), 500

        calls['openInterest'] = pd.to_numeric(calls['openInterest'], errors='coerce').fillna(0)
        puts['openInterest'] = pd.to_numeric(puts['openInterest'], errors='coerce').fillna(0)

        total_call_oi_value = int((calls['openInterest'] * 100).sum())
        total_put_oi_value = int((puts['openInterest'] * 100).sum())

        # This is a simplified GEX-like exposure.
        # Positive value suggests dealers are net long gamma (market makers sold calls/bought puts)
        # Negative value suggests dealers are net short gamma (market makers bought calls/sold puts)
        # For GEX, it's typically (Call OI * Gamma_call) - (Put OI * Gamma_put), summed over strikes.
        # Here, we are using a proxy: Sum(Call OI) - Sum(Put OI)
        # The sign convention for GEX is often: Call GEX - Put GEX.
        # Call GEX = Call OI * Gamma (positive)
        # Put GEX = Put OI * Gamma (negative, because short puts have positive gamma, but GEX is exposure)
        # For this simplified version:
        # GEX = (Call OI * 100 shares/contract) - (Put OI * 100 shares/contract)
        # This is more like "Net Open Interest Value" rather than true Gamma Exposure.
        # Let's refine this slightly. Gamma exposure is the change in delta for a $1 move in underlying.
        # Call Open Interest represents potential for positive gamma if market makers are short calls.
        # Put Open Interest represents potential for positive gamma if market makers are short puts.
        # GEX = Sum over strikes [ (OI_call * Gamma_call) - (OI_put * Gamma_put) ] * 100
        # For a rough estimate without individual gammas:
        # If dealers are short calls, they are long gamma. OI_call contributes positively.
        # If dealers are short puts, they are long gamma. OI_put contributes positively.
        # However, the market impact is what we care about.
        # Let's stick to the common interpretation: GEX = sum(OI_call * gamma_c) - sum(OI_put * gamma_p)
        # Without gamma, a simpler proxy is (Sum Call OI) - (Sum Put OI)
        # Let's call this "Net Options Exposure" to be clear it's not true GEX.

        # The common formula for GEX ($ per 1% move) =
        # Sum over all strikes [ (Call OI * Call Gamma * Underlying Price * 0.01)^2 - (Put OI * Put Gamma * Underlying Price * 0.01)^2 ] * 100
        # This is too complex for now.

        # Let's use the definition: Total Gamma = sum(gamma_call * OI_call * 100) + sum(gamma_put * OI_put * 100)
        # Dollar Gamma = Total Gamma * Stock Price ^ 2 * 0.01
        # GEX (Gamma Exposure) is often defined as the change in dealer's delta for a 1% move in the underlying.
        # GEX = Sum over strikes [(Call OI * Call Delta) - (Put OI * abs(Put Delta))] * 100 shares
        # This is also complex as it requires Delta.

        # Sticking to a very simplified approach based on OI only for now:
        # Net Call OI = sum(calls['openInterest'])
        # Net Put OI = sum(puts['openInterest'])
        # Simplified Exposure = (Net Call OI - Net Put OI) * 100 (shares)
        # This is a proxy for directional bias more than gamma exposure.

        # Let's use the formula from a known source:
        # GEX = Σ (Call OI * Call Gamma) - Σ (Put OI * Put Gamma)
        # Since we don't have Gamma easily from yfinance basic calls, we'll use a proxy.
        # A common simplified proxy for GEX is based on OI and assumes gamma is positive for calls and negative for puts from the market maker's perspective if they are short options.
        # GEX = (Sum of Call Open Interest * 100 shares) - (Sum of Put Open Interest * 100 shares)
        # This calculates the net number of shares dealers would have to buy/sell to remain delta neutral if they are short these options and the stock price moves.
        # If GEX is positive, dealers are net short calls / long puts, and would buy as price rises, sell as price falls (stabilizing).
        # If GEX is negative, dealers are net long calls / short puts, and would sell as price rises, buy as price falls (destabilizing).
        # Wait, the interpretation of GEX sign can vary.
        # Let's use: GEX = Sum over strikes [OI_c * Gamma_c - OI_p * Gamma_p] * 100
        # Without Gamma, the most basic proxy is simply looking at OI.
        # Total Call OI value vs Total Put OI value.
        # If we assume market makers are typically net short options (provide liquidity):
        # Short calls = +Gamma exposure for MM
        # Short puts = +Gamma exposure for MM
        # So, Total Gamma Exposure ~ (Call OI + Put OI) * AvgGamma * 100. This is not GEX.

        # Let's use the definition from SqueezeMetrics:
        # GEX = (Call Open Interest * Call Gamma) - (Put Open Interest * Put Gamma), summed across all strikes.
        # Gamma is positive for long calls and long puts.
        # If market makers are net short, their gamma exposure is negative of this.
        # For simplicity, if we cannot get Gamma, we will report Call OI and Put OI separately.

        # Given the limitations of yfinance not directly providing gamma per strike easily,
        # I will calculate a "Net OI Exposure" which is Call OI - Put OI.
        # This is a common simplification, though not true GEX.
        # GEX = (call open interest – put open interest) * 100 (shares per contract) * share price
        # This is dollar GEX. We'll calculate share GEX.

        # Net OI in shares = (Sum of Call OI - Sum of Put OI) * 100
        net_oi_exposure_shares = int(total_call_oi_value - total_put_oi_value)

        # current_price = stock.history(period="1d")['Close'].iloc[-1] # Not used in current response
        # Dollar GEX (approx) = Net OI Exposure (shares) * Current Price
        # This interpretation is: if price moves $1, how much value dealers need to trade.
        # No, GEX is typically $ per 1% move.
        # Dollar Gamma = Sum (Gamma * OI * 100 * Stock Price^2 * 0.01)

        # Let's stick to the most basic interpretation of GEX for now, which is often
        # presented as the total gamma from calls minus total gamma from puts.
        # Since we don't have gamma, we'll use OI as a proxy.
        # This is a simplification. A positive value would imply more call OI than put OI.
        # A common way to calculate GEX (simplified):
        # GEX_per_strike = (Call_OI * 100) - (Put_OI * 100)
        # Total_GEX = sum(GEX_per_strike)
        # This is effectively what `net_oi_exposure_shares` calculates.

        return jsonify({
            "ticker": company_name,
            "selected_expiration_date": exp_date,
            "total_call_open_interest_shares": total_call_oi_value,
            "total_put_open_interest_shares": total_put_oi_value,
            "net_open_interest_exposure_shares": net_oi_exposure_shares,
            "calculation_note": "This is a simplified GEX-like exposure based on Net Open Interest (Call OI - Put OI). True GEX requires individual option gamma values."
        })

    except Exception as e:
        # Log the exception e for debugging
        print(f"Error fetching or processing data for {ticker_symbol}: {e}")
        return jsonify({"error": f"Failed to retrieve or process GEX data for {ticker_symbol}. Error: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(debug=True)
