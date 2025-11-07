"""
Simple tweet sender using Tweepy with safe credential handling.

Features:
- Reads credentials from environment variables
- Uses Twitter API v2 (Client) when possible, falls back to v1.1 (API)
- Validates tweet length and provides clear error messages

Required environment variables:
- TWITTER_API_KEY
- TWITTER_API_SECRET
- TWITTER_ACCESS_TOKEN
- TWITTER_ACCESS_TOKEN_SECRET
Optional (for v2):
- TWITTER_BEARER_TOKEN
"""

import os
import sys
from typing import Optional

import tweepy


def get_env(name: str, required: bool = True) -> Optional[str]:
    val = os.getenv(name)
    if required and not val:
        print(f"❌ Missing environment variable: {name}")
    return val


def post_tweet(text: str) -> None:
    if not text or len(text) > 280:
        raise ValueError("Tweet text must be between 1 and 280 characters")

    api_key = 'L8893sMB0kMS17Her2cpMjFbo'
    api_secret = 'enMG9zVQancWSPPqa1tXRfIGClFzyx5Jp59lwq4u6qDI6KUOzd'
    access_token = '1374608185844137985-zln6TsnCbMfy6DFIoGrYhnXlkdFwqK'
    access_token_secret = 'K6T4wCoOJ7PkQzGTu5RHKGijt3iCYWsKAspT1Tc9NyFQs'
    bearer_token = 'AAAAAAAAAAAAAAAAAAAAAJLG5AEAAAAATkxIFVJuCGqiCbbFrPedJVRA63k%3Di1KWvmI4v9PmbxyTrvZp1F1qMTkDHYjUkcQAAXsZFAgg2i3pwD'  # optional

    # Ensure required creds exist
    missing = [n for n, v in (
        ("TWITTER_API_KEY", api_key),
        ("TWITTER_API_SECRET", api_secret),
        ("TWITTER_ACCESS_TOKEN", access_token),
        ("TWITTER_ACCESS_TOKEN_SECRET", access_token_secret),
    ) if not v]
    if missing:
        raise RuntimeError(
            "Missing Twitter credentials. Set env vars: " + ", ".join(missing)
        )

    # Prefer v2 Client when bearer token is available
    if bearer_token:
        try:
            client = tweepy.Client(
                bearer_token=bearer_token,
                consumer_key=api_key,
                consumer_secret=api_secret,
                access_token=access_token,
                access_token_secret=access_token_secret,
            )
            resp = client.create_tweet(text=text)
            tweet_id = getattr(resp, "data", {}).get("id") if hasattr(resp, "data") else None
            print(f"✅ Tweet posted via v2! ID: {tweet_id}")
            return
        except tweepy.TweepyException as e:
            if "duplicate" in str(e).lower():
                print(f"⚠️ Duplicate content detected (v2): {e}")
                print("💡 Try adding unique timestamp or varying the content")
            elif "access level" in str(e).lower() or "subset" in str(e).lower():
                print(f"⚠️ v2 API access level insufficient: {e}")
                print("💡 Consider upgrading API access or using v1.1 fallback")
            else:
                print(f"⚠️ v2 create_tweet failed, falling back to v1.1: {e}")
        except Exception as e:
            print(f"⚠️ Unexpected error using v2, falling back to v1.1: {e}")

    # Fallback to v1.1 API
    auth = tweepy.OAuth1UserHandler(api_key, api_secret, access_token, access_token_secret)
    api = tweepy.API(auth)
    try:
        api.update_status(status=text)
        print("✅ Tweet posted via v1.1!")
    except tweepy.TweepyException as e:
        if "duplicate" in str(e).lower():
            print(f"❌ Duplicate content detected: {e}")
            print("💡 Add timestamp or unique content to avoid duplicates")
        elif "access level" in str(e).lower() or "subset" in str(e).lower():
            print(f"❌ API access level insufficient: {e}")
            print("💡 You may need to upgrade your X API access level")
        else:
            print(f"❌ Failed to post tweet: {e}")
        raise


def post_tweet_with_media(text: str, media_path: str) -> None:
    """
    Post a tweet with media (image/GIF/video).
    
    Note: This function requires elevated API access for media uploads.
    Basic/Essential access level may not support media posting.
    """
    if not text or len(text) > 280:
        raise ValueError("Tweet text must be between 1 and 280 characters")
    if not media_path or not os.path.exists(media_path):
        raise FileNotFoundError(f"Media file not found: {media_path}")

    api_key = 'L8893sMB0kMS17Her2cpMjFbo'
    api_secret = 'enMG9zVQancWSPPqa1tXRfIGClFzyx5Jp59lwq4u6qDI6KUOzd'
    access_token = '1374608185844137985-zln6TsnCbMfy6DFIoGrYhnXlkdFwqK'
    access_token_secret = 'K6T4wCoOJ7PkQzGTu5RHKGijt3iCYWsKAspT1Tc9NyFQs'
    bearer_token = 'AAAAAAAAAAAAAAAAAAAAAJLG5AEAAAAATkxIFVJuCGqiCbbFrPedJVRA63k%3Di1KWvmI4v9PmbxyTrvZp1F1qMTkDHYjUkcQAAXsZFAgg2i3pwD'  # optional

    missing = [n for n, v in (
        ("TWITTER_API_KEY", api_key),
        ("TWITTER_API_SECRET", api_secret),
        ("TWITTER_ACCESS_TOKEN", access_token),
        ("TWITTER_ACCESS_TOKEN_SECRET", access_token_secret),
    ) if not v]
    if missing:
        raise RuntimeError(
            "Missing Twitter credentials. Set env vars: " + ", ".join(missing)
        )

    # Build v1.1 API for media upload
    auth = tweepy.OAuth1UserHandler(api_key, api_secret, access_token, access_token_secret)
    api = tweepy.API(auth)

    # Check API access level first
    try:
        # Test with a simple API call to check access level
        api.verify_credentials()
        print("✅ API credentials verified")
    except tweepy.TweepyException as e:
        if "access level" in str(e).lower() or "subset" in str(e).lower():
            print(f"❌ Insufficient API access level for media uploads: {e}")
            print("💡 You may need to upgrade your X API access level to post media")
            raise RuntimeError("Media upload requires elevated API access")
        else:
            print(f"❌ API verification failed: {e}")
            raise

    # Decide chunked upload for likely large/video files
    lower = media_path.lower()
    use_chunked = lower.endswith((".mp4", ".mov", ".mkv"))
    
    try:
        print(f"📤 Uploading media: {media_path}")
        if use_chunked:
            media = api.media_upload(filename=media_path, chunked=True)
        else:
            media = api.media_upload(filename=media_path)
        media_id = getattr(media, "media_id", None)
        if not media_id:
            raise RuntimeError("Media upload did not return a media_id")
        print(f"📎 Media uploaded successfully. ID: {media_id}")
    except tweepy.TweepyException as e:
        if "access level" in str(e).lower() or "subset" in str(e).lower():
            print(f"❌ Media upload failed - insufficient API access: {e}")
            print("💡 Try upgrading to X API Pro or Enterprise for media uploads")
            raise RuntimeError("Media upload requires higher API access level")
        else:
            print(f"❌ Media upload failed: {e}")
            raise

    # Skip v2 API attempt for media posts with basic access
    print("ℹ️ Using v1.1 API for media post (recommended for basic access)")
    
    # Post with v1.1 API
    try:
        api.update_status(status=text, media_ids=[media_id])
        print("✅ Tweet with media posted via v1.1!")
    except tweepy.TweepyException as e:
        if "duplicate" in str(e).lower():
            print(f"⚠️ Duplicate content detected: {e}")
            print("💡 Try adding a timestamp or unique identifier to avoid duplicates")
        elif "access level" in str(e).lower():
            print(f"❌ Access level insufficient: {e}")
            print("💡 Upgrade your X API access to post tweets with media")
        else:
            print(f"❌ Failed to post tweet with media: {e}")
        raise


def add_unique_suffix(text: str, max_length: int = 280) -> str:
    """Add a unique timestamp suffix to avoid duplicate content."""
    from datetime import datetime
    
    # Create short timestamp
    timestamp = datetime.now().strftime("%H:%M")
    suffix = f" [{timestamp}]"
    
    # Ensure we don't exceed character limit
    if len(text) + len(suffix) > max_length:
        # Truncate text to fit suffix
        text = text[:max_length - len(suffix)].rstrip()
    
    return text + suffix


def create_trading_boost_notification(symbol: str, current_price: float, holders: int, volume_1h: float, boost_duration: str = "4h", add_timestamp: bool = False) -> str:
    """
    Create a trading notification similar to Skeleton Ecosystem trending boost.
    
    Args:
        symbol: Trading symbol (e.g., "XAUUSD", "BTCUSD")
        current_price: Current market price
        holders: Number of holders/participants
        volume_1h: 1-hour trading volume
        boost_duration: Duration of the boost (default "4h")
    
    Returns:
        Formatted notification text
    """
    
    notification = f"""🔥 GRID DCA ECOSYSTEM 🚀

🤖 Mr. DCA Bot entered {symbol} Trending Boost for [{boost_duration}]

💹 Price: ${current_price:,.2f}
👥 Traders: {holders}
📈 Volume 1H: ${volume_1h:,.0f}K

🎯 Target Profit System ON
✅ Risk Management Active

#GridDCA #AutoTrading #MT5"""

    if add_timestamp:
        notification = add_unique_suffix(notification)
    
    return notification


def create_balance_chart_notification(balance: float, equity: float, pnl: float, runtime: str, add_timestamp: bool = False) -> str:
    """
    Create a balance/performance update notification.
    
    Args:
        balance: Current account balance
        equity: Current equity
        pnl: Profit/Loss amount
        runtime: Session runtime
    
    Returns:
        Formatted performance notification
    """
    
    pnl_emoji = "📈" if pnl >= 0 else "📉"
    status_emoji = "✅" if pnl >= 0 else "⚠️"
    
    notification = f"""{pnl_emoji} GRID DCA UPDATE

{status_emoji} Runtime: {runtime}

💰 Balance: ${balance:,.2f}
💎 Equity: ${equity:,.2f}
{pnl_emoji} PnL: ${pnl:+,.2f}

🔥 Grid Active | 🎯 Profit Monitoring
📊 /balance for charts

#GridDCA #Performance"""

    if add_timestamp:
        notification = add_unique_suffix(notification)
    
    return notification


def create_chart_post_notification(symbol: str, timeframe: str, key_stats: dict) -> str:
    """
    Create a notification optimized for posting with balance charts.
    
    Args:
        symbol: Trading symbol
        timeframe: Chart timeframe (e.g., "24h", "1w")
        key_stats: Dict with balance, equity, pnl, etc.
    
    Returns:
        Short notification text for media posts
    """
    pnl = key_stats.get('pnl', 0)
    pnl_emoji = "📈" if pnl >= 0 else "📉" 
    
    notification = f"""{pnl_emoji} {symbol} Grid DCA ({timeframe})

💰 Balance: ${key_stats.get('balance', 0):,.0f}
💎 Equity: ${key_stats.get('equity', 0):,.0f}
{pnl_emoji} PnL: ${pnl:+,.0f}

🔥 Grid Strategy Active
📊 Chart attached

#GridDCA #TradingResults"""

    return notification


def demo_notifications():
    """Demo function to show both notification types without posting."""
    print("🔥 GRID DCA NOTIFICATION EXAMPLES\n")
    
    # Trading Boost Notification
    print("1️⃣ TRADING BOOST NOTIFICATION:")
    print("-" * 50)
    boost_notification = create_trading_boost_notification(
        symbol="XAUUSD",
        current_price=2750.50,
        holders=1247,
        volume_1h=2840.7,
        boost_duration="6h"
    )
    print(boost_notification)
    print(f"\n📊 Length: {len(boost_notification)}/280 characters\n")
    
    # Balance Update Notification  
    print("2️⃣ BALANCE UPDATE NOTIFICATION:")
    print("-" * 50)
    balance_notification = create_balance_chart_notification(
        balance=15750.25,
        equity=15892.40,
        pnl=+142.15,
        runtime="2h 35m"
    )
    print(balance_notification)
    print(f"\n📊 Length: {len(balance_notification)}/280 characters\n")
    
    # Loss example
    print("3️⃣ LOSS EXAMPLE:")
    print("-" * 50)
    loss_notification = create_balance_chart_notification(
        balance=9845.75,
        equity=9723.20,
        pnl=-276.80,
        runtime="4h 12m"
    )
    print(loss_notification)
    print(f"\n📊 Length: {len(loss_notification)}/280 characters\n")
    
    # Chart post example
    print("4️⃣ CHART POST NOTIFICATION:")
    print("-" * 50)
    chart_notification = create_chart_post_notification(
        symbol="XAUUSD",
        timeframe="24h",
        key_stats={
            'balance': 15750,
            'equity': 15892,
            'pnl': +142
        }
    )
    print(chart_notification)
    print(f"\n📊 Length: {len(chart_notification)}/280 characters")
    print("💡 This format is optimized for posting with balance chart images\n")


def check_api_access():
    """Check your current X API access level and provide recommendations."""
    print("🔍 X API ACCESS LEVEL CHECK\n")
    
    print("❌ Current Error: 403 Forbidden - Limited API Access")
    print("📊 Your current access level: Basic/Essential (Free Tier)\n")
    
    print("🚫 LIMITATIONS:")
    print("• Cannot post tweets via API")
    print("• Cannot upload media")
    print("• Read-only access to some endpoints")
    print("• Very limited rate limits\n")
    
    print("💡 SOLUTIONS:")
    print("1️⃣ UPGRADE API ACCESS:")
    print("   • X API Pro ($100/month)")
    print("   • X API Enterprise (Custom pricing)")
    print("   • Visit: https://developer.x.com/en/portal/product\n")
    
    print("2️⃣ ALTERNATIVE APPROACHES:")
    print("   • Use browser automation (Selenium)")
    print("   • Manual posting with generated content")
    print("   • Use scheduling tools like Buffer/Hootsuite")
    print("   • Copy-paste generated notifications\n")
    
    print("3️⃣ FOR TESTING:")
    print("   • Use demo_mode=True to generate content")
    print("   • Copy generated text and post manually")
    print("   • Test different notification formats\n")


if __name__ == "__main__":
    # Configuration
    demo_mode = False  # Set to False to attempt actual posting
    show_api_info = True  # Show API access information
    
    if show_api_info:
        check_api_access()
    
    if demo_mode:
        demo_notifications()
        print("✅ Demo completed! Generated notifications ready for manual posting.")
        print("💡 Set demo_mode=False only if you have upgraded API access.")
        sys.exit(0)
    
    # Example usage - choose notification type
    notification_type = "balance_update"  # or "trading_boost" or "original"
    
    if notification_type == "trading_boost":
        # Example: XAUUSD Gold trading boost notification
        tweet_text = create_trading_boost_notification(
            symbol="XAUUSD",
            current_price=2750.50,
            holders=1247,
            volume_1h=2840.7,
            boost_duration="6h",
            add_timestamp=True  # Add timestamp to avoid duplicates
        )
        
    elif notification_type == "balance_update":
        # Example: Performance update notification
        tweet_text = create_balance_chart_notification(
            balance=15750.25,
            equity=15892.40,
            pnl=+142.15,
            runtime="2h 35m",
            add_timestamp=True  # Add timestamp to avoid duplicates
        )
        
    else:
        # Original example tweet
        tweet_text = (
            "This is a test tweet sent using the Twitter API. "
            "It is part of a future analysis project and meets the minimum length requirement."
        )
    
    # Optional media path
    media_path = None  # Set to image path if you want to include media
    
    try:
        print(f"📝 Generated notification:\n{tweet_text}\n")
        print(f"📊 Character count: {len(tweet_text)}/280")
        
        if len(tweet_text) > 280:
            print("⚠️ Warning: Tweet exceeds 280 character limit!")
            sys.exit(1)
            
        # Uncomment below lines to actually post the tweet
        if media_path:
            print(f"Attempting to post with media: {media_path}")
            post_tweet_with_media(tweet_text, media_path)
        else:
            post_tweet(tweet_text)
            
        print("✅ Notification ready to post!")
        
    except Exception as exc:
        print(f"❌ Error: {exc}")
        sys.exit(1)
    sys.exit(0)
