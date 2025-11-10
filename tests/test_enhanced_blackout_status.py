#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test the enhanced blackout status display for FTMO strategy
Tests different times and status scenarios
"""

from datetime import datetime, timezone, timedelta

def test_blackout_status_display():
    """Test blackout status display logic for different times"""
    
    # Configuration
    blackout_enabled = True
    blackout_start = 2  # 2am GMT+7
    blackout_end = 6    # 6am GMT+7
    
    # Test cases with different hours
    test_scenarios = [
        (1, "Just before blackout starts"),
        (2, "Blackout just started"),
        (3, "Middle of blackout"),
        (5, "Near end of blackout"),
        (6, "Just after blackout ends"),
        (12, "Midday - far from blackout"),
        (23, "Late evening - approaching next blackout")
    ]
    
    print("=== Enhanced Blackout Status Display Test ===")
    print(f"Blackout window: {blackout_start:02d}:00-{blackout_end:02d}:00 GMT+7\n")
    
    for current_hour, description in test_scenarios:
        print(f"Clock {description} (Hour {current_hour:02d}:00)")
        
        # Blackout detection logic
        currently_in_blackout = (
            blackout_enabled and (
                (blackout_start <= blackout_end and blackout_start <= current_hour < blackout_end) or
                (blackout_start > blackout_end and (current_hour >= blackout_start or current_hour < blackout_end))
            )
        )
        
        # Status display logic
        if currently_in_blackout:
            if current_hour < blackout_end or (blackout_start > blackout_end and current_hour < blackout_end):
                hours_left = (blackout_end - current_hour) if current_hour < blackout_end else (24 - current_hour + blackout_end)
            else:
                hours_left = blackout_end - current_hour
            status = f"RED ACTIVE - {hours_left}h left (ends {blackout_end:02d}:00)"
            action = "New grids SUSPENDED, monitoring positions"
        else:
            if current_hour < blackout_start:
                hours_until = blackout_start - current_hour
            else:
                hours_until = 24 - current_hour + blackout_start
            status = f"GREEN inactive - starts in {hours_until}h (at {blackout_start:02d}:00)"
            action = "Normal grid placement active"
        
        print(f"   Status: {status}")
        print(f"   Effect: {action}")
        print()
    
    print("=== Telegram Status Message Format ===")
    
    # Simulate current time during blackout (3am)
    current_hour = 3
    currently_in_blackout = True
    hours_left = 3
    
    print("Sample /blackout command response during active period:")
    print("-" * 50)
    print("Risk Management Blackout")
    print("")
    print("* Status: RED ACTIVE")
    print("* Current time: 03:25 GMT+7")
    print("* Window: 02:00-06:00 GMT+7 daily")
    print("* Ends in ~3h (at 06:00)")
    print("")
    print("Current Effect:")
    print("New grid placement SUSPENDED")
    print("Monitoring existing positions for TP/SL")
    print("")
    print("Controls:")
    print("* /blackout off - Disable")
    print("* /blackout HH-HH - Set window")

if __name__ == "__main__":
    test_blackout_status_display()