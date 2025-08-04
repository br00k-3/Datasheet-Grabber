# 🚀 DigiKey Datasheet Downloader

**Clean, professional tool to automatically download datasheets for electronic components from DigiKey.**

## ✨ Features

- 📊 **Real-time progress** with ETA and worker status indicators
- 🔄 **Auto-resume** - skips existing files, handles interruptions gracefully
- � **Multiple API workers** - parallel part searching with separate API keys for 2x+ speed
- 🎯 **Smart manufacturer matching** - fuzzy matching with 5,881+ manufacturers
- 📄 **CSV support** - automatic column detection for spreadsheets
- ⚡ **Parallel processing** - configurable workers for optimal speed
- 🔒 **Built-in protections** - rate limiting, timeout handling, clean shutdown

---

## 🚀 Quick Start

### 1. Setup API Keys
```bash
cp api_keys.json.template api_keys.json
# Edit api_keys.json with your DigiKey API credentials
```

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

### 2. Create Parts List

**CSV Format (Recommended)** - Create `parts_list.csv`:
```csv
Item Number,Mfr. Part Number,Mfr. Name
R001,RC0603FR-071KL,Yageo
C001,GRM188R71H104KA93D,Murata
U001,STM32F407VGT6,STMicroelectronics
```

**Text Format** - Create `parts_list.txt`:
```
# Format: [Internal P/N] [Manufacturer P/N] [Manufacturer]
R001    RC0603FR-071KL      Yageo
C001    GRM188R71H104KA93D  Murata
U001    STM32F407VGT6       STMicroelectronics
```

### 3. Run the Script
```bash
python3 script.py
```

### 4. View Results
- 📁 PDFs saved to `datasheets/` directory
- 📊 Detailed report in `datasheets/report.csv`
- ✅ Auto-resumes on restart

---

## ⚙️ Configuration

### Performance Settings
Edit the top of `script.py` to customize:

```python
MAX_WORKERS = 3         # Parallel downloads (1-5 recommended)
MAX_API_WORKERS = 2     # Parallel API searchers (1-3 recommended)
REQUESTS_PER_MINUTE = 15  # Rate limit per API worker
TIMEOUT_SECONDS = 30    # Request timeout
RESUME_ON_RESTART = True    # Skip existing files
```

### Multiple API Keys (Recommended for Speed)
For **2x faster part searching**, add multiple API keys. Each API worker will use its own key:

```json
{
  "api_keys": [
    {
      "CLIENT_ID": "first_client_id",
      "CLIENT_SECRET": "first_client_secret"
    },
    {
      "CLIENT_ID": "second_client_id", 
      "CLIENT_SECRET": "second_client_secret"
    }
  ]
}
```

**Performance with Multiple API Keys:**
- 1 API key: ~15 parts/minute
- 2 API keys: ~30 parts/minute  
- 3 API keys: ~45 parts/minute

**Note:** Script automatically uses all available API keys up to `MAX_API_WORKERS` limit.

---

## 📊 Example Output

```
🚀 DigiKey Datasheet Downloader (Multi-API)
==================================================
📚 Loaded 5,881 manufacturers with 66 acquisition mappings
📋 Found 200 parts to process
⚙️  Settings: 2 API workers + 3 download workers, 15 req/min per API worker
🔑 Started API-Worker-1 with API key #1
🔑 Started API-Worker-2 with API key #2
⏱️  Estimated time: 3.3 minutes (2x faster with dual API keys!)
💡 Press Ctrl+C to stop safely at any time
==================================================
🔐 Authenticating API-Worker-1 with DigiKey...
🔐 Authenticating API-Worker-2 with DigiKey...
✅ Authentication successful for both workers!

[████████████████████████████████] 185/200 (92.5%) | ETA: 0:12 | Processing...
  API-Worker-1: 🔍 Searching R092
  API-Worker-2: � Searching C156  
  DL-Worker-1: 📥 Downloading R089
  DL-Worker-2: ✅ Success R087
  DL-Worker-3: ⚪ Idle

✅ Completed in 4:15 (2.1x speedup with dual API workers!)

==================================================
📊 SUMMARY
==================================================
✅ Downloaded: 185
⚠️  No datasheet: 8
❌ Not found: 5  
⚠️  Download failed: 2
❌ Errors: 0
📄 Report saved: datasheets/report.csv
```

## 🔧 Troubleshooting

| Issue | Solution |
|-------|----------|
| **High failure rate** | Lower `REQUESTS_PER_MINUTE` to 10-12 per API worker |
| **Timeouts** | Increase `TIMEOUT_SECONDS` to 45-60 |
| **Need more speed** | Add more API keys and increase `MAX_API_WORKERS` to 2-3 |
| **Interrupted download** | Just restart - auto-resumes from where it left off |
| **Download speed** | Increase `MAX_WORKERS` to 3-5 (watch rate limits) |

## 🛡️ Built-in Safety Features

- **Process locking** - Prevents multiple script instances
- **Rate limiting** - Respects DigiKey's API limits  
- **Smart retries** - Handles temporary failures gracefully
- **Timeout protection** - Prevents hanging on problematic downloads
- **Clean shutdown** - Ctrl+C stops safely without data corruption
- **PDF validation** - Ensures downloaded files are valid

## 📄 Report Details

The `datasheets/report.csv` includes:
- **Success**: Downloaded PDFs with filenames
- **No Datasheet**: Parts found but no datasheet available
- **Not Found**: Parts not in DigiKey database
- **Download Failed**: Parts found but download blocked (includes manual URLs)
- **Errors**: API or network issues

## ⚠️ Important Notes

- **API Keys**: Never commit `api_keys.json` (it's in `.gitignore`)
- **Rate Limits**: Start conservative, increase if no issues
- **Large Lists**: Script handles 200+ parts automatically with resume capability
- **Legal**: Ensure compliance with DigiKey's Terms of Service
