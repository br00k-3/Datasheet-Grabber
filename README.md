
# Datasheet Grabber

A robust, multi-threaded Python tool for automatically searching, downloading, and reporting datasheets for electronic parts. Designed for reliability, anti-bot evasion, and user-friendly progress display.

## Features

- **Multi-threaded**: Fast, concurrent downloads and API lookups.
- **Per-domain crawl delay**: Respects vendor rate limits and robots.txt.
- **Realistic browser headers**: Avoids 403/429 errors by mimicking real browsers.
- **Persistent cookies**: Uses sessions for better anti-bot evasion.
- **Graceful shutdown**: Handles rate limits and user interrupts cleanly.
- **Rich progress display**: Beautiful, live-updating terminal UI with per-worker status.
- **Detailed CSV report**: Results are saved and sorted for easy review.

## Requirements

- Python 3.8+
- `requests`
- `rich`

Install dependencies:

```
pip install -r requirements.txt
```

## Setup

1. **API Keys**: Create an `api_keys.json` file in the project root:

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

2. **Parts List**: Prepare a CSV file (e.g., `parts.csv`) with columns:

```
Internal P/N,Manufacturer,Manufacturer P/N
190.0015,ON Semiconductor,1N5235B
...
```

## Usage

Run the script with your parts file:

```
python script.py parts.csv
```

- Progress and worker status will be shown in the terminal.
- Results are saved to `datasheets/report.csv`.
- Downloaded PDFs are saved in the `datasheets/` folder.
- Errors and warnings are logged to `report.log`.

## Customization

- **Crawl Delays**: Edit `CRAWL_DELAY_PER_DOMAIN` and `CRAWL_DELAY_DEFAULT` in `script.py`.
- **Worker Counts**: Adjust `MAX_WORKERS` and `MAX_API_WORKERS` in `script.py`.

## Troubleshooting

- If you see 403/429 errors, check your API keys and try increasing crawl delays.
- If the display is glitchy, try resizing your terminal or using a different terminal emulator.
- For debugging, check `report.log` for detailed error messages.

## License

MIT License. See `LICENSE` file for details.


*Created by br00k-3. Contributions welcome!*

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
| **Slow downloads** | Increase `MAX_WORKERS` to 10-15 |
| **Interrupted run** | Just restart - auto-resumes from where it stopped |

## ⚠️ Important Notes

- **Rate Limits**: Start conservative, the script auto-detects and handles limits
- **Large Lists**: Handles 500+ parts efficiently with resume capability
- **CSV Format**: Standard 3-column format: Internal P/N, Manufacturer, Manufacturer P/N
- **Legal**: Ensure compliance with DigiKey's Terms of Service

---

**Ready to download? Just run `python3 script_new.py parts.csv` and watch it work!**
