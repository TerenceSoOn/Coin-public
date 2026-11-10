-- Add role column to decisions table to track which pipeline stage produced the decision
-- Migration: 002_add_role_to_decisions.sql

ALTER TABLE decisions 
ADD COLUMN IF NOT EXISTS role VARCHAR(20) NOT NULL DEFAULT 'legacy';

-- Create index for faster filtering by role
CREATE INDEX IF NOT EXISTS ix_decisions_role ON decisions(role);

-- Update comment
COMMENT ON COLUMN decisions.role IS 'Pipeline role that generated this decision: strategist, tactician, executor, or legacy';
