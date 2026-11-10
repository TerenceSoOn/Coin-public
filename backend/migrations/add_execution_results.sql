-- Add execution_results column to decisions table
-- This stores the trade execution results for each decision

ALTER TABLE decisions 
ADD COLUMN IF NOT EXISTS execution_results JSON;

-- Add comment explaining the field
COMMENT ON COLUMN decisions.execution_results IS 'Trade execution results: [{symbol, action, status, order_id, filled_size, avg_price, message}]';
