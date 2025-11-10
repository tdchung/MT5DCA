#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test enhanced blackout logic that allows cycle completion during blackout
"""

def test_enhanced_blackout_logic():
    """Test the enhanced blackout logic for different scenarios"""
    
    blackout_enabled = True
    blackout_start = 2  # 2am GMT+7
    blackout_end = 6    # 6am GMT+7
    blackout_allow_cycle_completion = True
    
    test_scenarios = [
        # (hour, has_positions, has_orders, price, description)
        (1, False, False, 0, "Before blackout - fresh start"),
        (2, False, False, 0, "Blackout starts - no active strategy"),
        (2, True, True, 0, "Blackout starts - has active strategy"),
        (3, True, True, 1.2345, "During blackout - order filled"),
        (3, False, False, 0, "During blackout - strategy completed"),
        (6, False, False, 0, "After blackout - fresh start allowed"),
        (12, True, True, 1.2350, "Normal hours - order filled")
    ]
    
    print("=== Enhanced Blackout Logic Test ===")
    print(f"Blackout: {blackout_start:02d}:00-{blackout_end:02d}:00 GMT+7")
    print(f"Allow cycle completion: {blackout_allow_cycle_completion}")
    print()
    
    for hour, has_positions, has_orders, price, description in test_scenarios:
        print(f"Time {hour:02d}:00 - {description}")
        
        # Blackout detection
        in_blackout = (
            blackout_enabled and (
                (blackout_start <= blackout_end and blackout_start <= hour < blackout_end) or
                (blackout_start > blackout_end and (hour >= blackout_start or hour < blackout_end))
            )
        )
        
        if in_blackout:
            strategy_is_active = has_positions or has_orders
            
            if strategy_is_active and blackout_allow_cycle_completion:
                action = "ALLOW - Continuing existing strategy cycle"
                behavior = "Place new grids to complete current cycle"
                notification = "Blackout active - continuing existing strategy" if price > 0 else "Silent continuation"
            else:
                action = "BLOCK - No active strategy or completion disabled"
                behavior = "Return early, no new grids"
                notification = "New strategy cycles suspended"
        else:
            action = "ALLOW - Normal trading hours"
            behavior = "Place new grids normally"
            notification = "Normal operation"
        
        print(f"  Status: {'RED BLACKOUT' if in_blackout else 'GREEN Normal'}")
        print(f"  Positions: {has_positions}, Orders: {has_orders}, Price: {price}")
        print(f"  Action: {action}")
        print(f"  Behavior: {behavior}")
        print(f"  Notification: {notification}")
        print()
    
    print("=== Expected Behavior Summary ===")
    print()
    print("SCENARIO 1: Strategy starts at 1:50am, blackout begins at 2:00am")
    print("- 1:50am: Place initial grids normally")
    print("- 2:00am: Blackout starts, but strategy has active orders/positions") 
    print("- 2:30am: Order fills -> Continue placing new grids (cycle completion)")
    print("- 3:00am: TP reached -> Strategy cycle complete")
    print("- 3:01am: Try to start new cycle -> BLOCKED (no new cycles in blackout)")
    print("- 6:00am: Blackout ends -> New cycles allowed again")
    print()
    print("SCENARIO 2: No active strategy when blackout starts")
    print("- 2:00am: Blackout starts with no active positions/orders")
    print("- 2:00am onwards: All new grid placement BLOCKED")
    print("- 6:00am: Blackout ends -> Normal operation resumes")

if __name__ == "__main__":
    test_enhanced_blackout_logic()