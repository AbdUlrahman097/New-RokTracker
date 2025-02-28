import sqlite3
import pandas as pd
import json
from datetime import datetime
from pathlib import Path

class HistoricalDatabase:
    def __init__(self, db_path="historical_data.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scans (
                    scan_id TEXT PRIMARY KEY,
                    scan_date TIMESTAMP,
                    scan_name TEXT,
                    total_governors INTEGER
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS governor_data (
                    scan_id TEXT,
                    governor_id TEXT,
                    name TEXT,
                    power INTEGER,
                    killpoints INTEGER,
                    t1_kills INTEGER,
                    t2_kills INTEGER,
                    t3_kills INTEGER,
                    t4_kills INTEGER,
                    t5_kills INTEGER,
                    dead INTEGER,
                    alliance TEXT,
                    FOREIGN KEY(scan_id) REFERENCES scans(scan_id),
                    UNIQUE(scan_id, governor_id)
                )
            """)
            conn.commit()

    def save_scan_data(self, scan_id, scan_name, governors_data):
        """Save scan data with proper handling of UNIQUE constraint"""
        with sqlite3.connect(self.db_path) as conn:
            try:
                # First try to insert scan metadata
                conn.execute(
                    "INSERT INTO scans (scan_id, scan_date, scan_name, total_governors) VALUES (?, ?, ?, ?)",
                    (scan_id, datetime.now(), scan_name, len(governors_data))
                )
                
                # Save governor data
                gov_data = []
                for gov in governors_data:
                    gov_data.append((
                        scan_id, gov.id, gov.name, gov.power, gov.killpoints,
                        gov.t1_kills, gov.t2_kills, gov.t3_kills, gov.t4_kills,
                        gov.t5_kills, gov.dead, gov.alliance
                    ))
                
                conn.executemany("""
                    INSERT INTO governor_data 
                    (scan_id, governor_id, name, power, killpoints, t1_kills, t2_kills, 
                     t3_kills, t4_kills, t5_kills, dead, alliance)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, gov_data)
                
                conn.commit()
            
            except sqlite3.IntegrityError as e:
                if "UNIQUE constraint failed: scans.scan_id" in str(e):
                    # If scan_id already exists, update the existing scan
                    conn.execute("""
                        UPDATE scans 
                        SET scan_date = ?, scan_name = ?, total_governors = total_governors + ?
                        WHERE scan_id = ?
                    """, (datetime.now(), scan_name, len(governors_data), scan_id))
                    
                    # Insert new governor data (existing governors will be preserved)
                    for gov in governors_data:
                        try:
                            conn.execute("""
                                INSERT INTO governor_data 
                                (scan_id, governor_id, name, power, killpoints, t1_kills, t2_kills, 
                                 t3_kills, t4_kills, t5_kills, dead, alliance)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (
                                scan_id, gov.id, gov.name, gov.power, gov.killpoints,
                                gov.t1_kills, gov.t2_kills, gov.t3_kills, gov.t4_kills,
                                gov.t5_kills, gov.dead, gov.alliance
                            ))
                        except sqlite3.IntegrityError:
                            # Skip duplicate governor entries
                            continue
                    
                    conn.commit()
                else:
                    raise

    def get_governor_history(self, governor_id):
        query = """
            SELECT s.scan_date, g.*
            FROM governor_data g
            JOIN scans s ON g.scan_id = s.scan_id
            WHERE g.governor_id = ?
            ORDER BY s.scan_date
        """
        df = pd.read_sql_query(query, sqlite3.connect(self.db_path), params=(governor_id,))
        # Ensure numeric columns have correct dtypes
        numeric_columns = ['power', 'killpoints', 't1_kills', 't2_kills', 
                         't3_kills', 't4_kills', 't5_kills', 'dead']
        for col in numeric_columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        return df

    def get_kingdom_trends(self, days=30):
        query = """
            SELECT 
                s.scan_date,
                CAST(AVG(g.power) as INTEGER) as avg_power,
                CAST(AVG(g.killpoints) as INTEGER) as avg_killpoints,
                COUNT(*) as active_governors,
                CAST(SUM(g.t4_kills + g.t5_kills) as INTEGER) as total_t4t5_kills
            FROM governor_data g
            JOIN scans s ON g.scan_id = s.scan_id
            WHERE s.scan_date >= date('now', ?)
            GROUP BY s.scan_id
            ORDER BY s.scan_date
        """
        df = pd.read_sql_query(query, sqlite3.connect(self.db_path), params=(f'-{days} days',))
        # Ensure numeric columns have correct dtypes
        numeric_columns = ['avg_power', 'avg_killpoints', 'active_governors', 'total_t4t5_kills']
        for col in numeric_columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        return df

    def get_top_governors(self, metric='power', limit=10):
        query = """
            WITH latest_scan AS (
                SELECT scan_id FROM scans 
                ORDER BY scan_date DESC LIMIT 1
            )
            SELECT g.governor_id, g.name, g.power, g.killpoints, g.alliance,
                   g.t4_kills, g.t5_kills, g.dead
            FROM governor_data g
            JOIN latest_scan ls ON g.scan_id = ls.scan_id
            ORDER BY g.{} DESC
            LIMIT ?
        """.format(metric)
        df = pd.read_sql_query(query, sqlite3.connect(self.db_path), params=(limit,))
        # Ensure numeric columns have correct dtypes
        numeric_columns = ['power', 'killpoints', 't4_kills', 't5_kills', 'dead']
        for col in numeric_columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        return df

    def get_alliance_statistics(self):
        query = """
            WITH latest_scan AS (
                SELECT scan_id FROM scans 
                ORDER BY scan_date DESC LIMIT 1
            )
            SELECT 
                g.alliance,
                COUNT(*) as members,
                CAST(AVG(g.power) as INTEGER) as avg_power,
                CAST(SUM(g.power) as INTEGER) as total_power,
                CAST(AVG(g.killpoints) as INTEGER) as avg_killpoints,
                CAST(SUM(g.t4_kills + g.t5_kills) as INTEGER) as total_t4t5_kills
            FROM governor_data g
            JOIN latest_scan ls ON g.scan_id = ls.scan_id
            WHERE g.alliance IS NOT NULL
            GROUP BY g.alliance
            ORDER BY total_power DESC
        """
        df = pd.read_sql_query(query, sqlite3.connect(self.db_path))
        # Ensure numeric columns have correct dtypes
        numeric_columns = ['members', 'avg_power', 'total_power', 'avg_killpoints', 'total_t4t5_kills']
        for col in numeric_columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        return df