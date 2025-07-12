import unittest
from unittest.mock import patch, MagicMock
import json
import datetime
from app.app import app # Main Flask app instance
from app.models import db, Ticker, Expiration, GEXStrikeData # SQLAlchemy instance and models
from app.gex_calculator import GEXCalculator # For risk_free_rate default


class TestAPIWithDB(unittest.TestCase):
    def setUp(self):
        self.app_context = app.app_context()
        self.app_context.push()
        app.config['TESTING'] = True
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:' # Use in-memory SQLite for tests
        db.create_all() # Create tables for each test
        self.client = app.test_client()

        self.mock_today = datetime.date(2024, 7, 15) # Consistent date for tests

    def tearDown(self):
        db.session.remove()
        db.drop_all() # Drop tables after each test
        self.app_context.pop()

    def _populate_db_for_ticker(self, ticker_symbol, company_name, expirations_data):
        """Helper to populate DB with mock data for a ticker."""
        ticker_orm = Ticker(symbol=ticker_symbol, company_name=company_name)
        db.session.add(ticker_orm)
        db.session.commit() # Commit to get ticker_orm.id

        for exp_data in expirations_data:
            exp_date_obj = datetime.datetime.strptime(exp_data['date_str'], '%Y-%m-%d').date()
            expiration_orm = Expiration(
                ticker_id=ticker_orm.id,
                date=exp_date_obj,
                last_fetched_gex=datetime.datetime.now(datetime.timezone.utc) # Mark as fetched
            )
            db.session.add(expiration_orm)
            db.session.commit() # Commit to get expiration_orm.id

            for strike_data in exp_data['strikes']:
                gex_strike = GEXStrikeData(
                    expiration_id=expiration_orm.id,
                    strike=strike_data['strike'],
                    call_gex_usd=strike_data['call_gex_usd'],
                    put_gex_usd=strike_data['put_gex_usd'],
                    net_gex_usd=strike_data['net_gex_usd'],
                    spot_price_at_calculation=exp_data['spot_price_used'] # Store spot price used
                )
                db.session.add(gex_strike)
        db.session.commit()
        return ticker_orm

    def test_gex_endpoint_db_success(self):
        # Populate DB with some data for "DBTEST"
        exp_date_str1 = (self.mock_today + datetime.timedelta(days=30)).strftime('%Y-%m-%d')
        exp_date_str2 = (self.mock_today + datetime.timedelta(days=60)).strftime('%Y-%m-%d')

        mock_expirations_data = [
            {
                "date_str": exp_date_str1,
                "spot_price_used": 150.0,
                "strikes": [
                    {"strike": 145.0, "call_gex_usd": 1000.0, "put_gex_usd": 50.0, "net_gex_usd": 950.0},
                    {"strike": 150.0, "call_gex_usd": 1200.0, "put_gex_usd": 120.0, "net_gex_usd": 1080.0},
                ]
            },
            {
                "date_str": exp_date_str2,
                "spot_price_used": 152.0,
                "strikes": [
                    {"strike": 150.0, "call_gex_usd": 800.0, "put_gex_usd": 70.0, "net_gex_usd": 730.0},
                ]
            }
        ]
        self._populate_db_for_ticker("DBTEST", "DB Test Inc.", mock_expirations_data)

        # Test fetching specific expiration
        response = self.client.get(f'/gex?ticker=DBTEST&expiration={exp_date_str1}')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)

        self.assertEqual(data['ticker'], 'DB Test Inc.')
        self.assertListEqual(data['all_expiration_dates'], [exp_date_str1, exp_date_str2])
        self.assertEqual(data['selected_expiration_date'], exp_date_str1)
        self.assertAlmostEqual(data['spot_price_used'], 150.0)
        self.assertEqual(data['risk_free_rate_used'], GEXCalculator().risk_free_rate) # From default

        self.assertEqual(len(data['gex_by_strike']), 2)
        self.assertAlmostEqual(data['gex_by_strike'][0]['net_gex_usd'], 950.0)
        self.assertAlmostEqual(data['total_net_gex_usd'], 950.0 + 1080.0)
        self.assertIn('data_source_timestamp', data)

        # Test fetching default expiration (should be exp_date_str1)
        response_default = self.client.get('/gex?ticker=DBTEST')
        self.assertEqual(response_default.status_code, 200)
        data_default = json.loads(response_default.data)
        self.assertEqual(data_default['selected_expiration_date'], exp_date_str1)

    def test_gex_endpoint_db_ticker_not_found(self):
        response = self.client.get('/gex?ticker=NOSUCHTICKER')
        self.assertEqual(response.status_code, 404)
        data = json.loads(response.data)
        self.assertTrue(data['error'].startswith('Data for ticker NOSUCHTICKER not found'))
        self.assertEqual(data['all_expiration_dates'], [])


    def test_gex_endpoint_db_no_expirations_for_ticker(self):
        self._populate_db_for_ticker("EMPTYEXP", "Empty Expirations Inc.", [])
        response = self.client.get('/gex?ticker=EMPTYEXP')
        self.assertEqual(response.status_code, 404)
        data = json.loads(response.data)
        self.assertTrue(data['error'].startswith('No expiration dates found in database'))

    def test_gex_endpoint_db_invalid_expiration_format(self):
        # Need to populate something so it doesn't 404 on ticker first
        exp_date_str1 = (self.mock_today + datetime.timedelta(days=30)).strftime('%Y-%m-%d')
        self._populate_db_for_ticker("DBTESTFORMAT", "DB Test Format Inc.", [{"date_str": exp_date_str1, "spot_price_used": 1.0, "strikes": []}])

        response = self.client.get('/gex?ticker=DBTESTFORMAT&expiration=bad-date')
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.data)
        self.assertEqual(data['error'], 'Invalid expiration date format. Use YYYY-MM-DD.')

    def test_gex_endpoint_db_expiration_not_found_for_ticker(self):
        exp_date_str1 = (self.mock_today + datetime.timedelta(days=30)).strftime('%Y-%m-%d')
        self._populate_db_for_ticker("DBTESTEXP", "DB Test Exp Inc.", [{"date_str": exp_date_str1, "spot_price_used": 1.0, "strikes": []}])

        non_existent_exp = (self.mock_today + datetime.timedelta(days=500)).strftime('%Y-%m-%d')
        response = self.client.get(f'/gex?ticker=DBTESTEXP&expiration={non_existent_exp}')
        self.assertEqual(response.status_code, 400) # Should be 400 as per current logic, date not in list
        data = json.loads(response.data)
        self.assertTrue(data['error'].startswith(f'Selected expiration date {non_existent_exp} not found'))

    def test_gex_endpoint_db_no_gex_data_for_expiration(self):
        exp_date_str1 = (self.mock_today + datetime.timedelta(days=30)).strftime('%Y-%m-%d')
        # Populate ticker and expiration, but no GEXStrikeData for it
        ticker = Ticker(symbol="NOGEXDATA", company_name="No GEX Data Inc.")
        db.session.add(ticker)
        db.session.commit()
        expiration = Expiration(ticker_id=ticker.id, date=datetime.datetime.strptime(exp_date_str1, '%Y-%m-%d').date())
        db.session.add(expiration)
        db.session.commit()

        response = self.client.get(f'/gex?ticker=NOGEXDATA&expiration={exp_date_str1}')
        self.assertEqual(response.status_code, 404)
        data = json.loads(response.data)
        self.assertTrue(data['error'].startswith(f'No GEX data found in database for NOGEXDATA on {exp_date_str1}'))
        self.assertEqual(data['gex_by_strike'], [])

    def test_gex_endpoint_no_ticker_param(self):
        response = self.client.get('/gex')
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.data)
        self.assertEqual(data['error'], 'Ticker symbol is required')

if __name__ == '__main__':
    unittest.main()
