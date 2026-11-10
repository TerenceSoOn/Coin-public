"""
Test script for OKX trading client in sandbox mode
"""
import sys
sys.path.insert(0, '/Users/terence/Desktop/coin/backend')

from app.okx.trading_client import get_trading_client

def test_okx_sandbox():
    print("Testing OKX Trading Client in Sandbox Mode\n")
    
    # Get client (will use sandbox mode based on config)
    client = get_trading_client()
    
    # Test 1: Get account balance
    print("1. Testing account balance...")
    try:
        balance = client.get_account_balance()
        print(f"   ✓ Balance retrieved: {balance.get('code')} - {balance.get('msg')}")
        
        if balance.get('data'):
            for detail in balance['data']:
                print(f"   Total Equity (USD): {detail.get('totalEq', 'N/A')}")
                print(f"   Details: {len(detail.get('details', []))} currencies")
                break
    except Exception as e:
        print(f"   ✗ Error: {e}")
    
    print()
    
    # Test 2: Place a small test order (will fail in sandbox if no demo balance)
    print("2. Testing market order placement...")
    try:
        # Small BTC order
        result = client.place_spot_order(
            inst_id="BTC-USDT",
            side="buy",
            sz="0.001",  # Very small amount
            ord_type="market"
        )
        print(f"   ✓ Order placed: {result.get('data', [{}])[0].get('ordId', 'N/A')}")
    except Exception as e:
        print(f"   ✗ Order failed (expected in sandbox without demo funds): {e}")
    
    print("\nTest complete!")

if __name__ == "__main__":
    test_okx_sandbox()
