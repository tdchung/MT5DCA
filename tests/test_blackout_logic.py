#!/usr/bin/env python3
"""
Test script for FTMO strategy blackout time controls
Tests the 2am-6am GMT+7 blackout period logic
"""

from datetime import datetime, timezone, timedelta

def test_blackout_logic():
    """Test blackout period detection for different hours"""
    
    # Configuration matching FTMO strategy
    blackout_enabled = True
    blackout_start = 2  # 2am GMT+7
    blackout_end = 6    # 6am GMT+7
    
    # Test cases for different hours (GMT+7)
    test_hours = [0, 1, 2, 3, 4, 5, 6, 7, 12, 18, 23]
    
    print("=== FTMO Strategy Blackout Period Test ===")
    print(f"Blackout configured: {blackout_start:02d}:00-{blackout_end:02d}:00 GMT+7")
    print()
    
    for hour in test_hours:
        # Simulate current time at specific hour
        current_hour = hour
        
        # Apply blackout logic (same as in strategy - fixed to exclude end hour)
        in_blackout = (
            blackout_enabled and (
                (blackout_start <= blackout_end and blackout_start <= current_hour < blackout_end) or
                (blackout_start > blackout_end and (current_hour >= blackout_start or current_hour < blackout_end))
            )
        )
        
        status = "🔴 BLACKOUT ACTIVE" if in_blackout else "🟢 NORMAL TRADING"
        action = "New grids suspended, monitoring positions" if in_blackout else "New grid placement allowed"
        
        print(f"Hour {hour:02d}:00 GMT+7 - {status} - {action}")
    
    print()
    print("Expected behavior:")
    print("• 02:00-05:59 GMT+7: Blackout active (no new grids)")
    print("• 06:00-01:59 GMT+7: Normal trading (new grids allowed)")
    print("• Existing positions monitored 24/7 for TP/SL")

if __name__ == "__main__":
    test_blackout_logic()