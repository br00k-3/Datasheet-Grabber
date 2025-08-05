import requests, os, json, time, random, threading, sys, difflib, csv, signal, queue, logging, shutil
from urllib.parse import quote, urlparse
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

#CONFIGURATION
MAX_WORKERS = 5
MAX_API_WORKERS = 1
REQUESTS_PER_MINUTE = 120
TIMEOUT_SECONDS = 30
RESUME_ON_RESTART = True  

logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


def load_api_keys():
    """Load API keys from external file"""
    try:
        with open('api_keys.json', 'r') as f:
            config = json.load(f)
            return config.get('api_keys', [])
    except FileNotFoundError:
        logger.error("❌ api_keys.json not found!")
        return []
    except Exception as e:
        logger.error(f"❌ Error loading API keys: {e}")
        return []

API_KEYS = load_api_keys()

def download_pdf_with_requests(url, filepath):
    """Download PDF with 403 error handling - matching main branch approach"""
    
    # Random user agents for 403 protection
    user_agents = [
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2.1 Safari/605.1.15',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0'
    ]
    
    headers = {
        'User-Agent': random.choice(user_agents),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1'
    }

    max_attempts = 3

    # Fix protocol-relative URLs (starting with //)
    if url.startswith('//'):
        url = 'https:' + url
    # URL encode spaces
    url = url.replace(' ', '%20')

    for attempt in range(max_attempts):
        try:
            # Add random delay between attempts
            if attempt > 0:
                time.sleep(random.uniform(1.0, 3.0))
            
            # Make request with timeout and redirects
            response = requests.get(url, headers=headers, timeout=30, allow_redirects=True, stream=True)
            
            # Handle 403 errors with exponential backoff
            if response.status_code == 403:
                if attempt < max_attempts - 1:
                    wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                    time.sleep(wait_time)
                    continue
                return False, "HTTP 403 Forbidden - Access denied after retries"
            
            # Handle rate limiting
            elif response.status_code == 429:
                if attempt < max_attempts - 1:
                    time.sleep(5)
                    continue
                return False, "HTTP 429 - Rate limited"
            
            elif response.status_code == 200:
                # Download the content
                content = b''
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        content += chunk
                
                # Verify it's a PDF and has reasonable size
                if len(content) > 1000 and content.startswith(b'%PDF'):
                    with open(filepath, 'wb') as f:
                        f.write(content)
                    return True, "Downloaded successfully"
                elif len(content) <= 1000:
                    return False, "PDF content too small"
                elif not content.startswith(b'%PDF'):
                    return False, "Response not a valid PDF"
                else:
                    return False, "Unknown content issue"
            else:
                return False, f"HTTP {response.status_code}"
                
        except requests.exceptions.Timeout:
            if attempt < max_attempts - 1:
                continue
            return False, "Request timeout after retries"
        except requests.exceptions.ConnectionError:
            if attempt < max_attempts - 1:
                continue
            return False, "Connection error after retries"
        except requests.exceptions.RequestException as e:
            if attempt < max_attempts - 1:
                continue
            return False, f"Request error: {str(e)}"
        except Exception as e:
            return False, f"Download error: {str(e)}"
    
    return False, "Failed after all retry attempts"

class DownloadJob:
    
    def __init__(self, internal_pn, manufacturer_pn, manufacturer_name, datasheet_url, 
                 found_part, manufacturer_found, digikey_pn):
        self.internal_pn = internal_pn
        self.manufacturer_pn = manufacturer_pn
        self.manufacturer_name = manufacturer_name
        self.datasheet_url = datasheet_url
        self.found_part = found_part
        self.manufacturer_found = manufacturer_found
        self.digikey_pn = digikey_pn
        self.filename = (f"{internal_pn} {manufacturer_pn}") + ".pdf"

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
                try:
                    part_info = self.parts_queue.get(timeout=1)
                except queue.Empty:
                    continue
                
                if part_info is None:
                    break
                
                internal_pn, manufacturer_pn, manufacturer_name = part_info
                
                if self.progress:
                    self.progress.update_worker_status(self.worker_id, "🔍", f"{internal_pn}")
                
                # Check if file already exists
                filename = f"{internal_pn} {manufacturer_pn}.pdf"
                filepath = os.path.join("datasheets", filename)
                if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                    result = {
                        'status': 'success',
                        'internal_pn': internal_pn,
                        'manufacturer_pn': manufacturer_pn,
                        'found_part': manufacturer_pn,
                        'manufacturer': 'Unknown',
                        'filename': filename,
                        'skipped': True
                    }
                    self.results_queue.put(result)
                    self.parts_queue.task_done()
                    continue
                
                # Search for part
                product = self.search_part(manufacturer_pn)
                
                if not product:
                    result = {
                        'status': 'not_found',
                        'internal_pn': internal_pn,
                        'manufacturer_pn': manufacturer_pn
                    }
                    self.results_queue.put(result)
                elif product.get('error'):
                    result = {
                        'status': 'error',
                        'internal_pn': internal_pn,
                        'manufacturer_pn': manufacturer_pn,
                        'error': product.get('message', 'Unknown error')
                    }
                    self.results_queue.put(result)
                else:
                    datasheet_url = product.get('DatasheetUrl')
                    if not datasheet_url:
                        result = {
                            'status': 'no_datasheet',
                            'internal_pn': internal_pn,
                            'manufacturer_pn': manufacturer_pn,
                            'found_part': product.get('ManufacturerPartNumber', 'Unknown')
                        }
                        self.results_queue.put(result)
                    else:
                        manufacturer_info = product.get('Manufacturer', {})
                        manufacturer_found = manufacturer_info.get('Name', 'Unknown') if isinstance(manufacturer_info, dict) else 'Unknown'
                        
                        download_job = DownloadJob(
                            internal_pn=internal_pn,
                            manufacturer_pn=manufacturer_pn,
                            manufacturer_name=manufacturer_name,
                            datasheet_url=datasheet_url,
                            found_part=product.get('ManufacturerPartNumber', manufacturer_pn),
                            manufacturer_found=manufacturer_found,
                            digikey_pn=product.get('DigiKeyPartNumber', '')
                        )
                        
                        self.download_queue.put(download_job)
                
                self.parts_queue.task_done()
                
            except Exception as e:
                if 'part_info' in locals():
                    result = {
                        'status': 'error',
                        'internal_pn': part_info[0] if part_info else 'unknown',
                        'manufacturer_pn': part_info[1] if part_info else 'unknown',
                        'error': f'API worker error: {str(e)}'
                    }
                    self.results_queue.put(result)
                    try:
                        self.parts_queue.task_done()
                    except:
                        pass
        
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
                expires_in = token_data.get('expires_in', 1800)
                self.token_expiry = datetime.now() + timedelta(seconds=expires_in - 60)
                return True
            elif response.status_code == 429:
                logger.error("❌ RATE LIMIT EXCEEDED!")
                sys.exit(1)
            else:
                logger.error(f"❌ Authentication failed: HTTP {response.status_code}")
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
        
        # Use keyword search API for better results
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
                result = response.json()
                products = result.get('Products', [])
                
                if products:
                    # Find best match (simple version)
                    best_match = self._find_best_match(part_number, products)
                    return best_match
                else:
                    return None
            elif response.status_code == 404:
                return None
            elif response.status_code == 429:
                logger.error("❌ RATE LIMIT EXCEEDED!")
                sys.exit(1)
            else:
                return {'error': 'api_error', 'message': f'API returned {response.status_code}'}
                
        except Exception as e:
            return {'error': 'request_failed', 'message': str(e)}
    
    def _find_best_match(self, original_part, products):
        """Find the best matching product from search results"""
        # First, try exact match
        for product in products:
            mpn = product.get('ManufacturerPartNumber', '')
            if mpn.upper() == original_part.upper():
                return product
        
        # Then try partial match
        for product in products:
            mpn = product.get('ManufacturerPartNumber', '')
            if original_part.upper() in mpn.upper() or mpn.upper() in original_part.upper():
                return product
        
        # Return first active product with datasheet
        for product in products:
            if product.get('ProductStatus') == 'Active' and product.get('DatasheetUrl'):
                return product
        
        # Fall back to first product
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
                    job = self.download_queue.get(timeout=1)
                except queue.Empty:
                    continue
                
                if job is None:
                    break
                
                if self.progress:
                    self.progress.update_worker_status(self.worker_id, "📥", f"{job.internal_pn}")
                
                # Create filepath
                os.makedirs("datasheets", exist_ok=True)
                filepath = os.path.join("datasheets", job.filename)
                
                # Download with requests
                success, message = download_pdf_with_requests(job.datasheet_url, filepath)
                
                if success:
                    result = {
                        'status': 'success',
                        'internal_pn': job.internal_pn,
                        'manufacturer_pn': job.manufacturer_pn,
                        'found_part': job.found_part,
                        'manufacturer': job.manufacturer_found,
                        'filename': job.filename,
                        'url': job.datasheet_url
                    }
                else:
                    result = {
                        'status': 'download_failed',
                        'internal_pn': job.internal_pn,
                        'manufacturer_pn': job.manufacturer_pn,
                        'found_part': job.found_part,
                        'manufacturer': job.manufacturer_found,
                        'url': job.datasheet_url,
                        'error': message
                    }
                
                self.results_queue.put(result)
                self.download_queue.task_done()
                
            except Exception as e:
                if 'job' in locals():
                    result = {
                        'status': 'download_failed',
                        'internal_pn': job.internal_pn if hasattr(job, 'internal_pn') else 'unknown',
                        'manufacturer_pn': job.manufacturer_pn if hasattr(job, 'manufacturer_pn') else 'unknown',
                        'error': f'Download worker error: {str(e)}'
                    }
                    self.results_queue.put(result)
                    try:
                        self.download_queue.task_done()
                    except:
                        pass
        
        if self.progress:
            self.progress.update_worker_status(self.worker_id, "⚪ Idle", "")



class ProgressDisplay:
    def __init__(self, total_parts):
        self.total_parts = total_parts
        self.completed = 0
        self.workers = {}
        self.results = {
            "success": 0,
            "not_found": 0,
            "no_datasheet": 0,
            "download_failed": 0,
            "error": 0,
            "skipped": 0
        }
        self.start_time = time.time()
        self.lock = threading.Lock()
    
    def update_worker_status(self, worker_id, status, part_info):
        with self.lock:
            self.workers[worker_id] = f"{status} {part_info}".strip()
    
    def update_progress(self, result):
        with self.lock:
            self.completed += 1
            
            # Count result types
            status = result.get("status", "unknown")
            if result.get("skipped"):
                self.results["skipped"] += 1
            elif status in self.results:
                self.results[status] += 1
            else:
                self.results["error"] += 1
    
    def display(self):
        with self.lock:
            # Calculate progress
            percent = (self.completed / self.total_parts * 100) if self.total_parts > 0 else 0
            elapsed = time.time() - self.start_time
            
            # Estimate time remaining
            if self.completed > 0:
                eta_seconds = (elapsed / self.completed) * (self.total_parts - self.completed)
                eta = f"{int(eta_seconds//60)}:{int(eta_seconds%60):02d}"
            else:
                eta = "Unknown"
            
            # Progress bar
            bar_width = 30
            filled = int(bar_width * percent / 100)
            bar = "█" * filled + "░" * (bar_width - filled)
            
            # Clear screen and display
            logger.info("\033[H\033[J")  # Clear screen
            logger.info(f"🚀 Progress:")
            logger.info("=" * 50)
            logger.info(f"{bar} {self.completed}/{self.total_parts} ({percent:.1f}%)")
            logger.info(f"Elapsed: {int(elapsed//60)}:{int(elapsed%60):02d} | ETA: {eta}")
            logger.info("")
            
            # Results summary
            logger.info(f"  ✅ Downloaded: {self.results['success']}")
            logger.info(f"  ⏭️  Skipped: {self.results['skipped']}")
            logger.info(f"  ⚠️  No datasheet: {self.results['no_datasheet']}")
            logger.info(f"  ❌ Not found: {self.results['not_found']}")
            logger.info(f"  ⚠️  Download failed: {self.results['download_failed']}")
            logger.info(f"  ❌ Errors: {self.results['error']}")
            logger.info("")
            
            # Worker status
            logger.info("Workers:")
            logger.info("=" * 50)
            for worker_id, status in self.workers.items():
                logger.info(f"  {worker_id}: {status}")
            
            logger.info("")
    
    def final_summary(self):
        elapsed = time.time() - self.start_time
        logger.info("\n" + "=" * 50)
        logger.info("📊 FINAL SUMMARY")
        logger.info("=" * 50)
        logger.info(f"✅ Downloaded: {self.results['success']}")
        logger.info(f"⏭️  Skipped: {self.results['skipped']}")
        logger.info(f"⚠️  No datasheet: {self.results['no_datasheet']}")
        logger.info(f"❌ Not found: {self.results['not_found']}")
        logger.info(f"⚠️  Download failed: {self.results['download_failed']}")
        logger.info(f"❌ Errors: {self.results['error']}")
        logger.info(f"⏱️  Total time: {int(elapsed//60)}:{int(elapsed%60):02d}")
        logger.info("=" * 50)

def load_parts_from_csv(filename):
    """Load parts from CSV file"""
    parts = []
    try:
        with open(filename, 'r') as f:
            reader = csv.reader(f)
            next(reader)  # Skip header
            for row in reader:
                if len(row) >= 3:
                    internal_pn, manufacturer, manufacturer_pn = row[0], row[1], row[2]
                    parts.append((internal_pn, manufacturer_pn, manufacturer))
        logger.info(f"📋 Loaded {len(parts)} parts from {filename}")
        return parts
    except FileNotFoundError:
        logger.error(f"❌ File {filename} not found!")
        return []
    except Exception as e:
        logger.error(f"❌ Error loading parts: {e}")
        return []

def save_results_to_csv(results, filename="datasheets/report.csv"):
    """Save results to CSV report, organized by status then internal P/N"""
    os.makedirs("datasheets", exist_ok=True)
    
    # Define status priority for sorting (successful results first)
    status_priority = {
        'success': 1,
        'skipped': 2,
        'no_datasheet': 3,
        'not_found': 4,
        'download_failed': 5,
        'error': 6,
        'unknown': 7
    }
    
    # Sort results by status priority, then by internal P/N
    def sort_key(result):
        status = result.get('status', 'unknown')
        # Handle skipped items as success
        if result.get('skipped'):
            status = 'success'
        priority = status_priority.get(status, 7)
        internal_pn = result.get('internal_pn', '').upper()  # Case-insensitive sort
        return (priority, internal_pn)
    
    sorted_results = sorted(results, key=sort_key)
    
    with open(filename, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["Status", "Internal P/N", "Manufacturer P/N", "Found Part", "Manufacturer", "Filename/URL", "Notes"])
        
        for result in sorted_results:
            status = result.get('status', 'unknown')
            if result.get('skipped'):
                status_text = 'Success'
                notes = 'Downloaded'
            elif status == 'success':
                status_text = 'Success'
                notes = 'Downloaded'
            elif status == 'not_found':
                status_text = 'Not Found'
                notes = 'Part not found in DigiKey'
            elif status == 'no_datasheet':
                status_text = 'No Datasheet'
                notes = 'No datasheet available'
            elif status == 'download_failed':
                status_text = 'Download Failed'
                notes = result.get('error', 'Download failed')
                # Clean up error message - remove extra newlines and normalize whitespace
                notes = ' '.join(notes.split())
            else:
                status_text = 'Error'
                notes = result.get('error', 'Unknown error')
                # Clean up error message - remove extra newlines and normalize whitespace
                notes = ' '.join(notes.split())
            
            # Ensure URLs and filenames have spaces replaced with %20 for consistency
            filename_or_url = result.get('filename', result.get('url', ''))
            if filename_or_url:
                filename_or_url = filename_or_url.replace(' ', '%20')
            writer.writerow([
                status_text,
                result.get('internal_pn', ''),
                result.get('manufacturer_pn', ''),
                result.get('found_part', ''),
                result.get('manufacturer', ''),
                filename_or_url,
                notes
            ])
    
    logger.info(f"📄 Report saved: {filename}")

def main():
    """Main function to run the datasheet downloader"""
    if len(sys.argv) < 2:
        logger.error("❌ Usage: python script_new.py <parts_file.csv>")
        sys.exit(1)
    
    parts_file = sys.argv[1]
    
    # Check API keys
    if not API_KEYS:
        logger.error("❌ No API keys found! Please configure api_keys.json")
        sys.exit(1)
    
    # Load parts
    parts = load_parts_from_csv(parts_file)
    if not parts:
        logger.error("❌ No parts to process!")
        sys.exit(1)
    
    # Setup queues
    parts_queue = queue.Queue()
    download_queue = queue.Queue()
    results_queue = queue.Queue()
    
    # Add parts to queue
    for part in parts:
        parts_queue.put(part)
    
    # Setup progress display
    progress = ProgressDisplay(len(parts))
    
    # Setup workers
    api_workers = []
    download_workers = []
    
    # Create API workers
    for i in range(MAX_API_WORKERS):
        worker = APIWorker(
            downloader=None,
            download_queue=download_queue,
            results_queue=results_queue,
            progress=progress,
            api_key=API_KEYS[i % len(API_KEYS)],
            worker_id=f"API-Worker-{i+1}"
        )
        worker.set_parts_queue(parts_queue)
        api_workers.append(worker)
    
    # Create download workers
    for i in range(MAX_WORKERS):
        worker = DownloadWorker(
            download_queue=download_queue,
            results_queue=results_queue,
            progress=progress,
            worker_id=f"DL-Worker-{i+1}"
        )
        download_workers.append(worker)
    
    # Start workers
    logger.info("🚀 Starting datasheet downloader...")
    logger.info(f"⚙️  Settings: {MAX_API_WORKERS} API workers + {MAX_WORKERS} download workers")
    
    for worker in api_workers:
        worker.start()
    
    for worker in download_workers:
        worker.start()
    
    # Collect results
    results = []
    completed = 0
    
    try:
        while completed < len(parts):
            try:
                result = results_queue.get(timeout=1)
                results.append(result)
                completed += 1
                progress.update_progress(result)
                progress.display()
            except queue.Empty:
                continue
            except KeyboardInterrupt:
                logger.info("⏹️  Shutdown requested...")
                break
    
    except KeyboardInterrupt:
        logger.info("⏹️  Shutdown requested...")
    
    # Stop workers
    for worker in api_workers:
        worker.stop()
    
    for worker in download_workers:
        worker.stop()
    
    # Final summary
    progress.final_summary()
    
    # Save results
    save_results_to_csv(results)
    
    logger.info("✅ Complete!")

if __name__ == "__main__":
    main()
