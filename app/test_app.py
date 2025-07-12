import unittest
from unittest.mock import patch, MagicMock
import json
import datetime
from app.app import app, get_next_expiration_date # Corrected import

class MockTicker:
    def __init__(self, ticker_symbol, has_options=True, options_dates=None, option_chain_data=None, info_data=None):
        self.ticker = ticker_symbol
        self._has_options = has_options
        self.options = options_dates if options_dates is not None else ('2025-12-31',)
        self._option_chain_data = option_chain_data
        self.info = info_data if info_data is not None else {'longName': ticker_symbol.upper()}
        if not self._has_options:
            self.options = tuple()

    def option_chain(self, date_str):
        if not self._has_options or self._option_chain_data is None:
            # Simulate yfinance behavior for no data or error
            # yfinance might return an empty DataFrame or raise an exception.
            # For simplicity, we'll have it return an object that lacks .calls or .puts
            # or has them as None or empty.
            mock_chain = MagicMock()
            mock_chain.calls = pd.DataFrame({'openInterest': []}) # Empty DataFrame
            mock_chain.puts = pd.DataFrame({'openInterest': []})  # Empty DataFrame
            return mock_chain
        return self._option_chain_data

    def history(self, period):
        # Mock history call to get current price
        import pandas as pd
        return pd.DataFrame({'Close': [100.0]})


# Import pandas here as it's used in MockTicker and app.py
import pandas as pd

class TestApp(unittest.TestCase):

    def setUp(self):
        self.app = app.test_client()
        self.app_context = app.app_context()
        self.app_context.push()
        app.config['TESTING'] = True
        app.config['CACHE_TYPE'] = 'NullCache' # Disable cache for testing

    def tearDown(self):
        self.app_context.pop()

    @patch('yfinance.Ticker')
    def test_get_gex_data_success(self, mock_yf_ticker):
        # Mock yfinance.Ticker response
        mock_calls_df = pd.DataFrame({'openInterest': [10, 20, 0], 'strike': [100, 105, 110]})
        mock_puts_df = pd.DataFrame({'openInterest': [5, 15, 0], 'strike': [100, 95, 90]})

        mock_option_chain = MagicMock()
        mock_option_chain.calls = mock_calls_df
        mock_option_chain.puts = mock_puts_df

        mock_ticker_instance = MockTicker(
            'AAPL',
            options_dates=('2025-01-01', '2025-12-31'), # Ensure get_next_expiration_date works
            option_chain_data=mock_option_chain,
            info_data={'longName': 'Apple Inc.'}
        )
        mock_yf_ticker.return_value = mock_ticker_instance

        # Patch get_next_expiration_date to return a fixed date for predictability
        # Or ensure MockTicker.options is set up to make get_next_expiration_date deterministic
        # For this test, we rely on MockTicker.options and the actual get_next_expiration_date logic

        response = self.app.get('/gex?ticker=AAPL')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)

        self.assertEqual(data['ticker'], 'Apple Inc.')
        self.assertIn('selected_expiration_date', data)
        # This will depend on today's date, let's ensure it's one of the options
        # For more robust test, mock datetime.date.today() in get_next_expiration_date
        # or ensure a future date is picked.
        # For now, we check if it's a valid date string.
        try:
            datetime.datetime.strptime(data['selected_expiration_date'], '%Y-%m-%d')
        except ValueError:
            self.fail("selected_expiration_date is not a valid date string")


        self.assertEqual(data['total_call_open_interest_shares'], (10 + 20) * 100)
        self.assertEqual(data['total_put_open_interest_shares'], (5 + 15) * 100)
        expected_net_oi = ((10 + 20) - (5 + 15)) * 100
        self.assertEqual(data['net_open_interest_exposure_shares'], expected_net_oi)
        self.assertIn('calculation_note', data)

    def test_get_gex_data_no_ticker(self):
        response = self.app.get('/gex')
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.data)
        self.assertEqual(data['error'], 'Ticker symbol is required')

    @patch('yfinance.Ticker')
    def test_get_gex_data_no_options_data(self, mock_yf_ticker):
        # Simulate a ticker that has no options
        mock_ticker_instance = MockTicker('NOPT', has_options=False)
        mock_yf_ticker.return_value = mock_ticker_instance

        response = self.app.get('/gex?ticker=NOPT')
        self.assertEqual(response.status_code, 404)
        data = json.loads(response.data)
        self.assertEqual(data['error'], 'No options expiration dates found for NOPT')

    @patch('yfinance.Ticker')
    def test_get_gex_data_missing_open_interest_column(self, mock_yf_ticker):
        # Simulate option chain data missing the 'openInterest' column
        mock_bad_calls_df = pd.DataFrame({'strike': [100, 105]}) # Missing 'openInterest'
        mock_puts_df = pd.DataFrame({'openInterest': [5, 15], 'strike': [100, 95]})

        mock_option_chain = MagicMock()
        mock_option_chain.calls = mock_bad_calls_df
        mock_option_chain.puts = mock_puts_df

        mock_ticker_instance = MockTicker(
            'BADCOL',
            option_chain_data=mock_option_chain,
            info_data={'longName': 'Bad Column Corp'}
        )
        mock_yf_ticker.return_value = mock_ticker_instance

        response = self.app.get('/gex?ticker=BADCOL')
        self.assertEqual(response.status_code, 500)
        data = json.loads(response.data)
        self.assertTrue(data['error'].startswith('Open interest data not available'))


    def test_get_next_expiration_date(self):
        mock_ticker_obj = MagicMock()
        today = datetime.date(2024, 7, 15)

        # Patch datetime.date.today()
        with patch('datetime.date') as mock_date:
            mock_date.today.return_value = today
            mock_date.side_effect = lambda *args, **kw: datetime.date(*args, **kw) # Allow date construction

            # Case 1: No expirations
            mock_ticker_obj.options = []
            self.assertIsNone(get_next_expiration_date(mock_ticker_obj))

            # Case 2: Only past expirations
            mock_ticker_obj.options = ('2024-07-01', '2024-07-10')
            self.assertIsNone(get_next_expiration_date(mock_ticker_obj))

            # Case 3: Mix of past and future, should pick closest future
            mock_ticker_obj.options = ('2024-07-01', '2024-07-20', '2024-07-30', '2024-07-18')
            self.assertEqual(get_next_expiration_date(mock_ticker_obj), '2024-07-18')

            # Case 4: All future, should pick earliest
            mock_ticker_obj.options = ('2024-08-01', '2024-07-25', '2025-01-01')
            self.assertEqual(get_next_expiration_date(mock_ticker_obj), '2024-07-25')

            # Case 5: Expiration is today
            mock_ticker_obj.options = ('2024-07-15', '2024-07-20')
            self.assertEqual(get_next_expiration_date(mock_ticker_obj), '2024-07-15')

            # Case 6: Invalid date format in options (should be skipped)
            mock_ticker_obj.options = ('2024-07-20', 'invalid-date', '2024-07-25')
            self.assertEqual(get_next_expiration_date(mock_ticker_obj), '2024-07-20')


if __name__ == '__main__':
    unittest.main()
