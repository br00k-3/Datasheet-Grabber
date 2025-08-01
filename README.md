# 🚀 DigiKey Datasheet Downloader

**Clean, professional tool to automatically download datasheets for electronic components from DigiKey.**

## ✨ Features

- 📊 **Real-time progress** with ETA and worker status indicators
- 🔄 **Auto-resume** - skips existing files, handles interruptions gracefully
- 🛡️ **Process protection** - prevents multiple instances from running
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
REQUESTS_PER_MINUTE = 15  # Rate limit to avoid blocks
TIMEOUT_SECONDS = 30    # Request timeout
RESUME_ON_RESTART = True    # Skip existing files
```

### Multiple API Keys (Optional)
For higher throughput, add multiple API keys:

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

---

## 📊 Example Output

```
🚀 DigiKey Datasheet Downloader
==================================================
📚 Loaded 5,881 manufacturers
📋 Found 200 parts to process
⚙️  Settings: 3 workers, 15 req/min
⏱️  Estimated time: 4.4 minutes
💡 Press Ctrl+C to stop safely at any time
==================================================
🔐 Authenticating with DigiKey...
✅ Authentication successful!

[████████████████████████████████] 185/200 (92.5%) | ETA: 0:23 | Workers: 🔍 📥 ✅ | ✅ Downloaded R185

✅ Completed in 8:42

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
| **High failure rate** | Lower `REQUESTS_PER_MINUTE` to 10-12 |
| **Timeouts** | Increase `TIMEOUT_SECONDS` to 45-60 |
| **Multiple instances** | Script prevents this automatically |
| **Interrupted download** | Just restart - auto-resumes from where it left off |
| **Need speed** | Increase `MAX_WORKERS` to 3-5 (watch rate limits) |

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
