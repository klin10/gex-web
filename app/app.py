import datetime
import os
from flask import Flask, render_template, request, jsonify
from .models import db, Ticker, Expiration, GEXStrikeData
from .gex_calculator import GEXCalculator # Needed for default risk rate

app = Flask(__name__)

# --- Database Configuration ---
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'gex_data.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)
# --- End Database Configuration ---


@app.route('/')
def index():
    return render_template('index.html')

@app.route('/gex', methods=['GET'])
def get_gex_data_from_db():
    ticker_symbol_req = request.args.get('ticker')
    # Use getlist to handle multiple expiration parameters
    selected_exp_date_str_reqs = request.args.getlist('expiration')

    if not ticker_symbol_req:
        return jsonify({"error": "Ticker symbol is required"}), 400

    ticker_symbol = ticker_symbol_req.upper()

    # 1. Find the Ticker in DB
    ticker_orm = db.session.query(Ticker).filter_by(symbol=ticker_symbol).first()
    if not ticker_orm:
        return jsonify({
            "error": f"Data for ticker {ticker_symbol} not found in database. Please run ingestor.",
            "ticker": ticker_symbol,
            "all_expiration_dates": []
        }), 404

    company_name = ticker_orm.company_name or ticker_symbol

    # 2. Get all available (ingested) expiration dates for this ticker from DB
    expirations_orm = db.session.query(Expiration).filter_by(ticker_id=ticker_orm.id).order_by(Expiration.date).all()
    all_exp_dates_db = [exp.date.strftime('%Y-%m-%d') for exp in expirations_orm]

    if not all_exp_dates_db:
        return jsonify({
            "error": f"No expiration dates found in database for {ticker_symbol}. Please run ingestor.",
            "ticker": company_name,
            "all_expiration_dates": []
        }), 404

    # 3. Determine which expiration dates to use
    expirations_to_query_orm = []
    if selected_exp_date_str_reqs:
        for selected_date_str in selected_exp_date_str_reqs:
            found = False
            for exp_orm in expirations_orm:
                if exp_orm.date.strftime('%Y-%m-%d') == selected_date_str:
                    expirations_to_query_orm.append(exp_orm)
                    found = True
                    break
            if not found:
                return jsonify({"error": f"Selected expiration date {selected_date_str} not found in DB."}), 400
    else:
        # Default to the first (earliest) expiration date if none are specified
        expirations_to_query_orm.append(expirations_orm[0])

    selected_exp_dates_to_display = [exp.date.strftime('%Y-%m-%d') for exp in expirations_to_query_orm]
    expiration_ids_to_query = [exp.id for exp in expirations_to_query_orm]

    # 4. Fetch all GEX strike data for the selected expirations
    all_gex_strikes_db = db.session.query(GEXStrikeData).filter(GEXStrikeData.expiration_id.in_(expiration_ids_to_query)).all()

    if not all_gex_strikes_db:
        # This case might be hit if expirations exist but have no strike data
        return jsonify({
            "error": f"No GEX data found in database for {ticker_symbol} on the selected dates.",
            "ticker": company_name, "all_expiration_dates": all_exp_dates_db,
            "selected_expiration_date": selected_exp_dates_to_display, "gex_by_strike": []
        }), 404

    # --- Aggregation Logic ---
    aggregated_strikes = {}
    for strike_db in all_gex_strikes_db:
        strike_val = strike_db.strike
        if strike_val not in aggregated_strikes:
            aggregated_strikes[strike_val] = {
                'strike': strike_val, 'call_gex_usd': 0, 'put_gex_usd': 0,
                'net_gex_usd': 0, 'call_oi': 0, 'put_oi': 0,
                'spot_price_at_calculation': strike_db.spot_price_at_calculation, # Take first one
                'calculation_timestamp': strike_db.calculation_timestamp # Take first one
            }

        aggregated_strikes[strike_val]['call_gex_usd'] += strike_db.call_gex_usd or 0
        aggregated_strikes[strike_val]['put_gex_usd'] += strike_db.put_gex_usd or 0
        aggregated_strikes[strike_val]['net_gex_usd'] += strike_db.net_gex_usd or 0
        aggregated_strikes[strike_val]['call_oi'] += strike_db.call_oi or 0
        aggregated_strikes[strike_val]['put_oi'] += strike_db.put_oi or 0

    # --- Filtering Logic ---
    MIN_STRIKES_TO_FILTER = 50
    OI_PERCENTILE_THRESHOLD = 0.95

    strikes_list = list(aggregated_strikes.values())

    if len(strikes_list) > MIN_STRIKES_TO_FILTER:
        for strike in strikes_list:
            strike['total_oi'] = strike['call_oi'] + strike['put_oi']

        total_oi_for_expiration = sum(s['total_oi'] for s in strikes_list)
        sorted_strikes = sorted(strikes_list, key=lambda s: s['total_oi'], reverse=True)

        oi_accumulator = 0
        filtered_strikes = []
        for strike in sorted_strikes:
            if total_oi_for_expiration > 0:
                oi_accumulator += strike['total_oi']
                filtered_strikes.append(strike)
                if (oi_accumulator / total_oi_for_expiration) >= OI_PERCENTILE_THRESHOLD:
                    break

        gex_strikes_to_display = sorted(filtered_strikes, key=lambda s: s['strike'])
    else:
        gex_strikes_to_display = sorted(strikes_list, key=lambda s: s['strike'])

    # 5. Format data for response
    strike_data_list_resp = gex_strikes_to_display
    total_net_gex = sum(s['net_gex_usd'] for s in gex_strikes_to_display)
    total_call_gex = sum(s['call_gex_usd'] for s in gex_strikes_to_display)
    total_put_gex = sum(s['put_gex_usd'] for s in gex_strikes_to_display)

    # Take the spot price from the latest selected expiration date for display
    spot_price_at_calc = expirations_to_query_orm[-1].strikes[0].spot_price_at_calculation if expirations_to_query_orm and expirations_to_query_orm[-1].strikes else None

    # Only return zero_gamma_level if exactly one expiration is selected
    zero_gamma_level_val = None
    if len(expirations_to_query_orm) == 1:
        zero_gamma_level_val = expirations_to_query_orm[0].zero_gamma_level

    return jsonify({
        "ticker": company_name,
        "all_expiration_dates": all_exp_dates_db,
        "selected_expiration_date": selected_exp_dates_to_display,
        "spot_price_used": round(spot_price_at_calc, 2) if spot_price_at_calc else None,
        "risk_free_rate_used": GEXCalculator().risk_free_rate,
        "zero_gamma_level": zero_gamma_level_val,
        "gex_by_strike": strike_data_list_resp,
        "total_net_gex_usd": round(total_net_gex, 2),
        "total_call_gex_usd": round(total_call_gex, 2),
        "total_put_gex_usd": round(total_put_gex, 2),
        "data_source_timestamp": expirations_to_query_orm[-1].last_fetched_gex.isoformat() if expirations_to_query_orm and expirations_to_query_orm[-1].last_fetched_gex else None,
        "calculation_notes": [
            "GEX data retrieved from database.",
            "GEX is Gamma Exposure in USD per 1% move in the underlying stock price.",
            "Net GEX = Call GEX - Put GEX."
        ]
    })


@app.cli.command('init-db')
def init_db_command():
    """Creates the database tables."""
    with app.app_context():
        db.create_all()
    print('Initialized the database.')

if __name__ == '__main__':
    app.run(debug=True, port=8080)
