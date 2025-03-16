import matplotlib.pyplot as plt
import pandas as pd
import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from datetime import datetime, timedelta
from scipy import stats
from scipy.signal import savgol_filter
from statsmodels.tsa.seasonal import seasonal_decompose
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from sklearn.preprocessing import StandardScaler
from sklearn.covariance import EllipticEnvelope
from .database import HistoricalDatabase

class KingdomAnalytics:
    def __init__(self, db: HistoricalDatabase):
        self.db = db

    def create_power_trend_plot(self):
        df = self.db.get_kingdom_trends(days=30)
        fig = Figure(figsize=(8, 4))
        ax = fig.add_subplot(111)
        
        if len(df) > 0:
            ax.plot(pd.to_datetime(df['scan_date']), df['avg_power'] / 1_000_000, marker='o')
        
        ax.set_title('Average Governor Power Trend')
        ax.set_xlabel('Date')
        ax.set_ylabel('Average Power (Million)')
        ax.grid(True)
        fig.autofmt_xdate()
        
        return FigureCanvasQTAgg(fig)

    def create_killpoints_trend_plot(self):
        df = self.db.get_kingdom_trends(days=30)
        fig = Figure(figsize=(8, 4))
        ax = fig.add_subplot(111)
        
        if len(df) > 0:
            ax.plot(pd.to_datetime(df['scan_date']), df['avg_killpoints'] / 1_000_000, marker='o', color='red')
        
        ax.set_title('Average Kill Points Trend')
        ax.set_xlabel('Date')
        ax.set_ylabel('Average Kill Points (Million)')
        ax.grid(True)
        fig.autofmt_xdate()
        
        return FigureCanvasQTAgg(fig)

    def create_t4t5_kills_trend_plot(self):
        df = self.db.get_kingdom_trends(days=30)
        fig = Figure(figsize=(8, 4))
        ax = fig.add_subplot(111)
        
        if len(df) > 0:
            ax.plot(pd.to_datetime(df['scan_date']), df['total_t4t5_kills'] / 1_000_000, marker='o', color='purple')
        
        ax.set_title('Total T4/T5 Kills Trend')
        ax.set_xlabel('Date')
        ax.set_ylabel('Total T4/T5 Kills (Million)')
        ax.grid(True)
        fig.autofmt_xdate()
        
        return FigureCanvasQTAgg(fig)

    def create_alliance_power_distribution(self):
        df = self.db.get_alliance_statistics()
        fig = Figure(figsize=(10, 5))
        ax = fig.add_subplot(111)
        
        if len(df) > 0:
            # Sort by total power and get top 10
            df = df.nlargest(10, 'total_power')
            
            # Create bar plot
            bars = ax.bar(range(len(df)), df['total_power'] / 1_000_000)
            
            # Add value labels on top of bars
            for bar in bars:
                height = bar.get_height()
                ax.text(bar.get_x() + bar.get_width()/2., height,
                       f'{int(height):,}M',
                       ha='center', va='bottom')
            
            # Set x-axis ticks and labels properly
            ax.set_xticks(range(len(df)))
            ax.set_xticklabels(df['alliance'], rotation=45, ha='right')
        
        ax.set_title('Top 10 Alliances by Total Power')
        ax.set_xlabel('Alliance')
        ax.set_ylabel('Total Power (Million)')
        fig.tight_layout()
        
        return FigureCanvasQTAgg(fig)

    def get_kingdom_summary(self):
        """Returns a dictionary with key kingdom statistics"""
        df_trends = self.db.get_kingdom_trends(days=30)
        df_alliances = self.db.get_alliance_statistics()
        
        if len(df_trends) > 1:  # Need at least 2 data points
            latest = df_trends.iloc[-1]
            first = df_trends.iloc[0]
            
            # Calculate power change with protection against division by zero
            if first['avg_power'] != 0:
                power_change = ((latest['avg_power'] - first['avg_power']) / first['avg_power']) * 100
            else:
                power_change = 100 if latest['avg_power'] > 0 else 0
            
            # Calculate KP change with protection against division by zero
            if first['avg_killpoints'] != 0:
                kp_change = ((latest['avg_killpoints'] - first['avg_killpoints']) / first['avg_killpoints']) * 100
            else:
                kp_change = 100 if latest['avg_killpoints'] > 0 else 0
            
            # Format with absolute values for display
            return {
                'active_governors': int(latest['active_governors']),
                'total_alliances': len(df_alliances),
                'avg_power': f"{latest['avg_power']/1_000_000:.1f}M",
                'power_change': f"{power_change:+.1f}%",
                'avg_killpoints': f"{latest['avg_killpoints']/1_000_000:.1f}M",
                'kp_change': f"{kp_change:+.1f}%",
                'total_t4t5': f"{latest['total_t4t5_kills']/1_000_000:.1f}M"
            }
        return None

    def get_governor_growth_analysis(self, governor_id):
        """Analyzes growth trends for a specific governor"""
        df = self.db.get_governor_history(governor_id)
        if len(df) < 2:
            return None
            
        first = df.iloc[0]
        latest = df.iloc[-1]
        
        power_growth = latest['power'] - first['power']
        kp_growth = latest['killpoints'] - first['killpoints']
        days = (pd.to_datetime(latest['scan_date']) - pd.to_datetime(first['scan_date'])).days
        
        return {
            'name': latest['name'],
            'days_tracked': days,
            'power_growth': f"{power_growth/1_000_000:+.1f}M",
            'daily_power_growth': f"{(power_growth/days)/1_000_000:.2f}M",
            'kp_growth': f"{kp_growth/1_000_000:+.1f}M",
            'daily_kp_growth': f"{(kp_growth/days)/1_000_000:.2f}M",
        }

    def create_governor_comparison_plot(self, governor_ids):
        """Creates a multi-line plot comparing multiple governors' power and kill points over time"""
        fig = Figure(figsize=(12, 6))
        power_ax = fig.add_subplot(121)
        kp_ax = fig.add_subplot(122)
        
        # Use a simple color cycle instead of Set3 colormap
        colors = ['#8dd3c7', '#ffffb3', '#bebada', '#fb8072', '#80b1d3', '#fdb462',
                 '#b3de69', '#fccde5', '#d9d9d9', '#bc80bd', '#ccebc5', '#ffed6f']
        
        for gov_id, color in zip(governor_ids, colors[:len(governor_ids)]):
            df = self.db.get_governor_history(gov_id)
            if len(df) > 0:
                dates = pd.to_datetime(df['scan_date'])
                power_ax.plot(dates, df['power'] / 1_000_000, marker='o', color=color, label=df.iloc[-1]['name'])
                kp_ax.plot(dates, df['killpoints'] / 1_000_000, marker='o', color=color, label=df.iloc[-1]['name'])
        
        power_ax.set_title('Power Comparison')
        power_ax.set_xlabel('Date')
        power_ax.set_ylabel('Power (Million)')
        power_ax.grid(True)
        power_ax.legend()
        
        kp_ax.set_title('Kill Points Comparison')
        kp_ax.set_xlabel('Date')
        kp_ax.set_ylabel('Kill Points (Million)')
        kp_ax.grid(True)
        kp_ax.legend()
        
        fig.autofmt_xdate()
        fig.tight_layout()
        return FigureCanvasQTAgg(fig)

    def predict_governor_growth(self, governor_id, days_to_predict=30):
        """Predicts future power and kill points growth using linear regression"""
        df = self.db.get_governor_history(governor_id)
        if len(df) < 3:  # Need at least 3 points for meaningful prediction
            return None
            
        df['date_num'] = (pd.to_datetime(df['scan_date']) - pd.to_datetime(df['scan_date'].iloc[0])).dt.total_seconds()
        
        # Convert to numpy arrays and ensure float type
        x = df['date_num'].to_numpy(dtype=np.float64)
        y_power = df['power'].to_numpy(dtype=np.float64)
        y_kp = df['killpoints'].to_numpy(dtype=np.float64)
        
        # Perform linear regression with explicit tuple unpacking
        power_slope, power_intercept, power_r, _, _ = np.array(stats.linregress(x, y_power), dtype=np.float64)
        kp_slope, kp_intercept, kp_r, _, _ = np.array(stats.linregress(x, y_kp), dtype=np.float64)
        
        # Generate future dates
        last_date = pd.to_datetime(df['scan_date'].iloc[-1])
        future_dates = pd.date_range(last_date, periods=days_to_predict+1, freq='D')[1:]
        future_seconds = np.array((future_dates - pd.to_datetime(df['scan_date'].iloc[0])).total_seconds(), dtype=np.float64)
        
        # Calculate predictions using numpy operations
        power_pred = power_slope * future_seconds + power_intercept
        kp_pred = kp_slope * future_seconds + kp_intercept
        
        return {
            'dates': future_dates,
            'power_predictions': power_pred,
            'kp_predictions': kp_pred,
            'power_r2': power_r ** 2,
            'kp_r2': kp_r ** 2,
            'daily_power_growth': power_slope * 86400 / 1_000_000,  # slope * seconds_per_day
            'daily_kp_growth': kp_slope * 86400 / 1_000_000  # slope * seconds_per_day
        }

    def create_governor_prediction_plot(self, governor_id, days_to_predict=30):
        """Creates plots showing historical data and future predictions"""
        df = self.db.get_governor_history(governor_id)
        predictions = self.predict_governor_growth(governor_id, days_to_predict)
        
        if predictions is None:
            return None
            
        fig = Figure(figsize=(12, 6))
        power_ax = fig.add_subplot(121)
        kp_ax = fig.add_subplot(122)
        
        # Plot historical data
        dates = pd.to_datetime(df['scan_date'])
        power_ax.plot(dates, df['power'] / 1_000_000, 'o-', label='Historical', color='blue')
        kp_ax.plot(dates, df['killpoints'] / 1_000_000, 'o-', label='Historical', color='blue')
        
        # Plot predictions
        power_ax.plot(predictions['dates'], predictions['power_predictions'] / 1_000_000, '--', 
                     label=f'Predicted (R² = {predictions["power_r2"]:.3f})', color='red')
        kp_ax.plot(predictions['dates'], predictions['kp_predictions'] / 1_000_000, '--',
                  label=f'Predicted (R² = {predictions["kp_r2"]:.3f})', color='red')
        
        power_ax.set_title(f'Power Prediction\nGrowth: {predictions["daily_power_growth"]:.2f}M/day')
        power_ax.set_xlabel('Date')
        power_ax.set_ylabel('Power (Million)')
        power_ax.grid(True)
        power_ax.legend()
        
        kp_ax.set_title(f'Kill Points Prediction\nGrowth: {predictions["daily_kp_growth"]:.2f}M/day')
        kp_ax.set_xlabel('Date')
        kp_ax.set_ylabel('Kill Points (Million)')
        kp_ax.grid(True)
        kp_ax.legend()
        
        fig.autofmt_xdate()
        fig.tight_layout()
        return FigureCanvasQTAgg(fig)

    def compare_governors(self, governor_ids):
        """Returns a detailed comparison of multiple governors"""
        comparisons = []
        
        for gov_id in governor_ids:
            df = self.db.get_governor_history(gov_id)
            if len(df) < 2:
                continue
                
            first = df.iloc[0]
            latest = df.iloc[-1]
            days = (pd.to_datetime(latest['scan_date']) - pd.to_datetime(first['scan_date'])).days
            
            if days == 0:  # Avoid division by zero
                continue
                
            power_growth = latest['power'] - first['power']
            kp_growth = latest['killpoints'] - first['killpoints']
            
            predictions = self.predict_governor_growth(gov_id) if len(df) >= 3 else None
            
            comparisons.append({
                'id': gov_id,
                'name': latest['name'],
                'alliance': latest['alliance'],
                'current_power': latest['power'],
                'power_growth': power_growth,
                'daily_power_growth': power_growth / days,
                'current_kp': latest['killpoints'],
                'kp_growth': kp_growth,
                'daily_kp_growth': kp_growth / days,
                'days_tracked': days,
                'predicted_power_growth': predictions['daily_power_growth'] * 1_000_000 if predictions else None,
                'predicted_kp_growth': predictions['daily_kp_growth'] * 1_000_000 if predictions else None,
                'power_r2': predictions['power_r2'] if predictions else None,
                'kp_r2': predictions['kp_r2'] if predictions else None
            })
            
        return pd.DataFrame(comparisons)

    def _calculate_moving_average(self, series, window=7):
        """Calculate moving average with the specified window"""
        return series.rolling(window=window, min_periods=1).mean()

    def _calculate_exp_smoothing(self, series, seasons=None):
        """Calculate exponential smoothing, optionally with seasonality"""
        if len(series) < 2:
            return series
        
        if seasons:
            model = ExponentialSmoothing(
                series,
                seasonal_periods=seasons,
                trend='add',
                seasonal='add'
            )
        else:
            model = ExponentialSmoothing(
                series,
                trend='add'
            )
        
        return model.fit().fittedvalues

    def _fit_polynomial(self, x, y, degree=2):
        """Fit polynomial regression of specified degree"""
        coeffs = np.polyfit(x, y, degree)
        poly = np.poly1d(coeffs)
        return poly, coeffs

    def _detect_anomalies(self, data, contamination=0.1):
        """Detect anomalies using Elliptic Envelope"""
        scaler = StandardScaler()
        data_scaled = scaler.fit_transform(data.reshape(-1, 1))
        outlier_detector = EllipticEnvelope(contamination=contamination, random_state=42)
        labels = outlier_detector.fit_predict(data_scaled)
        return labels == -1  # True for anomalies

    def analyze_power_trends(self, days=30):
        """Advanced power trend analysis with multiple statistical methods"""
        df = self.db.get_kingdom_trends(days=days)
        if len(df) < 3:
            return None

        dates = pd.to_datetime(df['scan_date'])
        power_values = df['avg_power'].values
        date_nums = (dates - dates.min()).dt.total_seconds()

        # Calculate various trends
        ma_trend = self._calculate_moving_average(df['avg_power'])
        exp_smooth = self._calculate_exp_smoothing(df['avg_power'])
        
        # Polynomial regression
        poly, coeffs = self._fit_polynomial(date_nums, power_values)
        poly_trend = poly(date_nums)

        # Time series decomposition (if enough data points)
        if len(df) >= 14:  # Need reasonable number of points for decomposition
            try:
                decomposition = seasonal_decompose(
                    df['avg_power'], 
                    period=7,  # Weekly seasonality
                    extrapolate_trend=1
                )
                trend = decomposition.trend
                seasonal = decomposition.seasonal
                residual = decomposition.resid
            except:
                trend = None
                seasonal = None
                residual = None
        else:
            trend = None
            seasonal = None
            residual = None

        # Detect anomalies
        anomalies = self._detect_anomalies(power_values)

        return {
            'dates': dates,
            'original': power_values,
            'moving_average': ma_trend,
            'exp_smoothing': exp_smooth,
            'polynomial': poly_trend,
            'poly_coeffs': coeffs,
            'trend': trend,
            'seasonal': seasonal,
            'residual': residual,
            'anomalies': anomalies,
            'anomaly_dates': dates[anomalies],
            'anomaly_values': power_values[anomalies]
        }

    def analyze_killpoints_trends(self, days=30):
        """Advanced kill points trend analysis with multiple statistical methods"""
        df = self.db.get_kingdom_trends(days=days)
        if len(df) < 3:
            return None

        dates = pd.to_datetime(df['scan_date'])
        kp_values = df['avg_killpoints'].values
        date_nums = (dates - dates.min()).dt.total_seconds()

        # Calculate various trends
        ma_trend = self._calculate_moving_average(df['avg_killpoints'])
        exp_smooth = self._calculate_exp_smoothing(df['avg_killpoints'])
        
        # Polynomial regression
        poly, coeffs = self._fit_polynomial(date_nums, kp_values)
        poly_trend = poly(date_nums)

        # Time series decomposition (if enough data points)
        if len(df) >= 14:
            try:
                decomposition = seasonal_decompose(
                    df['avg_killpoints'], 
                    period=7,  # Weekly seasonality
                    extrapolate_trend=1  # Use 1 period for extrapolation
                )
                trend = decomposition.trend
                seasonal = decomposition.seasonal
                residual = decomposition.resid
            except:
                trend = None
                seasonal = None
                residual = None
        else:
            trend = None
            seasonal = None
            residual = None

        # Detect anomalies
        anomalies = self._detect_anomalies(kp_values)

        return {
            'dates': dates,
            'original': kp_values,
            'moving_average': ma_trend,
            'exp_smoothing': exp_smooth,
            'polynomial': poly_trend,
            'poly_coeffs': coeffs,
            'trend': trend,
            'seasonal': seasonal,
            'residual': residual,
            'anomalies': anomalies,
            'anomaly_dates': dates[anomalies],
            'anomaly_values': kp_values[anomalies]
        }

    def create_advanced_power_trend_plot(self):
        """Creates an enhanced power trend plot with multiple statistical indicators"""
        analysis = self.analyze_power_trends()
        if analysis is None:
            return None

        fig = Figure(figsize=(12, 8))
        
        # Main trend plot
        ax1 = fig.add_subplot(211)
        ax1.plot(analysis['dates'], analysis['original'] / 1_000_000, 'o-', label='Original', alpha=0.5)
        ax1.plot(analysis['dates'], analysis['moving_average'] / 1_000_000, 'r-', label='Moving Average', linewidth=2)
        ax1.plot(analysis['dates'], analysis['exp_smoothing'] / 1_000_000, 'g-', label='Exp Smoothing', linewidth=2)
        ax1.plot(analysis['dates'], analysis['polynomial'] / 1_000_000, 'b--', label='Polynomial Trend', linewidth=2)
        
        # Plot anomalies if any found
        if len(analysis['anomaly_dates']) > 0:
            ax1.scatter(analysis['anomaly_dates'], 
                       analysis['anomaly_values'] / 1_000_000,
                       color='red', marker='x', s=100, label='Anomalies')

        ax1.set_title('Power Trends Analysis')
        ax1.set_xlabel('Date')
        ax1.set_ylabel('Power (Million)')
        ax1.grid(True)
        ax1.legend()

        # Decomposition plot if available
        if analysis['trend'] is not None:
            ax2 = fig.add_subplot(212)
            ax2.plot(analysis['dates'], analysis['trend'] / 1_000_000, 'b-', label='Trend')
            if analysis['seasonal'] is not None:
                ax2.plot(analysis['dates'], analysis['seasonal'] / 1_000_000, 'g-', label='Seasonal')
            if analysis['residual'] is not None:
                ax2.plot(analysis['dates'], analysis['residual'] / 1_000_000, 'r-', label='Residual')
            
            ax2.set_title('Time Series Decomposition')
            ax2.set_xlabel('Date')
            ax2.set_ylabel('Components (Million)')
            ax2.grid(True)
            ax2.legend()

        fig.autofmt_xdate()
        fig.tight_layout()
        return FigureCanvasQTAgg(fig)

    def create_advanced_killpoints_trend_plot(self):
        """Creates an enhanced kill points trend plot with multiple statistical indicators"""
        analysis = self.analyze_killpoints_trends()
        if analysis is None:
            return None

        fig = Figure(figsize=(12, 8))
        
        # Main trend plot
        ax1 = fig.add_subplot(211)
        ax1.plot(analysis['dates'], analysis['original'] / 1_000_000, 'o-', label='Original', alpha=0.5)
        ax1.plot(analysis['dates'], analysis['moving_average'] / 1_000_000, 'r-', label='Moving Average', linewidth=2)
        ax1.plot(analysis['dates'], analysis['exp_smoothing'] / 1_000_000, 'g-', label='Exp Smoothing', linewidth=2)
        ax1.plot(analysis['dates'], analysis['polynomial'] / 1_000_000, 'b--', label='Polynomial Trend', linewidth=2)
        
        # Plot anomalies if any found
        if len(analysis['anomaly_dates']) > 0:
            ax1.scatter(analysis['anomaly_dates'], 
                       analysis['anomaly_values'] / 1_000_000,
                       color='red', marker='x', s=100, label='Anomalies')

        ax1.set_title('Kill Points Trends Analysis')
        ax1.set_xlabel('Date')
        ax1.set_ylabel('Kill Points (Million)')
        ax1.grid(True)
        ax1.legend()

        # Decomposition plot if available
        if analysis['trend'] is not None:
            ax2 = fig.add_subplot(212)
            ax2.plot(analysis['dates'], analysis['trend'] / 1_000_000, 'b-', label='Trend')
            if analysis['seasonal'] is not None:
                ax2.plot(analysis['dates'], analysis['seasonal'] / 1_000_000, 'g-', label='Seasonal')
            if analysis['residual'] is not None:
                ax2.plot(analysis['dates'], analysis['residual'] / 1_000_000, 'r-', label='Residual')
            
            ax2.set_title('Time Series Decomposition')
            ax2.set_xlabel('Date')
            ax2.set_ylabel('Components (Million)')
            ax2.grid(True)
            ax2.legend()

        fig.autofmt_xdate()
        fig.tight_layout()
        return FigureCanvasQTAgg(fig)