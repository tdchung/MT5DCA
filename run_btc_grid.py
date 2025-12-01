"""
BTC Grid Strategy Runner
Simple runner script for the BTC Grid Strategy.
"""

import subprocess
import sys
import os

def main():
    """Run the BTC Grid Strategy."""
    
    # Get the path to the main script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    main_script = os.path.join(script_dir, 'src', 'main_btc_grid.py')
    
    print("🟨 Starting BTC Grid Strategy...")
    print(f"📁 Script location: {main_script}")
    print("💡 Use Ctrl+C to stop")
    print("-" * 50)
    
    try:
        # Run the main script
        result = subprocess.run([sys.executable, main_script], 
                              cwd=script_dir,
                              check=True)
        
        print("✅ BTC Grid Strategy completed successfully")
        return result.returncode
        
    except subprocess.CalledProcessError as e:
        print(f"❌ BTC Grid Strategy failed with exit code: {e.returncode}")
        return e.returncode
    except KeyboardInterrupt:
        print("\n📝 BTC Grid Strategy stopped by user")
        return 0
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return 1

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)