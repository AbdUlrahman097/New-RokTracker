import pandas as pd
from datetime import datetime
from pathlib import Path
import xlsxwriter
from xlsxwriter.workbook import Workbook 
from xlsxwriter.worksheet import Worksheet
from xlsxwriter.chart_area import ChartArea
from xlsxwriter.chart_line import ChartLine
from xlsxwriter.format import Format as XlsxFormat
from xlsxwriter.chartsheet import Chartsheet
from typing import Optional, Union, cast, Any
from .database import HistoricalDatabase
from .analytics import KingdomAnalytics

class AnalyticsExporter:
    def __init__(self, db: HistoricalDatabase, analytics: KingdomAnalytics):
        self.db = db
        self.analytics = analytics

    def export_kingdom_report(self, output_path: str | Path):
        """Exports comprehensive kingdom analytics to Excel with multiple sheets"""
        output_path = Path(output_path)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = output_path / f"kingdom_analytics_{timestamp}.xlsx"

        workbook = xlsxwriter.Workbook(str(filename))
        
        try:
            # Create format styles
            header_format = workbook.add_format({
                'bold': True,
                'align': 'center',
                'valign': 'vcenter',
                'bg_color': '#4472C4',
                'font_color': 'white',
                'border': 1
            })
            
            cell_format = workbook.add_format({
                'align': 'right',
                'border': 1
            })
            
            date_format = workbook.add_format({
                'align': 'center',
                'border': 1,
                'num_format': 'yyyy-mm-dd'
            })

            # Kingdom trends
            df_trends = self.db.get_kingdom_trends(days=30)
            if not df_trends.empty:
                worksheet = workbook.add_worksheet('Kingdom Trends')
                self._write_dataframe(worksheet, df_trends, header_format, cell_format, date_format)
                self._add_trends_chart(workbook, worksheet, df_trends, 'Kingdom Trends Chart')

            # Alliance statistics
            df_alliances = self.db.get_alliance_statistics()
            if not df_alliances.empty:
                worksheet = workbook.add_worksheet('Alliance Statistics')
                self._write_dataframe(worksheet, df_alliances, header_format, cell_format)

            # Kingdom summary
            summary = self.analytics.get_kingdom_summary()
            if summary:
                df_summary = pd.DataFrame([summary])
                worksheet = workbook.add_worksheet('Kingdom Summary')
                self._write_dataframe(worksheet, df_summary, header_format, cell_format)

        finally:
            workbook.close()

        return filename

    def export_governor_report(self, governor_ids: list[str], output_path: str | Path):
        """Exports detailed governor comparison data"""
        output_path = Path(output_path)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = output_path / f"governor_comparison_{timestamp}.xlsx"

        workbook = xlsxwriter.Workbook(str(filename))
        
        try:
            # Create format styles
            header_format = workbook.add_format({
                'bold': True,
                'align': 'center',
                'valign': 'vcenter',
                'bg_color': '#4472C4',
                'font_color': 'white',
                'border': 1
            })
            
            cell_format = workbook.add_format({
                'align': 'right',
                'border': 1
            })
            
            date_format = workbook.add_format({
                'align': 'center',
                'border': 1,
                'num_format': 'yyyy-mm-dd'
            })

            # Historical data for each governor
            all_history = []
            for gov_id in governor_ids:
                history = self.db.get_governor_history(gov_id)
                if not history.empty:
                    all_history.append(history)

            if all_history:
                df_combined = pd.concat(all_history, ignore_index=True)
                worksheet = workbook.add_worksheet('Governor History')
                self._write_dataframe(worksheet, df_combined, header_format, cell_format, date_format)
                self._add_governor_charts(workbook, worksheet, df_combined, 'Governor Trends')

            # Growth predictions
            predictions = []
            for gov_id in governor_ids:
                pred = self.analytics.predict_governor_growth(gov_id)
                if pred:
                    predictions.append(pd.DataFrame(pred))

            if predictions:
                df_predictions = pd.concat(predictions, ignore_index=True)
                worksheet = workbook.add_worksheet('Growth Predictions')
                self._write_dataframe(worksheet, df_predictions, header_format, cell_format, date_format)

        finally:
            workbook.close()

        return filename

    def _write_dataframe(self, worksheet: Worksheet, df: pd.DataFrame, 
                        header_format: XlsxFormat, cell_format: XlsxFormat,
                        date_format: Optional[XlsxFormat] = None) -> None:
        """Write pandas DataFrame to Excel worksheet with formatting"""
        # Write headers
        for col_num, value in enumerate(df.columns.values):
            worksheet.write(0, col_num, value, header_format)
            
        # Write data and set column widths
        for idx, col in enumerate(df.columns):
            # Get max length for column width
            max_length = max(
                df[col].astype(str).apply(len).max(),
                len(str(col))
            ) + 2
            worksheet.set_column(idx, idx, min(max_length, 50))  # Cap width at 50
            
            # Write data with appropriate format
            if 'date' in col.lower() and date_format is not None:
                for row in range(len(df)):
                    worksheet.write(row + 1, idx, df[col].iloc[row], date_format)
            else:
                for row in range(len(df)):
                    worksheet.write(row + 1, idx, df[col].iloc[row], cell_format)

        # Add filters
        worksheet.autofilter(0, 0, len(df), len(df.columns) - 1)
        
        # Freeze the top row
        worksheet.freeze_panes(1, 0)

    def _add_trends_chart(self, workbook: Workbook, worksheet: Worksheet, 
                         df: pd.DataFrame, chart_name: str) -> None:
        """Add kingdom trends chart to a new worksheet"""
        chartsheet = cast(Any, workbook.add_chartsheet(chart_name))
        if chartsheet is None:
            return
            
        chart = cast(ChartLine, workbook.add_chart({'type': 'line'}))
        if chart is None:
            return
        
        # Add data series
        metrics = ['avg_power', 'avg_killpoints', 'total_t4t5_kills']
        colors = ['#4472C4', '#ED7D31', '#A5A5A5']
        
        for idx, (metric, color) in enumerate(zip(metrics, colors)):
            if metric in df.columns:
                chart.add_series({
                    'name': metric,
                    'categories': [worksheet.name, 1, df.columns.get_loc('scan_date'), 
                                len(df), df.columns.get_loc('scan_date')],
                    'values': [worksheet.name, 1, df.columns.get_loc(metric), 
                            len(df), df.columns.get_loc(metric)],
                    'line': {'color': color, 'width': 2}
                })
        
        chart.set_title({'name': 'Kingdom Trends Over Time'})
        chart.set_x_axis({'name': 'Date', 'date_axis': True})
        chart.set_y_axis({'name': 'Values'})
        chart.set_size({'width': 1000, 'height': 600})
        
        if hasattr(chartsheet, 'set_chart'):
            chartsheet.set_chart(chart)

    def _add_governor_charts(self, workbook: Workbook, worksheet: Worksheet, 
                           df: pd.DataFrame, chart_name: str) -> None:
        """Add governor comparison charts to a new worksheet"""
        chartsheet = cast(Any, workbook.add_chartsheet(chart_name))
        if chartsheet is None:
            return
            
        chart = cast(ChartLine, workbook.add_chart({'type': 'line'}))
        if chart is None:
            return
            
        # Add data series for each governor
        colors = ['#4472C4', '#ED7D31', '#A5A5A5', '#FFC000', '#5B9BD5', '#70AD47']
        governors = df['name'].unique()
        
        for idx, (gov, color) in enumerate(zip(governors, colors)):
            gov_data = df[df['name'] == gov]
            if not gov_data.empty:
                chart.add_series({
                    'name': gov,
                    'categories': [worksheet.name, 1, df.columns.get_loc('scan_date'), 
                                len(gov_data), df.columns.get_loc('scan_date')],
                    'values': [worksheet.name, 1, df.columns.get_loc('power'), 
                            len(gov_data), df.columns.get_loc('power')],
                    'line': {'color': color, 'width': 2}
                })
        
        chart.set_title({'name': 'Governor Power Comparison'})
        chart.set_x_axis({'name': 'Date', 'date_axis': True})
        chart.set_y_axis({'name': 'Power'})
        chart.set_size({'width': 1000, 'height': 600})
        
        if hasattr(chartsheet, 'set_chart'):
            chartsheet.set_chart(chart)