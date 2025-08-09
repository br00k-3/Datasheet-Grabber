import requests
import os
import json
import threading
import logging
from datetime import datetime, timedelta
import time
import random
import queue
import csv
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Ensure logger is defined
logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3

class RateLimitExceeded(Exception):
    pass

class APIWorker:
    def __init__(self, downloader, download_queue, results_queue, progress, api_key, worker_id="API-Worker"):
        self.downloader = downloader
        self.download_queue = download_queue
        self.results_queue = results_queue
        self.progress = progress
        self.api_key = api_key
        self.worker_id = worker_id
        self.is_running = False
        self.thread = None
        self.parts_queue = None
        self.access_token = None
        self.token_expiry = None

    def start(self):
        if not self.is_running:
            self.is_running = True
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()

    def stop(self):
        self.is_running = False
        if self.thread:
            self.thread.join(timeout=5)

    def set_parts_queue(self, parts_queue):
        self.parts_queue = parts_queue

    def _run(self):
        while self.is_running:
            try:
                if self.parts_queue is None:
                    time.sleep(0.1)
                    continue
                
                try:
                    part_data = self.parts_queue.get(timeout=1.0)
                    internal_pn, manufacturer_pn, manufacturer = part_data
                    
                    if self.progress:
                        self.progress.update_worker_status(self.worker_id, "🔍 Searching", manufacturer_pn)
                    
                    result = self.search_part(manufacturer_pn)
                    
                    if result.get('error'):
                        logger.error(f"API error for {manufacturer_pn}: {result.get('message', '')}")
                        self.results_queue.put({
                            'internal_pn': internal_pn,
                            'manufacturer_pn': manufacturer_pn,
                            'manufacturer': manufacturer,
                            'status': 'error',
                            'message': result.get('message', 'API error'),
                            'datasheet_url': '',
                            'file_path': ''
                        })
                    elif result.get('datasheet_url'):
                        download_task = {
                            'internal_pn': internal_pn,
                            'manufacturer_pn': manufacturer_pn,
                            'manufacturer': manufacturer,
                            'datasheet_url': result['datasheet_url'],
                            'product_info': result.get('product_info', {})
                        }
                        self.download_queue.put(download_task)
                    else:
                        self.results_queue.put({
                            'internal_pn': internal_pn,
                            'manufacturer_pn': manufacturer_pn,
                            'manufacturer': manufacturer,
                            'status': 'not_found',
                            'message': 'Part not found in API',
                            'datasheet_url': '',
                            'file_path': ''
                        })
                    
                    if self.progress:
                        self.progress.update_worker_status(self.worker_id, "✅ Complete", "")
                    
                    self.parts_queue.task_done()
                    
                except queue.Empty:
                    # No more parts to process
                    if self.progress:
                        self.progress.update_worker_status(self.worker_id, "⚪ Waiting", "")
                    continue
                    
            except RateLimitExceeded:
                if self.progress:
                    self.progress.update_worker_status(self.worker_id, "⏱️ Rate limited", "")
                time.sleep(60)  # Wait 1 minute
            except Exception as e:
                logger.error(f"API Worker error: {e}")
                if self.progress:
                    self.progress.update_worker_status(self.worker_id, "❌ Error", str(e)[:20])
                
        if self.progress:
            self.progress.update_worker_status(self.worker_id, "⚪ Idle", "")

    def authenticate(self):
        data = {
            'client_id': self.api_key['CLIENT_ID'],
            'client_secret': self.api_key['CLIENT_SECRET'],
            'grant_type': 'client_credentials'
        }
        try:
            response = requests.post("https://api.digikey.com/v1/oauth2/token", data=data, timeout=30)
            if response.status_code == 200:
                token_data = response.json()
                self.access_token = token_data['access_token']
                expires_in = token_data.get('expires_in', 3600)
                self.token_expiry = datetime.now() + timedelta(seconds=expires_in - 300) # 5 min buffer
                return True
            elif response.status_code == 429:
                raise RateLimitExceeded()
            else:
                logger.error(f"Auth failed: {response.status_code} {response.text}")
                return False
        except Exception as e:
            logger.error(f"❌ Authentication error: {e}")
            return False

    def _ensure_authenticated(self):
        if not self.access_token or datetime.now() >= self.token_expiry:
            return self.authenticate()
        return True

    def search_part(self, part_number):
        if not self._ensure_authenticated():
            return {'error': 'authentication_failed', 'message': 'Failed to authenticate'}
        
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'X-DIGIKEY-Client-Id': self.api_key['CLIENT_ID'],
            'X-DIGIKEY-Locale-Site': 'US',
            'Content-Type': 'application/json'
        }
        
        search_data = {
            "Keywords": part_number,
            "RecordCount": 10,
            "RecordStartPosition": 0,
            "Filters": {}
        }
        
        try:
            response = requests.post(
                "https://api.digikey.com/products/v4/search/keyword", 
                json=search_data,
                headers=headers,
                timeout=30
            )
            if response.status_code == 200:
                data = response.json()
                products = data.get('Products', [])
                if products:
                    best_match = self._find_best_match(part_number, products)
                    if best_match and best_match.get('DatasheetUrl'):
                        return {
                            'datasheet_url': best_match['DatasheetUrl'],
                            'product_info': best_match
                        }
                return {'datasheet_url': None}
            elif response.status_code == 404:
                return {'datasheet_url': None}
            elif response.status_code == 429:
                raise RateLimitExceeded()
            else:
                return {'error': 'api_error', 'message': f'HTTP {response.status_code}'}
        except Exception as e:
            return {'error': 'request_failed', 'message': str(e)}

    def _find_best_match(self, original_part, products):
        # Exact match first
        for product in products:
            mpn = product.get('ManufacturerPartNumber', '')
            if mpn.upper() == original_part.upper():
                return product
        
        # Partial match
        for product in products:
            mpn = product.get('ManufacturerPartNumber', '')
            if original_part.upper() in mpn.upper() or mpn.upper() in original_part.upper():
                return product
        
        # Active products with datasheets
        for product in products:
            if product.get('ProductStatus') == 'Active' and product.get('DatasheetUrl'):
                return product
        
        return products[0] if products else None


class DownloadWorker:
    def __init__(self, download_queue, results_queue, progress, worker_id):
        self.download_queue = download_queue
        self.results_queue = results_queue
        self.progress = progress
        self.worker_id = worker_id
        self.is_running = False
        self.thread = None

    def start(self):
        if not self.is_running:
            self.is_running = True
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()

    def stop(self):
        self.is_running = False
        if self.thread:
            self.thread.join(timeout=5)

    def _run(self):
        while self.is_running:
            try:
                try:
                    # Get download task with timeout
                    task = self.download_queue.get(timeout=1.0)
                    
                    internal_pn = task['internal_pn']
                    manufacturer_pn = task['manufacturer_pn']
                    manufacturer = task['manufacturer']
                    datasheet_url = task['datasheet_url']
                    
                    if self.progress:
                        self.progress.update_worker_status(self.worker_id, "⬇️ Downloading", manufacturer_pn)
                    
                    # Create filename
                    safe_filename = "".join(c for c in manufacturer_pn if c.isalnum() or c in (' ', '-', '_')).rstrip()
                    filepath = os.path.join("datasheets", f"{safe_filename}.pdf")
                    
                    # Create directory if needed
                    os.makedirs("datasheets", exist_ok=True)
                    
                    # Check if file already exists
                    if os.path.exists(filepath):
                        msg = f"File already exists for {manufacturer_pn}, skipping"
                        logger.info(msg)  # Changed from warning to info
                        result = {
                            'internal_pn': internal_pn,
                            'manufacturer_pn': manufacturer_pn,
                            'manufacturer': manufacturer,
                            'status': 'skipped',
                            'message': 'File already exists',
                            'datasheet_url': datasheet_url,
                            'file_path': filepath
                        }
                        self.results_queue.put(result)
                        self.download_queue.task_done()
                        if self.progress:
                            self.progress.update_worker_status(self.worker_id, "⏭️ Skipped", "")
                        continue
                    
                    # Download the file
                    success, message = download_pdf_with_requests(datasheet_url, filepath, internal_pn)
                    
                    # Log failed download
                    if not success:
                        logger.error(f"Download failed for {manufacturer_pn}: {message}")
                    
                    # Put result
                    result = {
                        'internal_pn': internal_pn,
                        'manufacturer_pn': manufacturer_pn,
                        'manufacturer': manufacturer,
                        'status': 'success' if success else 'download_failed',
                        'message': message,
                        'datasheet_url': datasheet_url,
                        'file_path': filepath if success else ''
                    }
                    
                    self.results_queue.put(result)
                    self.download_queue.task_done()
                    
                    if self.progress:
                        status = "✅ Downloaded" if success else "❌ Failed"
                        self.progress.update_worker_status(self.worker_id, status, "")
                    
                except queue.Empty:
                    # No downloads to process
                    if self.progress:
                        self.progress.update_worker_status(self.worker_id, "⚪ Waiting", "")
                    continue
                    
            except Exception as e:
                logger.error(f"Download Worker error: {e}")
                if self.progress:
                    self.progress.update_worker_status(self.worker_id, "❌ Error", str(e)[:20])
                
        if self.progress:
            self.progress.update_worker_status(self.worker_id, "⚪ Idle", "")


# ... rest of the functions remain the same (MaxLevelFilter, load_api_keys, etc.)

class MaxLevelFilter(logging.Filter):
    def __init__(self, level):
        self.level = level

    def filter(self, record):
        return record.levelno < self.level


def load_api_keys():
    try:
        with open('api_keys.json', 'r') as f:
            config = json.load(f)
            return config.get('api_keys', [])
    except Exception:
        return []


def resolve_ti_redirect(url):
    import urllib.parse
    if url and url.startswith("https://www.ti.com/general/docs/suppproductinfo.tsp?"):
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)
        goto_urls = params.get('gotoUrl', [])
        if goto_urls:
            goto_url = urllib.parse.unquote(goto_urls[0])
            part_match = goto_url.rstrip('/').split('/')[-1]
            if part_match:
                return f"https://www.ti.com/lit/ds/symlink/{part_match}.pdf"
    return url


def download_pdf_with_requests(url, filepath, internal_pn=None):
    url = resolve_ti_redirect(url)
    # Reformat URLs starting with //mm to https:/mm
    if url.startswith('//mm'):
        url = 'https:/' + url[1:]
    
    if not url.lower().endswith('.pdf'):
        time.sleep(5)
    
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.6422.112 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5_2) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
    ]
    
    user_agent = random.choice(user_agents)
    headers = {
        'User-Agent': user_agent,
        'Accept': 'application/pdf,*/*',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    
    session = requests.Session()
    session.headers.update(headers)
    
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = session.get(url, timeout=30, allow_redirects=True, stream=True, verify=True)
            
            if response.status_code == 200:
                content = b''
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        content += chunk
                
                if len(content) > 1000 and content.startswith(b'%PDF'):
                    with open(filepath, 'wb') as f:
                        f.write(content)
                    return True, "Downloaded successfully"
                else:
                    return False, "Invalid PDF content"
            else:
                if attempt < MAX_ATTEMPTS - 1:
                    time.sleep(1)
                    continue
                return False, f"HTTP {response.status_code}"
                
        except Exception as e:
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(1)
                continue
            return False, f"Download error: {str(e)}"
    
    return False, "Failed after all attempts"


def run_downloader(csv_file, status_callback=None, progress_callback=None, config=None, results_callback=None, worker_callback=None, should_stop=None):
    from datetime import datetime
    global MAX_WORKERS, MAX_API_WORKERS, REQUESTS_PER_MINUTE, MAX_ATTEMPTS
    # Prepare reports folder
    os.makedirs("reports", exist_ok=True)
    # Timestamp for this run
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Set up logging if enabled - BEFORE workers start
    log_file = None
    cfg = config or {}
    if cfg.get("LOGGING", True):
        log_file = os.path.join("reports", f"report_{timestamp}.log")
        logging.basicConfig(
            filename=log_file,
            filemode='w',
            format='%(asctime)s [%(levelname)s] %(message)s',
            level=logging.WARNING
        )
        logger.setLevel(logging.WARNING)
        if status_callback:
            status_callback(f"📝 Logging warnings/errors to {log_file}\n")

    MAX_WORKERS = cfg.get("MAX_WORKERS", 5)
    MAX_API_WORKERS = cfg.get("MAX_API_WORKERS", 1)
    REQUESTS_PER_MINUTE = cfg.get("REQUESTS_PER_MINUTE", 120)
    MAX_ATTEMPTS = cfg.get("MAX_ATTEMPTS", 3)
    
    api_keys = load_api_keys()
    if not api_keys:
        if status_callback:
            status_callback("❌ No API keys found! Please configure api_keys.json\n")
        return

    # Load parts from CSV
    parts = []
    try:
        with open(csv_file, 'r') as f:
            reader = csv.reader(f)
            next(reader)  # Skip header
            for row in reader:
                if len(row) >= 3:
                    internal_pn, manufacturer, manufacturer_pn = row[0], row[1], row[2]
                    parts.append((internal_pn, manufacturer_pn, manufacturer))
    except Exception as e:
        if status_callback:
            status_callback(f"❌ Error loading parts: {e}\n")
        return

    if not parts:
        if status_callback:
            status_callback(f"❌ No parts to process in {csv_file}!\n")
        return

    parts_queue = queue.Queue()
    download_queue = queue.Queue()
    results_queue = queue.Queue()
    
    for part in parts:
        parts_queue.put(part)

    completed = 0
    total = len(parts)

    class ProgressTracker:
        def __init__(self, worker_count, api_worker_count):
            self.worker_status = {i: "Idle" for i in range(worker_count)}
            self.api_worker_status = {i: "Idle" for i in range(api_worker_count)}

        def update_worker_status(self, worker_id, status, details):
            if worker_id.startswith("DL-Worker-"):
                idx = int(worker_id.split("-")[-1]) - 1
                self.worker_status[idx] = status if details == "" else f"{status} {details}"
            elif worker_id.startswith("API-Worker-"):
                idx = int(worker_id.split("-")[-1]) - 1
                self.api_worker_status[idx] = status if details == "" else f"{status} {details}"

        def get_all_status(self):
            merged = {}
            offset = 0
            for i in range(len(self.api_worker_status)):
                merged[offset + i] = self.api_worker_status[i]
            offset += len(self.api_worker_status)
            for i in range(len(self.worker_status)):
                merged[offset + i] = self.worker_status[i]
            return dict(merged)

    progress_tracker = ProgressTracker(MAX_WORKERS, MAX_API_WORKERS)
    
    api_workers = []
    download_workers = []
    
    for i in range(MAX_API_WORKERS):
        worker = APIWorker(
            downloader=None,
            download_queue=download_queue,
            results_queue=results_queue,
            progress=progress_tracker,
            api_key=api_keys[i % len(api_keys)],
            worker_id=f"API-Worker-{i+1}"
        )
        worker.set_parts_queue(parts_queue)
        api_workers.append(worker)

    for i in range(MAX_WORKERS):
        worker = DownloadWorker(
            download_queue=download_queue,
            results_queue=results_queue,
            progress=progress_tracker,
            worker_id=f"DL-Worker-{i+1}"
        )
        download_workers.append(worker)

    for worker in api_workers:
        worker.start()
    for worker in download_workers:
        worker.start()

    if status_callback:
        status_callback(f"🚀 Starting datasheet downloader...\n")
        status_callback(f"⚙️  Settings: {MAX_API_WORKERS} API workers + {MAX_WORKERS} download workers\n")

    results = []
    result_counts = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    
    try:
        last_worker_update = time.time()
        while completed < total:
            now = time.time()
            try:
                if should_stop is not None and callable(should_stop) and should_stop():
                    raise KeyboardInterrupt
                
                result = results_queue.get(timeout=0.2)
                results.append(result)
                completed += 1
                
                status = result.get('status', '')
                if result.get('skipped') or status == 'success':
                    result_counts[0] += 1
                elif status == 'not_found':
                    result_counts[3] += 1
                elif status == 'no_datasheet':
                    result_counts[2] += 1
                elif status == 'download_failed':
                    result_counts[4] += 1
                elif status == 'error':
                    result_counts[5] += 1
                else:
                    result_counts[1] += 1

                if results_callback:
                    results_callback(result_counts.copy())

                if progress_callback:
                    progress_callback(completed, total)

                if worker_callback and (now - last_worker_update > 0.5):
                    worker_callback(progress_tracker.get_all_status())
                    last_worker_update = now

                if status_callback:
                    part_name = result.get('manufacturer_pn', 'Unknown')
                    status_callback(f"Processed: {part_name} - {status}\n")

            except queue.Empty:
                if worker_callback and (now - last_worker_update > 0.5):
                    worker_callback(progress_tracker.get_all_status())
                    last_worker_update = now

    except KeyboardInterrupt:
        if status_callback:
            status_callback("⏹️  Shutdown requested...\n")
        for worker in api_workers:
            worker.is_running = False
        for worker in download_workers:
            worker.is_running = False

    except Exception as e:
        if status_callback:
            status_callback(f"❌ Error: {e}\n")

        # Clean shutdown
    for worker in api_workers:
        worker.stop()
    for worker in download_workers:
        worker.stop()

    # Ensure reports directory exists
    os.makedirs("reports", exist_ok=True)

    # Save final results to CSV
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_file = os.path.join("reports", f"report_{timestamp}.csv")
    try:
        # Sort results by status - define custom order
        status_order = {
            'success': 0,      # Downloaded successfully
            'skipped': 1,      # Already exists
            'no_datasheet': 2, # No datasheet available
            'not_found': 3,    # Part not found
            'download_failed': 4, # Download failed
            'error': 5         # API or other errors
        }
        
        # Sort the results list
        sorted_results = sorted(results, key=lambda x: status_order.get(x.get('status', ''), 999))
        
        with open(report_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "internal_pn", "manufacturer_pn", "manufacturer",
                "status", "message", "datasheet_url", "file_path"
            ])
            writer.writeheader()
            writer.writerows(sorted_results)  # Write sorted results instead
        if status_callback:
            status_callback(f"💾 Results saved to {report_file}\n")
    except Exception as e:
        if status_callback:
            status_callback(f"❌ Failed to save results: {e}\n")

    if status_callback:
        status_callback("✅ Complete!\n")
