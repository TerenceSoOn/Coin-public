# Frontend UI Setup

## 🎨 Features

The web UI provides a modern dashboard to:
- ⚙️ **Change Configuration** - Modify AI cadence, risk limits, trading mode
- 🎯 **View AI Decisions** - See recent decisions with confidence levels and rationales
- 📋 **Monitor Logs** - Check system activity (instructions provided)
- 📊 **Inspect AI Data** - View exactly what's sent to AI and what it returns

## 🚀 Quick Start

1. **Make sure backend is running:**
   ```bash
   curl http://localhost:8000/health
   # Should return: {"status":"ok"}
   ```

2. **Open the UI:**
   ```bash
   open /Users/terence/Desktop/coin/frontend/index.html
   ```
   
   Or double-click `index.html` in Finder.

## 📊 Data Size Analysis

**Current AI input size:** ~7,573 tokens (~30KB JSON)
- Total bars: 292 (across 5 timeframes × 2 symbols)
- DeepSeek context limit: 64K tokens
- **Usage: Only 11.6% of available context** ✅

**Verdict: Data size is REASONABLE**
- Multi-timeframe analysis provides comprehensive market view
- Token usage is efficient and well within limits
- Cost is minimal with DeepSeek pricing

## 🎯 UI Tabs

### 1. Configuration Tab
- Change trading mode (paper/sandbox/live)
- Adjust AI decision frequency
- Modify risk limits (position size, leverage, loss caps)
- Changes take effect immediately (no server restart needed)

### 2. AI Decisions Tab
- View last 10 AI decisions
- See confidence levels (visual bar)
- Check actions for each symbol (buy/sell/hold)
- Read AI rationales

### 3. Logs Tab
- Instructions to view logs in terminal
- Backend writes all logs to `/tmp/uvicorn.log`
- Use `tail -f /tmp/uvicorn.log` for live monitoring

### 4. AI Input/Output Tab
- **Input**: Complete market data sent to AI (all timeframes, prices, constraints)
- **Output**: AI's decision with actions, confidence, and reasoning
- Raw JSON view for debugging

## 🔧 Backend CORS Setup

CORS has been enabled in `backend/app/main.py` to allow browser requests:
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
```

## 📝 Notes

- UI is a single HTML file with no dependencies
- Works offline (just needs backend API)
- Modern dark theme optimized for trading
- Auto-refreshes status every 5 seconds
- All changes via API are runtime - no restart needed

## 🛠️ Troubleshooting

**"Backend: Offline" error:**
- Check if backend is running: `ps aux | grep uvicorn`
- Restart backend: `cd backend && . ../.venv/bin/activate && uvicorn app.main:app --host 0.0.0.0 --port 8000`

**CORS errors in browser console:**
- Make sure you restarted backend after adding CORS middleware
- Check logs: `tail -20 /tmp/uvicorn.log`

**Config changes not saving:**
- Check browser console for errors (F12)
- Verify backend API is responding: `curl http://localhost:8000/api/config`
