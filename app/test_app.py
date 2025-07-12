import unittest
from unittest.mock import patch, MagicMock
import json
import datetime
import pandas as pd
import math
from app.app import app, calculate_time_to_expiration, black_scholes_gamma, get_all_future_expiration_dates, RISK_FREE_RATE

# Mock yf.Ticker class
class MockYFinanceTicker:
    def __init__(self, ticker_symbol, options_dates=None, option_chain_data=None, info_data=None, history_data=None):
        self.ticker = ticker_symbol
        self.options = options_dates if options_dates is not None else tuple()
        self._option_chain_data = option_chain_data if option_chain_data is not None else {}
        self.info = info_data if info_data is not None else {'longName': ticker_symbol.upper()}
        self._history_data = history_data

    def option_chain(self, date_str=None):
        if date_str in self._option_chain_data:
            return self._option_chain_data[date_str]
        # Return empty structure if no data for date
        mock_chain = MagicMock()
        mock_chain.calls = pd.DataFrame(columns=['strike', 'openInterest', 'impliedVolatility'])
        mock_chain.puts = pd.DataFrame(columns=['strike', 'openInterest', 'impliedVolatility'])
        return mock_chain

    def history(self, period="1d"):
        if self._history_data is not None:
            return self._history_data
        # Default history if not provided
        return pd.DataFrame({'Close': [100.0]})


class TestGEXApp(unittest.TestCase):
    def setUp(self):
        self.app_context = app.app_context()
        self.app_context.push()
        app.config['TESTING'] = True
        app.config['CACHE_TYPE'] = 'NullCache' # Disable caching for tests
        self.client = app.test_client()

        # Common mock date for testing time-dependent functions
        self.mock_today = datetime.date(2024, 7, 15)

    def tearDown(self):
        self.app_context.pop()

    # --- Test Helper Functions ---
    def test_calculate_time_to_expiration(self):
        with patch('datetime.date') as mock_date:
            mock_date.today.return_value = self.mock_today
            mock_date.side_effect = lambda *args, **kw: datetime.date(*args, **kw) # Ensure date constructor still works

            exp_str = "2025-07-15" # 1 year from mock_today
            T = calculate_time_to_expiration(exp_str)
            # (datetime.date(2025, 7, 15) - datetime.date(2024, 7, 15)).days = 365
            self.assertAlmostEqual(T, 365 / 365.25, places=5)

            exp_str_short = "2024-07-25" # 10 days from mock_today
            T_short = calculate_time_to_expiration(exp_str_short)
            self.assertAlmostEqual(T_short, 10 / 365.25, places=5)

            # Test for same day expiration (should be small positive, not zero)
            exp_str_today = "2024-07-15"
            T_today = calculate_time_to_expiration(exp_str_today)
            self.assertAlmostEqual(T_today, 1e-6, places=7) # Our minimum

    def test_black_scholes_gamma(self):
        # Using example values where Gamma can be verified (e.g., from an online calculator)
        # S=100, K=100, T=1 (1 year), r=0.05, sigma=0.2
        # Expected Gamma approx 0.0197 (for d1 approx 0.35)
        S, K, T, r, sigma = 100, 100, 1, 0.05, 0.2
        gamma = black_scholes_gamma(S, K, T, r, sigma)
        # Value calculated by the formula: norm.pdf(0.35) / 20 where d1 = 0.35
        # norm.pdf(0.35) approx 0.3752401145510106
        # gamma approx 0.3752401145510106 / 20 = 0.01876200572755053
        self.assertAlmostEqual(gamma, 0.018762, places=5)

        # Test edge case: sigma = 0
        self.assertEqual(black_scholes_gamma(100, 100, 1, 0.05, 0), 0.0)
        # Test edge case: T = 0
        self.assertEqual(black_scholes_gamma(100, 100, 0, 0.05, 0.2), 0.0)


    def test_get_all_future_expiration_dates(self):
        mock_ticker_obj = MagicMock()
        with patch('datetime.date') as mock_date:
            mock_date.today.return_value = self.mock_today

            mock_ticker_obj.options = ('2024-07-01', '2024-07-20', '2025-01-01', 'invalid-date')
            dates = get_all_future_expiration_dates(mock_ticker_obj)
            self.assertEqual(dates, ['2024-07-20', '2025-01-01'])

            mock_ticker_obj.options = []
            self.assertEqual(get_all_future_expiration_dates(mock_ticker_obj), [])

    # --- Test /gex Endpoint ---
    @patch('yfinance.Ticker')
    @patch('datetime.date') # Added mock for datetime.date
    def test_gex_endpoint_success(self, mock_datetime_date, mock_yf_ticker_class):
        mock_datetime_date.today.return_value = self.mock_today # Configure mock
        mock_datetime_date.side_effect = lambda *args, **kw: datetime.date(*args, **kw)

        # Mock data for yfinance.Ticker
        exp_date1 = (self.mock_today + datetime.timedelta(days=30)).strftime('%Y-%m-%d') # Approx 1 month out
        exp_date2 = (self.mock_today + datetime.timedelta(days=90)).strftime('%Y-%m-%d')

        mock_calls_df = pd.DataFrame({
            'strike': [100.0, 105.0],
            'openInterest': [10.0, 20.0],
            'impliedVolatility': [0.2, 0.22]
        })
        mock_puts_df = pd.DataFrame({
            'strike': [100.0, 95.0],
            'openInterest': [5.0, 15.0],
            'impliedVolatility': [0.21, 0.19]
        })

        option_chain_for_exp1 = MagicMock()
        option_chain_for_exp1.calls = mock_calls_df
        option_chain_for_exp1.puts = mock_puts_df

        mock_ticker_instance = MockYFinanceTicker(
            'TEST',
            options_dates=(exp_date1, exp_date2),
            option_chain_data={exp_date1: option_chain_for_exp1},
            info_data={'longName': 'Test Inc.'},
            history_data=pd.DataFrame({'Close': [102.0]}) # Spot price
        )
        mock_yf_ticker_class.return_value = mock_ticker_instance

        response = self.client.get(f'/gex?ticker=TEST&expiration={exp_date1}')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)

        self.assertEqual(data['ticker'], 'Test Inc.')
        self.assertListEqual(data['all_expiration_dates'], [exp_date1, exp_date2])
        self.assertEqual(data['selected_expiration_date'], exp_date1)
        self.assertAlmostEqual(data['spot_price_used'], 102.0)
        self.assertEqual(data['risk_free_rate_used'], RISK_FREE_RATE)

        self.assertIn('gex_by_strike', data)
        self.assertTrue(len(data['gex_by_strike']) > 0) # Check some data is there

        # Verify one strike's data structure (actual values depend on BS calc)
        first_strike_data = data['gex_by_strike'][0]
        self.assertIn('strike', first_strike_data)
        self.assertIn('call_gex_usd', first_strike_data)
        self.assertIn('put_gex_usd', first_strike_data)
        self.assertIn('net_gex_usd', first_strike_data)

        self.assertIn('total_net_gex_usd', data)
        self.assertIn('total_call_gex_usd', data)
        self.assertIn('total_put_gex_usd', data)
        self.assertIn('calculation_notes', data)

        # Test default expiration (first one)
        response_default_exp = self.client.get('/gex?ticker=TEST')
        self.assertEqual(response_default_exp.status_code, 200)
        data_default_exp = json.loads(response_default_exp.data)
        self.assertEqual(data_default_exp['selected_expiration_date'], exp_date1)


    @patch('yfinance.Ticker')
    def test_gex_endpoint_no_ticker(self, mock_yf_ticker_class):
        response = self.client.get('/gex')
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.data)
        self.assertEqual(data['error'], 'Ticker symbol is required')

    @patch('yfinance.Ticker')
    @patch('datetime.date')
    def test_gex_endpoint_no_options_for_ticker(self, mock_datetime_date, mock_yf_ticker_class):
        mock_datetime_date.today.return_value = self.mock_today
        mock_datetime_date.side_effect = lambda *args, **kw: datetime.date(*args, **kw)

        mock_ticker_instance = MockYFinanceTicker('NOOPT', options_dates=tuple())
        mock_yf_ticker_class.return_value = mock_ticker_instance

        response = self.client.get('/gex?ticker=NOOPT')
        self.assertEqual(response.status_code, 404)
        data = json.loads(response.data)
        self.assertEqual(data['error'], 'No options expiration dates found for NOOPT')
        self.assertEqual(data['all_expiration_dates'], [])

    @patch('yfinance.Ticker')
    @patch('datetime.date')
    def test_gex_endpoint_invalid_expiration_date(self, mock_datetime_date, mock_yf_ticker_class):
        mock_datetime_date.today.return_value = self.mock_today
        mock_datetime_date.side_effect = lambda *args, **kw: datetime.date(*args, **kw)

        exp_date1 = (self.mock_today + datetime.timedelta(days=30)).strftime('%Y-%m-%d')
        mock_ticker_instance = MockYFinanceTicker('TEST', options_dates=(exp_date1,))
        mock_yf_ticker_class.return_value = mock_ticker_instance

        response = self.client.get('/gex?ticker=TEST&expiration=2099-01-01') # Date not in options_dates
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.data)
        self.assertTrue(data['error'].startswith('Selected expiration date'))

    @patch('yfinance.Ticker')
    @patch('datetime.date')
    def test_gex_endpoint_spot_price_fetch_error(self, mock_datetime_date, mock_yf_ticker_class):
        mock_datetime_date.today.return_value = self.mock_today
        mock_datetime_date.side_effect = lambda *args, **kw: datetime.date(*args, **kw)

        # Simulate error during history fetch
        exp_date1 = (self.mock_today + datetime.timedelta(days=30)).strftime('%Y-%m-%d')
        mock_ticker_instance = MockYFinanceTicker(
            'SPERR',
            options_dates=(exp_date1,),
            history_data=None # No history data will cause IndexError in current mock or could be an exception
        )
        # Make history() raise an exception
        mock_ticker_instance.history = MagicMock(side_effect=Exception("Simulated yfinance history error"))
        mock_yf_ticker_class.return_value = mock_ticker_instance

        response = self.client.get(f'/gex?ticker=SPERR&expiration={exp_date1}')
        self.assertEqual(response.status_code, 500)
        data = json.loads(response.data)
        self.assertTrue(data['error'].startswith('Could not retrieve current price for SPERR'))

    @patch('yfinance.Ticker')
    @patch('datetime.date')
    def test_gex_endpoint_spot_price_is_nan(self, mock_datetime_date, mock_yf_ticker_class):
        mock_datetime_date.today.return_value = self.mock_today
        mock_datetime_date.side_effect = lambda *args, **kw: datetime.date(*args, **kw)

        exp_date1 = (self.mock_today + datetime.timedelta(days=30)).strftime('%Y-%m-%d')
        mock_ticker_instance = MockYFinanceTicker(
            'SPNAN',
            options_dates=(exp_date1,),
            history_data=pd.DataFrame({'Close': [pd.NA]}) # Simulate NaN price
        )
        mock_yf_ticker_class.return_value = mock_ticker_instance

        response = self.client.get(f'/gex?ticker=SPNAN&expiration={exp_date1}')
        self.assertEqual(response.status_code, 500)
        data = json.loads(response.data)
        self.assertEqual(data['error'], 'Could not retrieve current price for SPNAN. Price is NaN.')


if __name__ == '__main__':
    unittest.main()
