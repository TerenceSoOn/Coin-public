#!/usr/bin/env python3
"""
Migration script to add execution_results column to decisions table.
"""
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.db.database import engine

def migrate():
    """Add execution_results column to decisions table."""
    sql = """
    ALTER TABLE decisions 
    ADD COLUMN IF NOT EXISTS execution_results JSON;
    """
    
    with engine.connect() as conn:
        conn.execute(sql)
        conn.commit()
        print("✓ Successfully added execution_results column to decisions table")

if __name__ == "__main__":
    try:
        migrate()
        print("Migration completed successfully!")
    except Exception as e:
        print(f"Migration failed: {e}")
        sys.exit(1)
