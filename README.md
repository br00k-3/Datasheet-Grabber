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

## Example
<img width="902" height="632" alt="Screenshot 2025-08-08 122333" src="https://github.com/user-attachments/assets/994a968d-ce2e-460f-b0c1-c7d9c96f5c88" />

## Setup

1. **API Keys**: Press the key icon in the top right and enter your API key and secret



2. **Parts List**: Prepare a CSV file (e.g., `parts.csv`) with columns:

```
Internal P/N,Manufacturer,Manufacturer P/N
190.0015,ON Semiconductor,1N5235B
...
```

## Usage

Press Start Download! (It's that easy)

- Results are saved to `datasheets/report.csv`.
- Downloaded PDFs are saved in the `datasheets/` folder.
- Errors and warnings are logged to `report.log`.

## Customization

- **Worker Counts**: Adjust `MAX_WORKERS` and `MAX_API_WORKERS` in settings
- **Rate Limit**: Adjust the number of calls made per minute
- **Max Retries**: Adjust how many times the program will retry a download on fail
  
## Troubleshooting

- If you see 403/429 errors, check your API keys and try increasing crawl delays.
- For debugging, check `report.log` for detailed error messages.

## License

MIT License. See `LICENSE` file for details.


*Created by br00k-3. Contributions welcome!*

## 📋 Report Format

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
