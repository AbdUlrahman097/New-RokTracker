import matplotlib.pyplot as plt
import pandas as pd
import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from datetime import datetime, timedelta
from scipy import stats
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
            bars = ax.bar(df['alliance'], df['total_power'] / 1_000_000)
            
            # Add value labels on top of bars
            for bar in bars:
                height = bar.get_height()
                ax.text(bar.get_x() + bar.get_width()/2., height,
                       f'{int(height):,}M',
                       ha='center', va='bottom')
                       
            # Rotate x-axis labels for better readability
            ax.set_xticklabels(ax.get_xticklabels(), rotation=45, ha='right')
        
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
        power_ax.tick_params(axis='x', rotation=45)
        
        kp_ax.set_title('Kill Points Comparison')
        kp_ax.set_xlabel('Date')
        kp_ax.set_ylabel('Kill Points (Million)')
        kp_ax.grid(True)
        kp_ax.legend()
        kp_ax.tick_params(axis='x', rotation=45)
        
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
        power_ax.tick_params(axis='x', rotation=45)
        
        kp_ax.set_title(f'Kill Points Prediction\nGrowth: {predictions["daily_kp_growth"]:.2f}M/day')
        kp_ax.set_xlabel('Date')
        kp_ax.set_ylabel('Kill Points (Million)')
        kp_ax.grid(True)
        kp_ax.legend()
        kp_ax.tick_params(axis='x', rotation=45)
        
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