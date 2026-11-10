#!/usr/bin/env python3
"""
Migration: Add role column to decisions table

This migration adds the 'role' column to track which pipeline stage
(strategist, tactician, executor) produced each decision.
"""

import sys
import os
from pathlib import Path

# Add parent directory to path to import from backend
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.app.db.database import SessionLocal, engine
from sqlalchemy import text


def migrate():
    """Apply the migration"""
    print("Running migration: Add role column to decisions table")
    
    db = SessionLocal()
    try:
        # Add role column with default value 'legacy'
        db.execute(text("""
            ALTER TABLE decisions 
            ADD COLUMN IF NOT EXISTS role VARCHAR(20) NOT NULL DEFAULT 'legacy'
        """))
        
        # Create index for faster filtering by role
        db.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_decisions_role ON decisions(role)
        """))
        
        # Add comment
        db.execute(text("""
            COMMENT ON COLUMN decisions.role IS 
            'Pipeline role that generated this decision: strategist, tactician, executor, or legacy'
        """))
        
        db.commit()
        print("✅ Migration completed successfully")
        print("   - Added 'role' column to decisions table")
        print("   - Created index on 'role' column")
        
    except Exception as e:
        db.rollback()
        print(f"❌ Migration failed: {e}")
        raise
    finally:
        db.close()


def rollback():
    """Rollback the migration"""
    print("Rolling back migration: Remove role column from decisions table")
    
    db = SessionLocal()
    try:
        # Drop index
        db.execute(text("""
            DROP INDEX IF EXISTS ix_decisions_role
        """))
        
        # Remove column
        db.execute(text("""
            ALTER TABLE decisions DROP COLUMN IF EXISTS role
        """))
        
        db.commit()
        print("✅ Rollback completed successfully")
        
    except Exception as e:
        db.rollback()
        print(f"❌ Rollback failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "rollback":
        rollback()
    else:
        migrate()
