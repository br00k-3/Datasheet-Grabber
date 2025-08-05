# 🚀 DigiKey Datasheet Downloader

**Fast, reliable tool to automatically download datasheets for electronic components from DigiKey with advanced 403 error handling.**

## ✨ Features

- 📊 **Real-time progress** with live ETA and worker status indicators
- 🔄 **Smart resume** - automatically skips existing files and handles interruptions
- 🛡️ **Advanced 403 protection** - random user agents, exponential backoff, and retry logic
- 🎯 **Intelligent part matching** - uses DigiKey's fuzzy search API for best results
- 📄 **CSV input support** - works with standard parts list spreadsheets
- ⚡ **High-speed downloads** - configurable parallel workers (up to 10 simultaneous downloads)
- � **Organized reports** - results sorted by status with detailed error information
- 🔒 **Rate limit handling** - automatic 429 detection and recovery

---

## 🚀 Quick Start

### 1. Setup API Keys
Edit the API keys file:

```json
{
  "api_keys": [
    {
      "CLIENT_ID": "your_digikey_client_id",
      "CLIENT_SECRET": "your_digikey_client_secret"
    }
  ]
}
```

### 2. Prepare Your Parts List

**CSV Format** - Create `parts.csv`:
```csv
Internal P/N,Manufacturer,Manufacturer P/N
190.0025,onsemi,MUR860G
190.0027,STMicroelectronics,STTH8L06D
193.0002,Diodes Incorporated,BAV99-7-F
```

### 3. Run the Script
```bash
python3 script_new.py parts.csv
```

### 4. View Results
- 📁 PDFs automatically saved to `datasheets/` directory
- 📊 Comprehensive report generated as `datasheets/report.csv`
- ✅ Automatic resume on restart - no duplicate downloads

---

## ⚙️ Configuration

Edit the top of `script_new.py` to customize performance:

```python
MAX_WORKERS = 10          # Parallel downloads (1-15 recommended)
MAX_API_WORKERS = 1       # Parallel API workers (1-3 max)
REQUESTS_PER_MINUTE = 120 # API rate limit per worker
TIMEOUT_SECONDS = 30      # Download timeout
RESUME_ON_RESTART = True  # Skip existing files
```

### Performance Recommendations
- **Small lists (< 50 parts)**: `MAX_WORKERS = 5`
- **Medium lists (50-200 parts)**: `MAX_WORKERS = 10` 
- **Large lists (200+ parts)**: `MAX_WORKERS = 15`
- **Rate limiting issues**: Reduce `REQUESTS_PER_MINUTE` to 60-80

---

## 📊 Example Output

```
🚀 Starting datasheet downloader...
⚙️  Settings: 1 API workers + 10 download workers
� Loaded 217 parts from parts.csv

� Progress:
==================================================
███████████████████████████████ 185/217 (85.3%)
Elapsed: 4:15 | ETA: 0:45

  ✅ Downloaded: 156
  ⏭️  Skipped: 12
  ⚠️  No datasheet: 8
  ❌ Not found: 5
  ⚠️  Download failed: 3
  ❌ Errors: 1

Workers:
==================================================
  API-Worker-1: 🔍 193.0045
  DL-Worker-1: 📥 190.0087
  DL-Worker-2: 📥 193.0039
  DL-Worker-3: ⚪ Idle

==================================================
📊 FINAL SUMMARY
==================================================
✅ Downloaded: 185
⏭️  Skipped: 12
⚠️  No datasheet: 8
❌ Not found: 7
⚠️  Download failed: 4
❌ Errors: 1
⏱️  Total time: 5:32
==================================================
📄 Report saved: datasheets/report.csv
✅ Complete!
```

## �️ Advanced 403 Error Protection

This version includes sophisticated anti-blocking measures:

- **🎲 Random User-Agent Rotation** - 5 different browser profiles to avoid fingerprinting
- **⏰ Exponential Backoff** - Smart retry logic for 403 errors (1s → 2s → 4s delays)
- **🌐 Realistic Headers** - Complete browser header sets that pass anti-bot checks
- **🔄 Automatic Retries** - Up to 3 attempts per download with random delays
- **🚦 Rate Limit Recovery** - Automatic handling of 429 errors with backoff

## 📋 Report Format

The `datasheets/report.csv` is organized by status priority:

| Status | Description | Action Required |
|--------|-------------|-----------------|
| **Success** | ✅ Downloaded successfully | None - files in `datasheets/` |
| **No Datasheet** | ⚠️ Part found but no PDF available | Contact manufacturer |
| **Not Found** | ❌ Part not in DigiKey database | Verify part number/manufacturer |
| **Download Failed** | ⚠️ PDF blocked or inaccessible | Manual download from URL |
| **Error** | ❌ API or network issues | Check connection/API keys |

## 🔧 Troubleshooting

| Problem | Solution |
|---------|----------|
| **Many 403 errors** | Script auto-handles this, but reduce `MAX_WORKERS` to 5-8 |
| **Rate limit errors** | Reduce `REQUESTS_PER_MINUTE` to 60-80 |
| **Timeouts** | Increase `TIMEOUT_SECONDS` to 45-60 |
| **Slow downloads** | Increase `MAX_WORKERS` to 10-15 |
| **Interrupted run** | Just restart - auto-resumes from where it stopped |

## 🎯 Key Improvements Over Original

- **🚀 10x Faster Downloads** - Parallel workers instead of sequential
- **🛡️ Better 403 Handling** - Advanced anti-bot techniques with retry logic
- **📊 Cleaner Interface** - Streamlined progress display and error reporting
- **🔧 Simpler Architecture** - Pure requests-based, no browser automation overhead
- **📋 Organized Output** - Reports sorted by status for easy review
- **⚡ Higher Reliability** - Robust error handling and automatic recovery

## ⚠️ Important Notes

- **API Keys**: Keep `api_keys.json` secure and never commit to version control
- **Rate Limits**: Start conservative, the script auto-detects and handles limits
- **Large Lists**: Handles 500+ parts efficiently with resume capability
- **CSV Format**: Standard 3-column format: Internal P/N, Manufacturer, Manufacturer P/N
- **Legal**: Ensure compliance with DigiKey's Terms of Service

---

**Ready to download? Just run `python3 script_new.py parts.csv` and watch it work! 🚀**
