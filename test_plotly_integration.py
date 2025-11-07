#!/usr/bin/env python3
"""
Test the balance chart generation method in isolation
"""
import sys
import os
sys.path.append('src')

# Create sample balance data
import pandas as pd
from datetime import datetime, timedelta
import csv

def create_sample_balance_data():
    """Create a sample balance CSV file for testing"""
    # Ensure data directory exists
    os.makedirs('data/balances', exist_ok=True)
    
    # Create sample data
    filename = 'data/balances/balance_equity_test.csv'
    now = datetime.now()
    
    with open(filename, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['timestamp', 'datetime_gmt7', 'balance', 'equity', 'free_margin', 
                        'drawdown', 'pnl_from_start', 'session_runtime_minutes'])
        
        for i in range(120):  # 2 hours of data
            timestamp = now - timedelta(minutes=i)
            gmt7_time = timestamp + timedelta(hours=7)
            balance = 10000 + (i * 1.5) + (i % 15) * 3
            equity = balance + (i % 25) - 12
            free_margin = equity * 0.85
            drawdown = max(0, 10000 - equity)
            pnl_from_start = balance - 10000
            runtime_minutes = 120 - i
            
            writer.writerow([
                timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                gmt7_time.strftime('%Y-%m-%d %H:%M:%S'),
                f"{balance:.2f}",
                f"{equity:.2f}",
                f"{free_margin:.2f}",
                f"{drawdown:.2f}",
                f"{pnl_from_start:.2f}",
                f"{runtime_minutes:.1f}"
            ])
    
    print(f"✅ Created sample balance data: {filename}")
    return filename

def test_isolated_chart():
    """Test chart generation in isolation"""
    try:
        # Import after path setup
        from strategy.grid_dca_strategy import GridDCAStrategy
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
        import plotly.io as pio
        
        # Create sample data
        balance_file = create_sample_balance_data()
        
        # Create a minimal strategy instance just for testing the chart method
        class TestStrategy:
            def __init__(self, balance_log_file):
                self.balance_log_file = balance_log_file
                import logging
                self.logger = logging.getLogger(__name__)
            
            # Copy the chart generation method
            def generate_balance_chart(self, hours=24):
                """Generate balance/equity chart from CSV data using Plotly."""
                try:
                    if not self.balance_log_file or not os.path.exists(self.balance_log_file):
                        return None, "No balance log file found. Start the strategy to begin logging."
                    
                    # Read CSV data
                    df = pd.read_csv(self.balance_log_file)
                    if df.empty:
                        return None, "No data found in balance log file."
                    
                    # Convert timestamp to datetime
                    df['datetime'] = pd.to_datetime(df['timestamp'])
                    
                    # Filter recent data based on hours parameter
                    if hours > 0:
                        cutoff_time = datetime.now() - timedelta(hours=hours)
                        df = df[df['datetime'] >= cutoff_time]
                    
                    if df.empty:
                        return None, f"No data found in the last {hours} hours."
                    
                    # Create subplots with Plotly
                    fig = make_subplots(
                        rows=2, cols=1,
                        subplot_titles=('Balance & Equity', 'Drawdown & PnL'),
                        vertical_spacing=0.1,
                        shared_xaxes=True
                    )
                    
                    # Add balance and equity traces to top subplot
                    fig.add_trace(
                        go.Scatter(
                            x=df['datetime'], 
                            y=df['balance'],
                            mode='lines',
                            name='Balance',
                            line=dict(color='#2E86AB', width=2),
                            hovertemplate='<b>Balance</b><br>Time: %{x}<br>Amount: $%{y:.2f}<extra></extra>'
                        ),
                        row=1, col=1
                    )
                    
                    fig.add_trace(
                        go.Scatter(
                            x=df['datetime'], 
                            y=df['equity'],
                            mode='lines',
                            name='Equity',
                            line=dict(color='#A23B72', width=2),
                            hovertemplate='<b>Equity</b><br>Time: %{x}<br>Amount: $%{y:.2f}<extra></extra>'
                        ),
                        row=1, col=1
                    )
                    
                    # Add drawdown and PnL traces to bottom subplot
                    fig.add_trace(
                        go.Scatter(
                            x=df['datetime'], 
                            y=df['drawdown'],
                            mode='lines',
                            name='Drawdown',
                            line=dict(color='#F18F01', width=2),
                            hovertemplate='<b>Drawdown</b><br>Time: %{x}<br>Amount: $%{y:.2f}<extra></extra>'
                        ),
                        row=2, col=1
                    )
                    
                    fig.add_trace(
                        go.Scatter(
                            x=df['datetime'], 
                            y=df['pnl_from_start'],
                            mode='lines',
                            name='PnL from Start',
                            line=dict(color='#C73E1D', width=2),
                            hovertemplate='<b>PnL from Start</b><br>Time: %{x}<br>Amount: $%{y:.2f}<extra></extra>'
                        ),
                        row=2, col=1
                    )
                    
                    # Update layout
                    fig.update_layout(
                        title=dict(
                            text=f'Balance & Equity Chart (Last {hours}h)',
                            font=dict(size=16, color='#1f2937'),
                            x=0.5
                        ),
                        template='plotly_white',
                        height=600,
                        width=1000,
                        showlegend=True,
                        legend=dict(
                            orientation="h",
                            yanchor="bottom",
                            y=1.02,
                            xanchor="right",
                            x=1
                        ),
                        margin=dict(l=60, r=60, t=80, b=60)
                    )
                    
                    # Update axes
                    fig.update_yaxes(title_text="Amount ($)", gridcolor='#e5e7eb', row=1, col=1)
                    fig.update_yaxes(title_text="Amount ($)", gridcolor='#e5e7eb', row=2, col=1)
                    fig.update_xaxes(title_text="Time", gridcolor='#e5e7eb', row=2, col=1)
                    
                    # Convert to PNG using kaleido engine
                    img_bytes = pio.to_image(fig, format='png', width=1000, height=600, scale=2)
                    
                    # Save test files
                    with open('test_balance_isolated.png', 'wb') as f:
                        f.write(img_bytes)
                    
                    fig.write_html('test_balance_isolated.html')
                    
                    import io
                    buf = io.BytesIO(img_bytes)
                    buf.seek(0)
                    
                    # Generate summary stats
                    latest = df.iloc[-1]
                    oldest = df.iloc[0]
                    duration_hours = (latest['datetime'] - oldest['datetime']).total_seconds() / 3600
                    
                    stats = (
                        f"📊 Balance Chart Summary\n\n"
                        f"• Period: {duration_hours:.1f} hours ({len(df)} data points)\n"
                        f"• Current Balance: ${latest['balance']:.2f}\n"
                        f"• Current Equity: ${latest['equity']:.2f}\n"
                        f"• Total PnL: ${latest['pnl_from_start']:.2f}\n"
                        f"• Max Drawdown: ${df['drawdown'].max():.2f}\n"
                        f"• Free Margin: ${latest['free_margin']:.2f}\n\n"
                        f"• Balance Range: ${df['balance'].min():.2f} - ${df['balance'].max():.2f}\n"
                        f"• Equity Range: ${df['equity'].min():.2f} - ${df['equity'].max():.2f}"
                    )
                    
                    return buf, stats
                    
                except Exception as e:
                    self.logger.error(f"Error generating balance chart: {e}")
                    return None, f"Error generating chart: {str(e)}"
        
        # Test the chart generation
        test_strategy = TestStrategy(balance_file)
        chart_buffer, stats = test_strategy.generate_balance_chart(2)  # Last 2 hours
        
        if chart_buffer:
            print("✅ Chart generated successfully!")
            print("📊 Files created:")
            print("   - test_balance_isolated.png")
            print("   - test_balance_isolated.html")
            print("\n" + stats)
            chart_buffer.close()
            return True
        else:
            print(f"❌ Chart generation failed: {stats}")
            return False
            
    except Exception as e:
        print(f"❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_isolated_chart()
    if success:
        print("\n🎉 Plotly integration test passed!")
    else:
        print("\n💥 Plotly integration test failed!")