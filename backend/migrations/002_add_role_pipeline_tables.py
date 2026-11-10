#!/usr/bin/env python3
"""Create strategist/tactician/executor pipeline tables and decision role column."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.db.database import engine


def migrate():
    statements = [
        """
        ALTER TABLE decisions
        ADD COLUMN IF NOT EXISTS role VARCHAR(20) DEFAULT 'legacy';
        """,
        """
        UPDATE decisions
        SET role = COALESCE(role, 'legacy');
        """,
        """
        CREATE TABLE IF NOT EXISTS strategist_insights (
            id SERIAL PRIMARY KEY,
            symbol VARCHAR(20) NOT NULL,
            provider VARCHAR(50) NOT NULL,
            model VARCHAR(100),
            generated_at TIMESTAMPTZ DEFAULT NOW(),
            expires_at TIMESTAMPTZ,
            overall_trend VARCHAR(50) NOT NULL,
            next_days_trend VARCHAR(50) NOT NULL,
            next_24h_trend VARCHAR(50) NOT NULL,
            next_few_hours_trend VARCHAR(50) NOT NULL,
            confidence DOUBLE PRECISION,
            rationale TEXT,
            raw_output JSON NOT NULL,
            context_snapshot JSON
        );
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_strategist_insights_symbol ON strategist_insights (symbol);
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_strategist_insights_generated_at ON strategist_insights (generated_at DESC);
        """,
        """
        CREATE TABLE IF NOT EXISTS tactician_plans (
            id SERIAL PRIMARY KEY,
            symbol VARCHAR(20) NOT NULL,
            strategist_insight_id INTEGER NOT NULL REFERENCES strategist_insights(id) ON DELETE CASCADE,
            provider VARCHAR(50) NOT NULL,
            model VARCHAR(100),
            status VARCHAR(20) NOT NULL DEFAULT 'ready',
            confidence DOUBLE PRECISION,
            rationale TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            expires_at TIMESTAMPTZ,
            next_eval_at TIMESTAMPTZ,
            raw_output JSON NOT NULL,
            metadata JSON
        );
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_tactician_plans_symbol_status ON tactician_plans (symbol, status);
        """,
        """
        CREATE TABLE IF NOT EXISTS plan_steps (
            id SERIAL PRIMARY KEY,
            plan_id INTEGER NOT NULL REFERENCES tactician_plans(id) ON DELETE CASCADE,
            sequence_index INTEGER NOT NULL,
            action VARCHAR(20) NOT NULL,
            side VARCHAR(10),
            entry_price DOUBLE PRECISION,
            exit_price DOUBLE PRECISION,
            stop_loss_price DOUBLE PRECISION,
            take_profit_price DOUBLE PRECISION,
            size_usd DOUBLE PRECISION,
            size_pct DOUBLE PRECISION,
            leverage INTEGER,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            created_at TIMESTAMPTZ DEFAULT NOW(),
            executed_at TIMESTAMPTZ,
            metadata JSON
        );
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_plan_steps_plan_seq ON plan_steps (plan_id, sequence_index);
        """,
        """
        CREATE TABLE IF NOT EXISTS plan_execution_logs (
            id SERIAL PRIMARY KEY,
            plan_id INTEGER NOT NULL REFERENCES tactician_plans(id) ON DELETE CASCADE,
            step_id INTEGER REFERENCES plan_steps(id) ON DELETE SET NULL,
            provider VARCHAR(50) NOT NULL,
            model VARCHAR(100),
            status VARCHAR(20) NOT NULL,
            message TEXT,
            payload JSON,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_plan_execution_logs_plan ON plan_execution_logs (plan_id, created_at DESC);
        """,
    ]

    with engine.connect() as conn:
        for stmt in statements:
            conn.execute(stmt)
        conn.commit()
        print("✓ Pipeline tables ready")
if __name__ == "__main__":
    try:
        migrate()
        print("Migration completed successfully!")
    except Exception as exc:
        print(f"Migration failed: {exc}")
        sys.exit(1)
