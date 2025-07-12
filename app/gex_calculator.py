import math
import datetime
from scipy.stats import norm
import pandas as pd

class GEXCalculator:
    """
    Encapsulates the logic for calculating Gamma Exposure (GEX) from option chain data.
    """
    def __init__(self, risk_free_rate=0.03):
        """
        Initializes the calculator with a given risk-free rate.
        :param risk_free_rate: The risk-free interest rate to use in Black-Scholes.
        """
        self.risk_free_rate = risk_free_rate

    def _calculate_time_to_expiration(self, exp_date_str):
        """Calculates time to expiration in years from a YYYY-MM-DD string."""
        exp_date = datetime.datetime.strptime(exp_date_str, '%Y-%m-%d').date()
        today = datetime.date.today()
        delta = exp_date - today
        # Return a small positive number for same-day expiration to avoid division by zero
        return max(delta.days / 365.25, 1e-6)

    def _black_scholes_gamma(self, S, K, T, sigma):
        """
        Calculates Black-Scholes Gamma for an option.
        S: Spot price
        K: Strike price
        T: Time to expiration (years)
        sigma: Implied volatility
        """
        if sigma == 0 or T == 0:
            return 0.0

        r = self.risk_free_rate
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        gamma = norm.pdf(d1) / (S * sigma * math.sqrt(T))
        return gamma

    def calculate_gex_for_expiration(self, spot_price, calls_df, puts_df, expiration_date_str):
        """
        Calculates GEX for a single expiration date, broken down by strike.

        :param spot_price: Current price of the underlying stock.
        :param calls_df: DataFrame of call options from yfinance.
        :param puts_df: DataFrame of put options from yfinance.
        :param expiration_date_str: The expiration date string (e.g., "2025-01-17").
        :return: A dictionary containing detailed GEX data.
        """
        time_to_expiration_T = self._calculate_time_to_expiration(expiration_date_str)
        gex_data_by_strike = {}

        # Process Calls
        for _, row in calls_df.iterrows():
            strike = float(row['strike'])
            oi = float(row.get('openInterest', 0))
            iv = float(row.get('impliedVolatility', 0))

            if oi == 0 or iv == 0:
                gamma = 0.0
            else:
                gamma = self._black_scholes_gamma(spot_price, strike, time_to_expiration_T, iv)

            # GEX Value ($ per 1% move) = OI * 100 * Gamma * SpotPrice^2 * 0.01
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
                gamma = self._black_scholes_gamma(spot_price, strike, time_to_expiration_T, iv)

            # Put GEX is subtracted from Net GEX
            put_gex_value = oi * 100 * gamma * (spot_price**2) * 0.01

            if strike not in gex_data_by_strike:
                gex_data_by_strike[strike] = {'call_gex': 0.0, 'put_gex': 0.0, 'net_gex': 0.0}
            gex_data_by_strike[strike]['put_gex'] += put_gex_value
            gex_data_by_strike[strike]['net_gex'] -= put_gex_value

        # Prepare results
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

        return {
            "spot_price_used": round(spot_price, 2),
            "risk_free_rate_used": self.risk_free_rate,
            "time_to_expiration_years_used": round(time_to_expiration_T, 4),
            "gex_by_strike": strike_data_list,
            "total_net_gex_usd": round(total_net_gex, 2),
            "total_call_gex_usd": round(total_call_gex, 2),
            "total_put_gex_usd": round(total_put_gex, 2)
        }
