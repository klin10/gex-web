import unittest
import datetime
import pandas as pd
from unittest.mock import patch

from app.gex_calculator import GEXCalculator # Assuming app is in PYTHONPATH or structure allows

class TestGEXCalculator(unittest.TestCase):

    def setUp(self):
        self.calculator = GEXCalculator(risk_free_rate=0.05) # Use a specific rate for tests
        self.mock_today = datetime.date(2024, 7, 15)

    def test_calculate_time_to_expiration(self):
        with patch('datetime.date') as mock_date_for_calc:
            mock_date_for_calc.today.return_value = self.mock_today
            # Ensure datetime.datetime.strptime still works
            mock_date_for_calc.strptime = datetime.datetime.strptime

            exp_str_1_year = "2025-07-15"
            T_1_year = self.calculator._calculate_time_to_expiration(exp_str_1_year)
            self.assertAlmostEqual(T_1_year, 365 / 365.25, places=5)

            exp_str_10_days = "2024-07-25"
            T_10_days = self.calculator._calculate_time_to_expiration(exp_str_10_days)
            self.assertAlmostEqual(T_10_days, 10 / 365.25, places=5)

            exp_str_today = "2024-07-15"
            T_today = self.calculator._calculate_time_to_expiration(exp_str_today)
            self.assertAlmostEqual(T_today, 1e-6, places=7) # Minimum value

    def test_black_scholes_gamma(self):
        # S=100, K=100, T=1, r=0.05 (from setUp), sigma=0.2
        # d1 = (ln(100/100) + (0.05 + 0.5*0.2^2)*1) / (0.2*sqrt(1))
        # d1 = (0 + (0.05 + 0.02)*1) / 0.2 = 0.07 / 0.2 = 0.35
        # gamma = norm.pdf(0.35) / (100 * 0.2 * 1) = norm.pdf(0.35) / 20
        # norm.pdf(0.35) is approx 0.37524
        # Expected gamma approx 0.018762
        S, K, T, sigma = 100, 100, 1, 0.2
        gamma = self.calculator._black_scholes_gamma(S, K, T, sigma)
        self.assertAlmostEqual(gamma, 0.018762, places=5)

        # Edge cases
        self.assertEqual(self.calculator._black_scholes_gamma(100, 100, 1, 0), 0.0) # sigma = 0
        self.assertEqual(self.calculator._black_scholes_gamma(100, 100, 0, 0.2), 0.0) # T = 0

    def test_calculate_gex_for_expiration(self):
        spot_price = 102.0
        exp_date_str = (self.mock_today + datetime.timedelta(days=30)).strftime('%Y-%m-%d')

        # Mock DataFrames
        calls_df = pd.DataFrame({
            'strike': [100.0, 105.0],
            'openInterest': [10.0, 20.0],
            'impliedVolatility': [0.2, 0.22] # IV for calls
        })
        puts_df = pd.DataFrame({
            'strike': [100.0, 95.0],
            'openInterest': [5.0, 15.0],
            'impliedVolatility': [0.21, 0.19] # IV for puts
        })

        with patch('datetime.date') as mock_date_for_calc:
            mock_date_for_calc.today.return_value = self.mock_today
            mock_date_for_calc.strptime = datetime.datetime.strptime

            results = self.calculator.calculate_gex_for_expiration(spot_price, calls_df, puts_df, exp_date_str)

        self.assertAlmostEqual(results['spot_price_used'], spot_price)
        self.assertEqual(results['risk_free_rate_used'], self.calculator.risk_free_rate)
        self.assertTrue(len(results['gex_by_strike']) > 0)

        # Check structure of one strike item
        first_strike_data = results['gex_by_strike'][0] # Should be strike 95.0 (put only) or 100.0
        found_100_strike = next((item for item in results['gex_by_strike'] if item["strike"] == 100.0), None)
        self.assertIsNotNone(found_100_strike)

        self.assertIn('strike', found_100_strike)
        self.assertIn('call_gex_usd', found_100_strike)
        self.assertIn('put_gex_usd', found_100_strike)
        self.assertIn('net_gex_usd', found_100_strike)

        # Example: Manual check for one option (Call K=100)
        # T = 30/365.25
        T_calc = (30 / 365.25)
        # Gamma for Call K=100, S=102, T=T_calc, r=0.05, sigma=0.2
        gamma_call_100 = self.calculator._black_scholes_gamma(102.0, 100.0, T_calc, 0.2)
        expected_call_gex_100 = 10.0 * 100 * gamma_call_100 * (102.0**2) * 0.01

        self.assertAlmostEqual(found_100_strike['call_gex_usd'], expected_call_gex_100, places=1) # Check with some tolerance

        # Check totals (simple sum check, actual values depend on all calculations)
        calculated_total_net = sum(s['net_gex_usd'] for s in results['gex_by_strike'])
        self.assertAlmostEqual(results['total_net_gex_usd'], calculated_total_net, places=1)


if __name__ == '__main__':
    unittest.main()
