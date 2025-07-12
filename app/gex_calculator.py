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

    def _calculate_total_net_gex_for_price(self, hypothetical_spot_price, calls_df, puts_df):
        """
        Calculates the total Net GEX for a given hypothetical spot price and aggregated option data.
        Assumes calls_df and puts_df have an 'expirationDate' column.
        """
        total_net_gex = 0.0
        for _, row in calls_df.iterrows():
            oi = float(row.get('openInterest', 0))
            iv = float(row.get('impliedVolatility', 0))
            if oi == 0 or iv == 0: continue
            time_to_expiration_T = self._calculate_time_to_expiration(row['expirationDate'])
            gamma = self._black_scholes_gamma(hypothetical_spot_price, float(row['strike']), time_to_expiration_T, iv)
            total_net_gex += oi * 100 * gamma * (hypothetical_spot_price**2) * 0.01

        for _, row in puts_df.iterrows():
            oi = float(row.get('openInterest', 0))
            iv = float(row.get('impliedVolatility', 0))
            if oi == 0 or iv == 0: continue
            time_to_expiration_T = self._calculate_time_to_expiration(row['expirationDate'])
            gamma = self._black_scholes_gamma(hypothetical_spot_price, float(row['strike']), time_to_expiration_T, iv)
            total_net_gex -= oi * 100 * gamma * (hypothetical_spot_price**2) * 0.01

        return total_net_gex

    def find_zero_gamma_level(self, current_spot_price, calls_df, puts_df,
                              strikes_to_include=None, search_range_percent=0.20, steps=100):
        """
        Finds the approximate stock price where Net GEX is zero for the given aggregated option data.
        """
        if strikes_to_include:
            calls_df = calls_df[calls_df['strike'].isin(strikes_to_include)].copy()
            puts_df = puts_df[puts_df['strike'].isin(strikes_to_include)].copy()

        if calls_df.empty and puts_df.empty:
            return None

        min_price = current_spot_price * (1 - search_range_percent)
        max_price = current_spot_price * (1 + search_range_percent)
        price_step = (max_price - min_price) / steps
        prev_price, prev_gex = None, None

        for i in range(steps + 1):
            check_price = min_price + (i * price_step)
            if check_price <= 0: continue
            current_gex = self._calculate_total_net_gex_for_price(check_price, calls_df, puts_df)
            if prev_gex is not None:
                if (current_gex > 0 and prev_gex < 0) or (current_gex < 0 and prev_gex > 0):
                    zero_gamma_price = prev_price - prev_gex * (check_price - prev_price) / (current_gex - prev_gex)
                    return round(zero_gamma_price, 2)
                if current_gex == 0:
                    return round(check_price, 2)
            prev_price, prev_gex = check_price, current_gex

        print(f"Zero gamma level not found within {search_range_percent*100}% range of {current_spot_price}. Last GEX: {prev_gex:.2f} at price {prev_price:.2f}")
        return None
