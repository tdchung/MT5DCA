#!/usr/bin/env python3
"""
Test script for balance chart generation using Plotly
"""
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

# Test data
import pandas as pd
from datetime import datetime, timedelta
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import plotly.io as pio
import io

def test_chart_generation():
    """Test chart generation with sample data using Plotly"""
    try:
        # Create sample data
        now = datetime.now()
        data = []
        for i in range(100):
            timestamp = now - timedelta(minutes=i)
            balance = 10000 + (i * 2) + (i % 10) * 5
            equity = balance + (i % 20) - 10
            data.append({
                'timestamp': timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                'balance': balance,
                'equity': equity,
                'drawdown': max(0, 10000 - equity),
                'pnl_from_start': balance - 10000,
                'free_margin': equity * 0.85
            })
        
        df = pd.DataFrame(data)
        df['datetime'] = pd.to_datetime(df['timestamp'])
        
        # Create subplots with Plotly
        fig = make_subplots(
            rows=2, cols=1,
            subplot_titles=('Balance & Equity Test', 'Drawdown & PnL Test'),
            vertical_spacing=0.1,
            shared_xaxes=True
        )
        
        # Add balance and equity traces with dark theme colors
        fig.add_trace(
            go.Scatter(
                x=df['datetime'], 
                y=df['balance'],
                mode='lines',
                name='Balance',
                line=dict(color='#ffffff', width=3, shape='linear'),
                hovertemplate='<b>Balance</b><br>%{x}<br>$%{y:,.2f}<extra></extra>'
            ),
            row=1, col=1
        )
        
        fig.add_trace(
            go.Scatter(
                x=df['datetime'], 
                y=df['equity'],
                mode='lines',
                name='Equity',
                line=dict(color='#00ff88', width=3, shape='linear'),
                hovertemplate='<b>Equity</b><br>%{x}<br>$%{y:,.2f}<extra></extra>'
            ),
            row=1, col=1
        )
        
        # Add drawdown and PnL traces with dark theme colors
        fig.add_trace(
            go.Scatter(
                x=df['datetime'], 
                y=df['drawdown'],
                mode='lines',
                name='Drawdown',
                line=dict(color='#ff4444', width=2, shape='linear'),
                hovertemplate='<b>Drawdown</b><br>%{x}<br>$%{y:,.2f}<extra></extra>'
            ),
            row=2, col=1
        )
        
        fig.add_trace(
            go.Scatter(
                x=df['datetime'], 
                y=df['pnl_from_start'],
                mode='lines',
                name='PnL from Start',
                line=dict(color='#ffaa00', width=2, shape='linear'),
                hovertemplate='<b>PnL from Start</b><br>%{x}<br>$%{y:,.2f}<extra></extra>'
            ),
            row=2, col=1
        )
        
        # Update layout with dark theme
        fig.update_layout(
            title=dict(
                text='Balance Chart Test - Dark Theme',
                font=dict(size=18, color='#ffffff', family='Arial Black'),
                x=0.05,
                y=0.95
            ),
            paper_bgcolor='#2c3e50',  # Dark blue-gray background
            plot_bgcolor='#34495e',   # Slightly lighter plot area
            height=600,
            width=1000,
            showlegend=True,
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=-0.15,
                xanchor="center",
                x=0.5,
                font=dict(color='#ffffff', size=12),
                bgcolor='rgba(0,0,0,0)'
            ),
            margin=dict(l=80, r=40, t=80, b=80),
            font=dict(color='#ffffff', family='Arial')
        )
        
        # Update axes with dark theme styling
        fig.update_yaxes(
            title_text="Amount ($)", 
            title_font=dict(color='#ffffff', size=14),
            gridcolor='#4a5a6a', 
            gridwidth=1,
            tickfont=dict(color='#ffffff', size=11),
            linecolor='#4a5a6a',
            row=1, col=1
        )
        fig.update_yaxes(
            title_text="Amount ($)", 
            title_font=dict(color='#ffffff', size=14),
            gridcolor='#4a5a6a', 
            gridwidth=1,
            tickfont=dict(color='#ffffff', size=11),
            linecolor='#4a5a6a',
            row=2, col=1
        )
        fig.update_xaxes(
            title_text="Time", 
            title_font=dict(color='#ffffff', size=14),
            gridcolor='#4a5a6a', 
            gridwidth=1,
            tickfont=dict(color='#ffffff', size=11),
            linecolor='#4a5a6a',
            row=2, col=1
        )
        
        # Save as HTML for interactive viewing (PNG export may have issues on some systems)
        fig.write_html("test_balance_chart_plotly.html")
        
        # Try to save as PNG, but continue if it fails
        try:
            fig.write_image("test_balance_chart_plotly.png", width=1000, height=600, scale=2)
            png_saved = True
        except Exception as png_error:
            print(f"⚠️  PNG export failed: {png_error}")
            png_saved = False
        
        print("✅ Dark theme chart generation test successful!")
        if png_saved:
            print("📊 Saved as test_balance_chart_plotly.png (static - dark theme)")
        print("🌐 Saved as test_balance_chart_plotly.html (interactive - dark theme)")
        print("💡 Open the HTML file in your browser to view the interactive dark theme chart!")
        return True
        
    except Exception as e:
        print(f"❌ Plotly chart generation test failed: {e}")
        print("💡 Try installing: pip install plotly kaleido")
        return False

if __name__ == "__main__":
    test_chart_generation()