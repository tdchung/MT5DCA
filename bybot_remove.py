import requests
import csv
import os
from typing import List
import time
########################################################################################
def dex_token_info_is_ok(token):
    res = True
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token}"
        data = requests.get(url=url, timeout=10)
        data = data.json()
        print(f"DEBUG :: {token} :: {data}")
        if data and 'pairs' in data:
            if data['pairs'] is None:
                res = False
    except Exception as e:
        print(f"ERROR :: dex :: {e}")
    return res


def read_buybot_data(csv_file: str = "buybot_data.csv") -> List[dict]:
    """
    Read data from buybot_data.csv file.
    
    Args:
        csv_file: Path to the CSV file
        
    Returns:
        List of dictionaries with channel_id, token, and chain data
    """
    data = []
    try:
        if not os.path.exists(csv_file):
            print(f"❌ CSV file not found: {csv_file}")
            return data
            
        with open(csv_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                data.append({
                    'channel_id': row.get('channel_id', ''),
                    'token': row.get('token', ''),
                    'chain': row.get('chain', '')
                })
        print(f"✅ Loaded {len(data)} records from {csv_file}")
        
    except Exception as e:
        print(f"❌ Error reading CSV file: {e}")
        
    return data


def process_tokens_with_realtime_removal(data: List[dict], batch_size: int = 10, api_url: str = "http://52.74.240.48:8001/api/remove_tokens"):
    """
    Process tokens with real-time removal: check -> accumulate -> send removal when batch full -> continue.
    
    Args:
        data: List of token data dictionaries
        batch_size: Number of invalid tokens to accumulate before sending removal request
        api_url: API endpoint URL for removal
    """
    total_tokens = len(data)
    tokens_to_remove = []
    successful_batches = 0
    failed_batches = 0
    total_invalid_tokens = 0
    
    print(f"🔍 Processing {total_tokens} tokens with real-time removal (batch size: {batch_size})...")
    
    for i, record in enumerate(data, 1):
        token = record.get('token', '')
        chain = record.get('chain', '')
        
        if not token:
            print(f"⚠️ [{i}/{total_tokens}] Skipping empty token")
            continue
            
        print(f"🔍 [{i}/{total_tokens}] Checking token: {token} (chain: {chain})")
        
        # Use dex_token_info_is_ok to validate token
        is_valid = False
        try:
            is_valid = dex_token_info_is_ok(token)
            
            if not is_valid:  # res = False, token should be removed
                tokens_to_remove.append(token)
                total_invalid_tokens += 1
                print(f"🗑️ [{i}/{total_tokens}] Invalid token added to batch: {token} (no pairs found) - Batch: {len(tokens_to_remove)}/{batch_size}")
            else:
                print(f"✅ [{i}/{total_tokens}] Token valid: {token}")
                
        except Exception as e:
            print(f"❌ [{i}/{total_tokens}] Error checking token {token}: {e}")
            # On error, consider token invalid and add to removal list
            tokens_to_remove.append(token)
            total_invalid_tokens += 1
            print(f"🗑️ [{i}/{total_tokens}] Invalid token added to batch: {token} (validation error) - Batch: {len(tokens_to_remove)}/{batch_size}")
        
        # Send removal request when batch is full
        if len(tokens_to_remove) >= batch_size:
            batch_num = (total_invalid_tokens // batch_size)
            print(f"\n📦 Batch {batch_num} full! Sending removal request for {len(tokens_to_remove)} tokens:")
            for j, remove_token in enumerate(tokens_to_remove, 1):
                print(f"   {j}. {remove_token}")
            
            # Send removal request
            if send_removal_request(tokens_to_remove, api_url):
                successful_batches += 1
                print(f"✅ Batch {batch_num} sent successfully!")
            else:
                failed_batches += 1
                print(f"❌ Batch {batch_num} failed!")
            
            # Clear the batch and continue
            tokens_to_remove = []
            time.sleep(2)  # Brief pause between batch requests
        
        # Brief pause to avoid rate limiting DexScreener API
        time.sleep(0.5)
    
    # Send remaining tokens if any
    if tokens_to_remove:
        final_batch_num = successful_batches + failed_batches + 1
        print(f"\n� Final batch {final_batch_num} ({len(tokens_to_remove)} tokens):")
        for j, remove_token in enumerate(tokens_to_remove, 1):
            print(f"   {j}. {remove_token}")
        
        if send_removal_request(tokens_to_remove, api_url):
            successful_batches += 1
            print(f"✅ Final batch {final_batch_num} sent successfully!")
        else:
            failed_batches += 1
            print(f"❌ Final batch {final_batch_num} failed!")
    
    # Summary
    total_batches = successful_batches + failed_batches
    print(f"\n📊 REAL-TIME PROCESSING SUMMARY:")
    print(f"• Total tokens processed: {total_tokens}")
    print(f"• Invalid tokens found: {total_invalid_tokens}")
    print(f"• Valid tokens: {total_tokens - total_invalid_tokens}")
    print(f"• Removal batches sent: {total_batches}")
    print(f"• Successful batches: {successful_batches}")
    print(f"• Failed batches: {failed_batches}")
    print(f"• Success rate: {(successful_batches/total_batches*100):.1f}%" if total_batches > 0 else "• Success rate: N/A")


def send_removal_request(tokens: List[str], api_url: str = "http://52.74.240.48:8001/api/remove_tokens") -> bool:
    """
    Send removal request for a batch of tokens.
    
    Args:
        tokens: List of token addresses to remove
        api_url: API endpoint URL
        
    Returns:
        True if request was successful, False otherwise
    """
    try:
        # Create comma-separated token list
        token_list = ','.join(tokens)
        url = f"{api_url}?tokens={token_list}"
        
        print(f"🚀 Sending removal request for {len(tokens)} tokens...")
        print(f"🔗 URL: {url}")
        
        response = requests.get(url, timeout=30)
        
        if response.status_code == 200:
            print(f"✅ Removal request successful! Response: {response.text}")
            return True
        else:
            print(f"❌ Removal request failed! Status: {response.status_code}, Response: {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Error sending removal request: {e}")
        return False


def process_token_removal(csv_file: str = "buybot_data.csv", batch_size: int = 10):
    """
    Main function to process token removal from CSV data with real-time processing.
    
    Args:
        csv_file: Path to the CSV file
        batch_size: Number of tokens to send per removal request
    """
    print("🎯 Starting real-time token removal process...")
    
    # Read CSV data
    data = read_buybot_data(csv_file)
    if not data:
        print("❌ No data found. Exiting...")
        return
    
    # Process tokens with real-time removal (check -> accumulate -> send when batch full)
    process_tokens_with_realtime_removal(data, batch_size)


def test_sample_tokens(csv_file: str = "buybot_data.csv", sample_size: int = 10):
    """
    Test the validation logic with a small sample of tokens (no removal requests).
    
    Args:
        csv_file: Path to the CSV file
        sample_size: Number of tokens to test
    """
    print(f"🧪 Testing with sample of {sample_size} tokens...")
    
    data = read_buybot_data(csv_file)
    if not data:
        return
    
    # Take only first N tokens as sample
    sample_data = data[:sample_size]
    print(f"📊 Testing {len(sample_data)} tokens from CSV")
    
    tokens_to_remove = []
    valid_tokens = []
    
    for i, record in enumerate(sample_data, 1):
        token = record.get('token', '')
        chain = record.get('chain', '')
        
        if not token:
            print(f"⚠️ [{i}/{sample_size}] Skipping empty token")
            continue
            
        print(f"🔍 [{i}/{sample_size}] Testing token: {token} (chain: {chain})")
        
        try:
            is_valid = dex_token_info_is_ok(token)
            
            if not is_valid:
                tokens_to_remove.append(token)
                print(f"🗑️ [{i}/{sample_size}] Invalid: {token} (no pairs found)")
            else:
                valid_tokens.append(token)
                print(f"✅ [{i}/{sample_size}] Valid: {token}")
                
        except Exception as e:
            print(f"❌ [{i}/{sample_size}] Error checking token {token}: {e}")
            tokens_to_remove.append(token)
        
        time.sleep(0.5)
    
    print(f"\n📊 SAMPLE TEST RESULTS:")
    print(f"• Tokens tested: {len(sample_data)}")
    print(f"• Invalid tokens: {len(tokens_to_remove)}")
    print(f"• Valid tokens: {len(valid_tokens)}")
    
    if tokens_to_remove:
        print(f"\n🗑️ Invalid tokens found:")
        for i, token in enumerate(tokens_to_remove, 1):
            print(f"   {i}. {token}")
    else:
        print(f"\n✅ All tested tokens are valid!")


def demo_realtime_processing(csv_file: str = "buybot_data.csv", batch_size: int = 5, sample_size: int = 20):
    """
    Demo the real-time processing logic without sending actual removal requests.
    Shows how the system checks tokens and sends removal requests when batch is full.
    
    Args:
        csv_file: Path to the CSV file
        batch_size: Number of invalid tokens to accumulate before "sending" removal request  
        sample_size: Number of tokens to process in demo
    """
    print(f"🎬 DEMO: Real-time processing with {sample_size} tokens (batch size: {batch_size})")
    print("💡 This demo simulates removal requests without sending them to the API\n")
    
    data = read_buybot_data(csv_file)
    if not data:
        return
    
    # Take sample for demo
    sample_data = data[:sample_size]
    tokens_to_remove = []
    batch_count = 0
    total_invalid_tokens = 0
    
    for i, record in enumerate(sample_data, 1):
        token = record.get('token', '')
        chain = record.get('chain', '')
        
        if not token:
            print(f"⚠️ [{i}/{sample_size}] Skipping empty token")
            continue
            
        print(f"🔍 [{i}/{sample_size}] Checking token: {token[:20]}... (chain: {chain})")
        
        try:
            is_valid = dex_token_info_is_ok(token)
            
            if not is_valid:
                tokens_to_remove.append(token)
                total_invalid_tokens += 1
                print(f"🗑️ [{i}/{sample_size}] Invalid token added to batch: {token[:20]}... - Batch: {len(tokens_to_remove)}/{batch_size}")
            else:
                print(f"✅ [{i}/{sample_size}] Token valid: {token[:20]}...")
                
        except Exception as e:
            tokens_to_remove.append(token)
            total_invalid_tokens += 1
            print(f"🗑️ [{i}/{sample_size}] Invalid token added to batch: {token[:20]}... (error) - Batch: {len(tokens_to_remove)}/{batch_size}")
        
        # Simulate sending removal request when batch is full
        if len(tokens_to_remove) >= batch_size:
            batch_count += 1
            print(f"\n📦 DEMO BATCH {batch_count} READY! Would send removal request for:")
            for j, remove_token in enumerate(tokens_to_remove, 1):
                print(f"   {j}. {remove_token}")
            print(f"🔗 Demo URL: http://52.74.240.48:8001/api/remove_tokens?tokens={','.join(tokens_to_remove[:3])}...")
            print("✅ DEMO: Batch would be sent successfully!")
            
            # Clear batch and continue
            tokens_to_remove = []
            time.sleep(1)  # Demo pause
        
        time.sleep(0.2)  # Faster for demo
    
    # Handle remaining tokens
    if tokens_to_remove:
        batch_count += 1
        print(f"\n📦 DEMO FINAL BATCH {batch_count} ({len(tokens_to_remove)} tokens):")
        for j, remove_token in enumerate(tokens_to_remove, 1):
            print(f"   {j}. {remove_token}")
        print("✅ DEMO: Final batch would be sent successfully!")
    
    print(f"\n📊 DEMO SUMMARY:")
    print(f"• Tokens processed: {sample_size}")
    print(f"• Invalid tokens found: {total_invalid_tokens}")
    print(f"• Batches that would be sent: {batch_count}")
    print(f"💡 In live mode, each batch triggers an immediate API removal request!")


if __name__ == "__main__":
    # Configuration
    CSV_FILE = "buybot_data.csv"
    BATCH_SIZE = 10  # Send 10 tokens per removal request
    API_URL = "http://52.74.240.48:8001/api/remove_tokens"
    
    print("🤖 BUYBOT TOKEN REMOVAL TOOL")
    print("=" * 50)
    print(f"📄 CSV File: {CSV_FILE}")
    print(f"📦 Batch Size: {BATCH_SIZE} tokens per request")
    print(f"🔗 API URL: {API_URL}")
    print("🎯 Target: Remove invalid tokens (no DexScreener pairs)")
    print("=" * 50)
    
    # Ask user for mode
    print("\nSelect mode:")
    print("1. Test with sample (10 tokens)")
    print("2. Demo real-time processing (20 tokens, no actual API calls)")
    print("3. Process all tokens (LIVE - sends real removal requests)")
    choice = input("Enter choice (1, 2, or 3): ").strip()
    
    try:
        if choice == "1":
            test_sample_tokens(CSV_FILE, 10)
        elif choice == "2":
            # Demo real-time processing without actual API calls
            demo_realtime_processing(CSV_FILE, 5, 20)  # batch_size=5, sample_size=20
        elif choice == "3":
            confirm = input("\n⚠️  This will check ALL tokens and send REAL removal requests. Continue? (yes/no): ").strip().lower()
            if confirm in ['yes', 'y']:
                process_token_removal(CSV_FILE, BATCH_SIZE)
                print("\n🎉 Token removal process completed!")
            else:
                print("❌ Process cancelled by user")
        else:
            print("❌ Invalid choice. Exiting...")
            
    except KeyboardInterrupt:
        print("\n⏹️ Process interrupted by user")
    except Exception as e:
        print(f"\n💥 Unexpected error: {e}")
        import traceback
        traceback.print_exc()


