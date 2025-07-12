import datetime
import os
from flask import Flask, render_template, request, jsonify
from .models import db, Ticker, Expiration, GEXStrikeData # Corrected relative import
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
    selected_exp_date_str_req = request.args.get('expiration')

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

    # 3. Determine which expiration date to use
    expiration_to_query_orm = None
    if selected_exp_date_str_req:
        try:
            selected_exp_date_obj = datetime.datetime.strptime(selected_exp_date_str_req, '%Y-%m-%d').date()
            # Find the Expiration ORM object that matches
            for exp_orm_item in expirations_orm:
                if exp_orm_item.date == selected_exp_date_obj:
                    expiration_to_query_orm = exp_orm_item
                    break
            if not expiration_to_query_orm:
                 return jsonify({"error": f"Selected expiration date {selected_exp_date_str_req} not found in database for {ticker_symbol}."}), 400
        except ValueError:
            return jsonify({"error": "Invalid expiration date format. Use YYYY-MM-DD."}), 400
    else:
        # Default to the first (earliest) expiration date from the DB list
        expiration_to_query_orm = expirations_orm[0]

    selected_exp_date_to_display = expiration_to_query_orm.date.strftime('%Y-%m-%d')

    # 4. Fetch GEX strike data for this ticker and expiration from DB
    gex_strikes_db = db.session.query(GEXStrikeData).filter_by(expiration_id=expiration_to_query_orm.id).order_by(GEXStrikeData.strike).all()

    if not gex_strikes_db:
        return jsonify({
            "error": f"No GEX data found in database for {ticker_symbol} on {selected_exp_date_to_display}. Ingestor might have failed for this date.",
            "ticker": company_name,
            "all_expiration_dates": all_exp_dates_db,
            "selected_expiration_date": selected_exp_date_to_display,
            "gex_by_strike": [] # Ensure frontend can handle this
        }), 404

    # 5. Format data for response
    strike_data_list_resp = []
    total_net_gex = 0.0
    total_call_gex = 0.0
    total_put_gex = 0.0
    spot_price_at_calc = None

    for strike_db in gex_strikes_db:
        strike_data_list_resp.append({
            "strike": strike_db.strike,
            "call_gex_usd": strike_db.call_gex_usd,
            "put_gex_usd": strike_db.put_gex_usd,
            "net_gex_usd": strike_db.net_gex_usd
        })
        total_call_gex += strike_db.call_gex_usd or 0
        total_put_gex += strike_db.put_gex_usd or 0
        total_net_gex += strike_db.net_gex_usd or 0
        if spot_price_at_calc is None and strike_db.spot_price_at_calculation is not None:
            spot_price_at_calc = strike_db.spot_price_at_calculation

    return jsonify({
        "ticker": company_name,
        "all_expiration_dates": all_exp_dates_db,
        "selected_expiration_date": selected_exp_date_to_display,
        "spot_price_used": round(spot_price_at_calc, 2) if spot_price_at_calc else None,
        "risk_free_rate_used": GEXCalculator().risk_free_rate, # Using default from calculator
        "zero_gamma_level": expiration_to_query_orm.zero_gamma_level, # Add the new field
        "gex_by_strike": strike_data_list_resp,
        "total_net_gex_usd": round(total_net_gex, 2),
        "total_call_gex_usd": round(total_call_gex, 2),
        "total_put_gex_usd": round(total_put_gex, 2),
        "data_source_timestamp": expiration_to_query_orm.last_fetched_gex.isoformat() if expiration_to_query_orm.last_fetched_gex else None,
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
