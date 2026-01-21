"""
Database module for importing data from ZIP files using a separate PostgreSQL connection.
"""

import psycopg2
from psycopg2.extras import RealDictCursor
from typing import Dict, Any, List, Optional
import os


class ImportDatabase:
    """Separate database connection specifically for importing data from ZIP files."""
    
    def __init__(self):
        # Separate database configuration for importing data
        self.config = {
            'host': os.getenv('IMPORT_DB_HOST', '136.243.54.157'),
            'port': int(os.getenv('IMPORT_DB_PORT', 25618)),
            'user': os.getenv('IMPORT_DB_USER', 'admin_cold'),
            'password': os.getenv('IMPORT_DB_PASSWORD', 'Wyciek12'),
            'database': os.getenv('IMPORT_DB_NAME', 'cold_search_db')
        }
        self.connection = None
    
    def connect(self):
        """Establish connection to the import database."""
        try:
            self.connection = psycopg2.connect(**self.config)
            return True
        except Exception as e:
            print(f"Error connecting to import database: {e}")
            return False
    
    def disconnect(self):
        """Close the database connection."""
        if self.connection:
            self.connection.close()
            self.connection = None
    
    def execute_query(self, query: str, params: tuple = None) -> List[Dict[str, Any]]:
        """Execute a SELECT query and return results."""
        if not self.connection:
            if not self.connect():
                return []
        
        try:
            with self.connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(query, params)
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            print(f"Error executing query: {e}")
            return []
    
    def execute_non_query(self, query: str, params: tuple = None) -> bool:
        """Execute INSERT/UPDATE/DELETE query."""
        if not self.connection:
            if not self.connect():
                return False
        
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(query, params)
                self.connection.commit()
                return True
        except Exception as e:
            print(f"Error executing non-query: {e}")
            self.connection.rollback()
            return False
    
    def bulk_insert_leaks(self, data_list: List[Dict[str, Any]], batch_size: int = 1000) -> int:
        """Bulk insert leak data into the database."""
        if not self.connection:
            if not self.connect():
                return 0
        
        total_inserted = 0
        
        for i in range(0, len(data_list), batch_size):
            batch = data_list[i:i + batch_size]
            
            try:
                with self.connection.cursor() as cursor:
                    # Prepare the data for bulk insert
                    values_placeholders = ','.join(['%s'] * len(batch))
                    query = """
                        INSERT INTO leaks (data, source) VALUES %s
                        ON CONFLICT (data) DO NOTHING
                    """
                    
                    # Format the data for insertion
                    formatted_batch = [(item['data'], item['source']) for item in batch]
                    
                    cursor.executemany(
                        "INSERT INTO leaks (data, source) VALUES (%s, %s) ON CONFLICT (data) DO NOTHING",
                        formatted_batch
                    )
                    
                    self.connection.commit()
                    total_inserted += cursor.rowcount
                    
            except Exception as e:
                print(f"Error during bulk insert: {e}")
                self.connection.rollback()
                # Try inserting one by one as fallback
                for item in batch:
                    try:
                        with self.connection.cursor() as cursor:
                            cursor.execute(
                                "INSERT INTO leaks (data, source) VALUES (%s, %s) ON CONFLICT (data) DO NOTHING",
                                (item['data'], item['source'])
                            )
                            self.connection.commit()
                            if cursor.rowcount > 0:
                                total_inserted += 1
                    except Exception as e2:
                        print(f"Error inserting single item: {e2}")
                        self.connection.rollback()
        
        return total_inserted


# Create a global instance
import_db = ImportDatabase()