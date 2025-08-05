import requests
import os
import json
import time
import random
import threading
import sys
import difflib
import csv
import concurrent.futures
import signal
import socket
import re
import queue
import logging
from urllib.parse import quote
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

MAX_WORKERS = 5
MAX_API_WORKERS = 2
REQUESTS_PER_MINUTE = 120
TIMEOUT_SECONDS = 30
RESUME_ON_RESTART = True

shutdown_flag = threading.Event()

def sanitize_filename(filename):
    """Sanitize filename by replacing problematic characters for Windows/Unix filesystems"""
    invalid_chars = ['/', '\\', ':', '*', '?', '"', '<', '>', '|']
    
    sanitized = filename
    for char in invalid_chars:
        sanitized = sanitized.replace(char, '_')
    
    sanitized = sanitized.rstrip('. ')
    
    if len(sanitized) > 250:
        sanitized = sanitized[:250]
    
    return sanitized

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

if not API_KEYS:
    logger.error("❌ No API keys available")
    exit(1)


def signal_handler(signum, frame):
    """Handle Ctrl+C gracefully"""
    if shutdown_flag.is_set():
        logger.warning("\n⚠️  Force terminating...")
        os._exit(1)
    
    logger.info("\n⏹️  Shutdown requested...")
    shutdown_flag.set()

signal.signal(signal.SIGINT, signal_handler)

class ProgressBar:
    """Simple progress bar with compact worker status"""
    
    def __init__(self, total, max_workers=1):
        self.total = total
        self.current = 0
        self.start_time = time.time()
        self.lock = threading.Lock()
        self.max_workers = max_workers
        self.worker_status = {}
        self.last_line_count = 0  # Track number of lines printed
        self.thread_to_worker = {}  # Map thread IDs to worker numbers
        self.next_worker_id = 1
        self.refresh_interval = 0.5  # Minimum seconds between display updates
        
    def get_worker_id(self):
        """Get consistent worker ID for current thread"""
        with self.lock:
            thread_id = threading.current_thread().ident
            if thread_id not in self.thread_to_worker:
                self.thread_to_worker[thread_id] = f"Worker-{self.next_worker_id}"
                self.next_worker_id += 1
            return self.thread_to_worker[thread_id]
        
    def update_worker_status(self, worker_id, status, part_name=""):
        with self.lock:
            self.worker_status[worker_id] = {
                'status': status,
                'part': part_name,
                'timestamp': time.time()
            }
    
    def update(self, status="Processing..."):
        with self.lock:
            self.current += 1
            self._refresh_display(status)
            
            if self.current >= self.total:
                elapsed = time.time() - self.start_time
                total_time = f"{int(elapsed//60)}:{int(elapsed%60):02d}"
                print(f'\n✅ Completed in {total_time}')
    
    def force_refresh(self):
        with self.lock:
            self._refresh_display()
    
    def _refresh_display(self, main_status="Processing..."):
        """Refresh display with each worker on its own line"""
        # Update last refresh time
        self.last_refresh = time.time()
        
        # Calculate progress
        percent = (self.current / self.total) * 100 if self.total > 0 else 0
        elapsed = time.time() - self.start_time
        
        bar_length = 30
        filled = int(bar_length * self.current / self.total) if self.total > 0 else 0
        bar = '█' * filled + '░' * (bar_length - filled)
        
        if self.current > 0:
            rate = self.current / elapsed
            eta_seconds = (self.total - self.current) / rate if rate > 0 else 0
            eta = f"{int(eta_seconds//60)}:{int(eta_seconds%60):02d}"
        else:
            eta = "--:--"
        
        if hasattr(self, 'last_line_count'):
            print(f"\033[{self.last_line_count}A", end='')
            print("\033[J", end='')
        
        progress_line = f"[{bar}] {self.current}/{self.total} ({percent:.1f}%) | ETA: {eta} | {main_status}"
        print(progress_line)
        
        current_time = time.time()
        line_count = 1
        
        sorted_workers = sorted(self.worker_status.keys())
        
        for worker_id in sorted_workers:
            if worker_id in self.worker_status and self.worker_status[worker_id]:
                info = self.worker_status[worker_id]
                elapsed_worker = current_time - info['timestamp']
                
                # Format worker status with part info
                status_icon = "🔧"  # Default
                if "Searching" in info['status'] or "🔍" in info['status']:
                    status_icon = "🔍"
                elif "Downloading" in info['status'] or "📥" in info['status']:
                    status_icon = "📥"
                elif "Success" in info['status'] or "✅" in info['status']:
                    status_icon = "✅"
                elif "Not found" in info['status'] or "❌" in info['status']:
                    status_icon = "❌"
                elif "Error" in info['status'] or "⚠️" in info['status']:
                    status_icon = "⚠️"
                elif "No datasheet" in info['status']:
                    status_icon = "📄"
                elif "Stopped" in info['status'] or "Idle" in info['status'] or "⚪" in info['status']:
                    status_icon = "⚪"
                
                worker_line = f"  {worker_id}: {status_icon} "
                
                if info['part']:
                    part_display = info['part']
                    if len(part_display) > 20:
                        part_display = part_display[:17] + "..."
                    worker_line += f"{part_display}"
                else:
                    worker_line += "Idle"
                
                if elapsed_worker > 30:
                    worker_line += f" ({int(elapsed_worker)}s)"
                
                print(worker_line)
                line_count += 1
        
        active_worker_count = len([w for w in sorted_workers if w in self.worker_status and self.worker_status[w]])
        for i in range(active_worker_count, self.max_workers):
            print(f"  Worker-{i+1}: ⚪ Idle")
            line_count += 1
        
        self.last_line_count = line_count
        
        print("", end='', flush=True)


class SimpleRateLimiter:
    def __init__(self, requests_per_minute):
        self.requests_per_minute = requests_per_minute
        self.request_times = []
        self.lock = threading.Lock()
    
    def wait_if_needed(self):
        with self.lock:
            now = time.time()
            self.request_times = [t for t in self.request_times if now - t < 60]
            
            if len(self.request_times) >= self.requests_per_minute:
                oldest = min(self.request_times)
                wait_time = 60 - (now - oldest) + 1
                if wait_time > 0:
                    time.sleep(wait_time)
            
            self.request_times.append(now)

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
        self.filename = sanitize_filename(f"{internal_pn} {manufacturer_pn}") + ".pdf"


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
        
        self.rate_limiter = SimpleRateLimiter(REQUESTS_PER_MINUTE)
    
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
        while self.is_running and not shutdown_flag.is_set():
            try:
                try:
                    part_info = self.parts_queue.get(timeout=1)
                except queue.Empty:
                    continue
                
                if part_info is None:
                    break
                
                internal_pn, manufacturer_pn, manufacturer_name = part_info
                
                if self.progress:
                    self.progress.update_worker_status(self.worker_id, "🔍 Checking", f"{internal_pn}")
                
                if RESUME_ON_RESTART:
                    filename = sanitize_filename(f"{internal_pn} {manufacturer_pn}") + ".pdf"
                    filepath = os.path.join("datasheets", filename)
                    try:
                        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                            # File already exists, skip API call and report success
                            result = {
                                'status': 'success',
                                'internal_pn': internal_pn,
                                'manufacturer_pn': manufacturer_pn,
                                'found_part': manufacturer_pn,  # Use manufacturer PN as found part
                                'manufacturer': 'Unknown',      # We don't know manufacturer without API call
                                'digikey_pn': '',              # We don't have DigiKey PN without API call
                                'filename': filename,
                                'url': '',                     # We don't have URL without API call
                                'skipped': True
                            }
                            self.results_queue.put(result)
                            self.parts_queue.task_done()
                            
                            if self.progress:
                                self.progress.update_worker_status(self.worker_id, "✅ Exists", f"{internal_pn}")
                            continue
                    except (OSError, IOError):
                        # File system error checking file, proceed with API call
                        pass
                
                if self.progress:
                    self.progress.update_worker_status(self.worker_id, "🔍 API Search", f"{internal_pn}")
                
                # Add timeout protection for the entire part processing
                part_start_time = time.time()
                max_part_time = 45  # Maximum 45 seconds per part (including all API calls)
                
                try:
                    # Search for the part using API (with timeout) - use worker's own API key
                    product = self.search_part_with_key(manufacturer_pn, manufacturer_name)
                    
                    # Check if we exceeded time limit
                    if time.time() - part_start_time > max_part_time:
                        result = {
                            'status': 'error',
                            'internal_pn': internal_pn,
                            'manufacturer_pn': manufacturer_pn,
                            'error': 'Processing timeout - try reducing MAX_WORKERS'
                        }
                        self.results_queue.put(result)
                        self.parts_queue.task_done()
                        continue
                
                except Exception as search_error:
                    result = {
                        'status': 'error',
                        'internal_pn': internal_pn,
                        'manufacturer_pn': manufacturer_pn,
                        'error': f'Search failed: {str(search_error)}'
                    }
                    self.results_queue.put(result)
                    self.parts_queue.task_done()
                    continue
                
                if not product:
                    # Part not found
                    result = {
                        'status': 'not_found',
                        'internal_pn': internal_pn,
                        'manufacturer_pn': manufacturer_pn
                    }
                    self.results_queue.put(result)
                elif isinstance(product, dict) and product.get('error'):
                    # API error
                    result = {
                        'status': 'error',
                        'internal_pn': internal_pn,
                        'manufacturer_pn': manufacturer_pn,
                        'error': product.get('message', 'Unknown error')
                    }
                    self.results_queue.put(result)
                else:
                    # Part found - check for datasheet
                    datasheet_url = product.get('DatasheetUrl')
                    if not datasheet_url:
                        # No datasheet available
                        result = {
                            'status': 'no_datasheet',
                            'internal_pn': internal_pn,
                            'manufacturer_pn': manufacturer_pn,
                            'found_part': product.get('ManufacturerPartNumber', 'Unknown')
                        }
                        self.results_queue.put(result)
                    else:
                        # Has datasheet - create download job
                        manufacturer_info = product.get('Manufacturer', {})
                        if isinstance(manufacturer_info, dict):
                            manufacturer_found = manufacturer_info.get('Value', 'Unknown')
                        else:
                            manufacturer_found = str(manufacturer_info) if manufacturer_info else 'Unknown'
                        
                        download_job = DownloadJob(
                            internal_pn=internal_pn,
                            manufacturer_pn=manufacturer_pn,
                            manufacturer_name=manufacturer_name,
                            datasheet_url=datasheet_url,
                            found_part=product.get('ManufacturerPartNumber', manufacturer_pn),
                            manufacturer_found=manufacturer_found,
                            digikey_pn=product.get('DigiKeyPartNumber', '')
                        )
                        
                        # Queue for download
                        self.download_queue.put(download_job)
                        
                        # Don't put success result here - let download worker handle it
                        # This avoids duplicate entries in the results
                
                self.parts_queue.task_done()
                
            except Exception as e:
                # Handle unexpected errors
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
            self.progress.update_worker_status(self.worker_id, "⚪ Stopped", "")
    
    def set_parts_queue(self, parts_queue):
        """Set the parts queue for the API worker"""
        self.parts_queue = parts_queue
    
    def authenticate_worker(self, silent=False):
        """Authenticate this worker with its own API key"""
        data = {
            'client_id': self.api_key['CLIENT_ID'],
            'client_secret': self.api_key['CLIENT_SECRET'],
            'grant_type': 'client_credentials'
        }
        
        try:
            response = requests.post("https://api.digikey.com/v1/oauth2/token", data=data, timeout=30)
            
            if response.status_code == 429:
                if not silent:
                    logger.warning(f"⚠️  Rate limit hit during worker authentication ({self.worker_id}), waiting 60 seconds...")
                time.sleep(60)
                return False
            elif response.status_code == 200:
                token_data = response.json()
                self.access_token = token_data['access_token']
                expires_in = token_data.get('expires_in', 1800)
                self.token_expiry = datetime.now() + timedelta(seconds=expires_in - 60)
                return True
            else:
                return False
                
        except Exception as e:
            return False
    
    def _ensure_worker_authenticated(self):
        """Check if worker token is valid, refresh if needed"""
        if not hasattr(self, 'access_token') or not self.access_token or datetime.now() >= self.token_expiry:
            # Use silent mode when progress bar is active to avoid logging interference
            silent = hasattr(self, 'progress') and self.progress is not None
            return self.authenticate_worker(silent=silent)
        return True
    
    def search_part_with_key(self, part_number, manufacturer_name=None):
        """Search for a part using this worker's API key"""
        if not self._ensure_worker_authenticated():
            return {'error': 'authentication_failed', 'message': 'Failed to authenticate API worker'}
        
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'X-DIGIKEY-Client-Id': self.api_key['CLIENT_ID'],
            'X-DIGIKEY-Locale-Site': 'US',
        }
        
        # Use silent mode when progress bar is active to avoid logging interference
        silent = hasattr(self, 'progress') and self.progress is not None
        return self.downloader._search_part_with_headers(part_number, manufacturer_name, headers, self.rate_limiter, silent=silent)

class DigiKeyDownloader:
    
    def __init__(self):
        self.access_token = None
        self.token_expiry = None
        self.manufacturers = {}
        self.acquisition_mappings = {}
        self.acquisition_history = {}
        self.rate_limiter = SimpleRateLimiter(REQUESTS_PER_MINUTE)
        self.session_lock = threading.Lock()
        self.last_download_error = None  # Store detailed error messages for failed downloads
        
        # Load manufacturer database
        self._load_manufacturers()
        
        # User agents for rotation
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/120.0'
        ]
    
    def _load_manufacturers(self):
        """Load manufacturer database if available"""
        try:
            with open('manufacturers.json', 'r') as f:
                data = json.load(f)
                
                # Load basic manufacturer list
                for mfr in data.get('Manufacturers', []):
                    name = mfr.get('Name', '').upper()
                    self.manufacturers[name] = mfr.get('Id')
                
                # Load acquisition mappings
                self.acquisition_mappings = data.get('AcquisitionMappings', {})
                
                # Load acquisition history for reference
                self.acquisition_history = data.get('AcquisitionHistory', {})
                
                logger.info(f"📋 Loaded {len(self.manufacturers)} manufacturers with {len(self.acquisition_mappings)} acquisition mappings")
                
        except FileNotFoundError:
            logger.warning("⚠️  manufacturers.json not found - manufacturer filtering disabled")
            self.acquisition_mappings = {}
            self.acquisition_history = {}
        except Exception as e:
            logger.warning(f"⚠️  Error loading manufacturers: {e}")
            self.acquisition_mappings = {}
            self.acquisition_history = {}
    
    def _make_request(self, url, headers, params=None, stream=False):
        """Make HTTP request with proper error handling and SSL timeout protection"""
        # Build request kwargs with better timeout handling
        kwargs = {
            'url': url,
            'headers': headers,
            'timeout': (10, TIMEOUT_SECONDS),  # (connect_timeout, read_timeout)
            'stream': stream
        }
        
        # Add params if provided
        if params:
            kwargs['params'] = params
        
        # Create session with better SSL handling
        session = requests.Session()
        
        # Configure SSL to be more aggressive about timeouts
        adapter = requests.adapters.HTTPAdapter()
        session.mount('https://', adapter)
        session.mount('http://', adapter)
        
        # Set socket timeout to prevent SSL hangs
        old_socket_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(15)  # 15 second socket timeout
        
        try:
            response = session.get(**kwargs)
            return response
        finally:
            # Restore original socket timeout
            socket.setdefaulttimeout(old_socket_timeout)
            session.close()
    
    def _get_headers(self, domain=None):
        """Get realistic browser headers"""
        headers = {
            'User-Agent': random.choice(self.user_agents),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
        
        # Add domain-specific referers
        if domain:
            referers = {
                'onsemi.com': 'https://www.onsemi.com/',
                'st.com': 'https://www.st.com/',
                'ti.com': 'https://www.ti.com/',
                'analog.com': 'https://www.analog.com/',
            }
            for site, referer in referers.items():
                if site in domain:
                    headers['Referer'] = referer
                    break
        
        return headers
    
    def authenticate(self, silent=False):
        if not silent:
            logger.info("🔐 Authenticating with DigiKey...")
        
        api_key = API_KEYS[0]
        
        data = {
            'client_id': api_key['CLIENT_ID'],
            'client_secret': api_key['CLIENT_SECRET'],
            'grant_type': 'client_credentials'
        }
        
        try:
            response = requests.post("https://api.digikey.com/v1/oauth2/token", data=data, timeout=30)
            
            if response.status_code == 429:
                if not silent:
                    logger.warning("⚠️  Rate limit hit during authentication, waiting 60 seconds...")
                time.sleep(60)
                return False
            elif response.status_code == 200:
                token_data = response.json()
                self.access_token = token_data['access_token']
                expires_in = token_data.get('expires_in', 1800)
                self.token_expiry = datetime.now() + timedelta(seconds=expires_in - 60)
                
                if not silent:
                    logger.info("✅ Authentication successful!")
                return True
            else:
                if not silent:
                    logger.error(f"❌ Authentication failed: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Authentication error: {e}")
            return False
    
    def _ensure_authenticated(self):
        """Check if token is valid, refresh if needed (thread-safe)"""
        with self.session_lock:  # Serialize authentication to avoid multiple simultaneous auth attempts
            if not self.access_token or datetime.now() >= self.token_expiry:
                return self.authenticate()
            return True
    
    # ========================================================================
    # MANUFACTURER MATCHING METHODS
    def _find_manufacturer_variants(self, manufacturer_name):
        if not manufacturer_name or not self.manufacturers:
            return []
        
        name_upper = manufacturer_name.upper().strip()
        matches = []
        
        # 1. Handle acquisition mappings from JSON file
        search_terms = [name_upper]
        
        # Add mapped variations for the input name from the acquisition mappings
        for key, variations in self.acquisition_mappings.items():
            if name_upper == key or key in name_upper:
                search_terms.extend(variations)
            elif any(var in name_upper for var in variations):
                search_terms.append(key)
                search_terms.extend([v for v in variations if v != name_upper])
        
        # Find manufacturer IDs for all search terms
        for search_term in search_terms:
            if search_term in self.manufacturers:
                mfr_id = self.manufacturers[search_term]
                if mfr_id not in matches:
                    matches.append(mfr_id)
        
        # 2. Exact match (if not already found)
        if name_upper in self.manufacturers:
            mfr_id = self.manufacturers[name_upper]
            if mfr_id not in matches:
                matches.append(mfr_id)
        
        # 3. Find close matches using difflib (only if we need more matches)
        if len(matches) < 5:
            manufacturer_names = list(self.manufacturers.keys())
            
            # Get matches with similarity > 0.6 (60% similar)
            close_matches = difflib.get_close_matches(
                name_upper, 
                manufacturer_names, 
                n=10,  # Get up to 10 matches
                cutoff=0.6  # 60% similarity threshold
            )
            
            # Add IDs for close matches
            for match in close_matches:
                mfr_id = self.manufacturers[match]
                if mfr_id not in matches:
                    matches.append(mfr_id)
                    if len(matches) >= 5:
                        break
        
        # 4. Try substring matching for partial matches (only if we need more)
        if len(matches) < 5:
            for mfr_name, mfr_id in self.manufacturers.items():
                # Skip if already found
                if mfr_id in matches:
                    continue
                    
                # Check if either name contains the other (partial matching)
                if (name_upper in mfr_name or mfr_name in name_upper) and len(name_upper) > 2:
                    matches.append(mfr_id)
                    if len(matches) >= 5:
                        break
                    
                # Check for common abbreviations in manufacturer names
                name_parts = name_upper.split()
                mfr_parts = mfr_name.split()
                
                # If searching for abbreviation, look for full names
                if len(name_parts) == 1 and len(name_parts[0]) <= 4:  # Likely abbreviation
                    # Check if abbreviation matches first letters of manufacturer name parts
                    abbrev = name_parts[0]
                    if len(mfr_parts) >= len(abbrev):
                        first_letters = ''.join(part[0] for part in mfr_parts[:len(abbrev)])
                        if first_letters == abbrev:
                            matches.append(mfr_id)
                            if len(matches) >= 5:
                                break
                            
                # If searching for full name, look for abbreviations
                elif len(name_parts) > 1:
                    first_letters = ''.join(part[0] for part in name_parts)
                    if first_letters in mfr_name or mfr_name == first_letters:
                        matches.append(mfr_id)
                        if len(matches) >= 5:
                            break
        
        # Remove duplicates while preserving order
        unique_matches = []
        for match in matches:
            if match not in unique_matches:
                unique_matches.append(match)
        
        return unique_matches[:5]  # Limit to top 5 matches to avoid excessive API calls
    def search_part(self, part_number, manufacturer_name=None):
        """Search for a part in DigiKey database with sequential manufacturer ID attempts and part number variations"""
        if not self._ensure_authenticated():
            return None
        
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'X-DIGIKEY-Client-Id': API_KEYS[0]['CLIENT_ID'],
            'X-DIGIKEY-Locale-Site': 'US',
        }
        
        return self._search_part_with_headers(part_number, manufacturer_name, headers, self.rate_limiter, silent=False)
    
    def _search_part_with_headers(self, part_number, manufacturer_name, headers, rate_limiter, silent=False):
        """Internal search method that can be used by different workers with their own headers and rate limiters"""
        search_start_time = time.time()
        max_search_time = 30
        
        fuzzy_result = self._try_general_search(part_number, manufacturer_name, headers, rate_limiter, silent=silent)
        if fuzzy_result:
            return fuzzy_result
        
        return None
    
    def _try_general_search(self, part_number, manufacturer_name, headers, rate_limiter, silent=False):
        """Try DigiKey's general search API which has built-in fuzzy matching"""
        rate_limiter.wait_if_needed()
        
        search_data = {
            "Keywords": part_number,
            "RecordCount": 10,
            "RecordStartPosition": 0,
            "Filters": {}
        }
        
        if manufacturer_name and self.manufacturers:
            manufacturer_ids = self._find_manufacturer_variants(manufacturer_name)
            if manufacturer_ids:
                search_data["Filters"]["ManufacturerIds"] = manufacturer_ids[:3]
        
        try:
            response = requests.post(
                "https://api.digikey.com/products/v4/search/keyword", 
                json=search_data,
                headers=headers,
                timeout=(10, 30)
            )
            
            # Check for rate limit error before processing
            if response.status_code == 429:
                if not silent:
                    logger.warning(f"⚠️  Rate limit hit for {part_number}, waiting 60 seconds...")
                time.sleep(60)
                return None
            
            response.raise_for_status()
            
            if response.status_code == 200:
                result = response.json()
                products = result.get('Products', [])
                
                if products:
                    # Look for exact or close matches in the results
                    best_match = self._find_best_match(part_number, products)
                    if best_match:
                        # Normalize field names
                        if 'ManufacturerProductNumber' in best_match and 'ManufacturerPartNumber' not in best_match:
                            best_match['ManufacturerPartNumber'] = best_match['ManufacturerProductNumber']
                        return best_match
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                if not silent:
                    logger.warning(f"⚠️  Rate limit hit for {part_number}, waiting 60 seconds...")
                time.sleep(60)
                return None
            else:
                pass
        except Exception as e:
            pass
        
        return None
    
    def _find_best_match(self, original_part, products):
        """Find the best matching product from search results"""
        
        # First priority: exact match (case-insensitive)
        for product in products:
            mfr_pn = product.get('ManufacturerPartNumber', '') or product.get('ManufacturerProductNumber', '')
            if mfr_pn and mfr_pn.upper() == original_part.upper():
                return product
        
        # Second priority: starts with the original part number
        for product in products:
            mfr_pn = product.get('ManufacturerPartNumber', '') or product.get('ManufacturerProductNumber', '')
            if mfr_pn:
                if (mfr_pn.upper().startswith(original_part.upper()) or 
                    original_part.upper().startswith(mfr_pn.upper())):
                    return product
        
        # Third priority: return first result if it's active and has a datasheet
        for product in products:
            if product.get('ProductStatus') == 'Active' and product.get('DatasheetUrl'):
                return product
        
        # Last resort: return first result
        if products:
            return products[0]
        
        return None
    
    def _fix_malformed_url(self, url):
        """Fix common URL malformations and encode special characters"""
        if not url:
            return url
            
        # Fix protocol-relative URLs (starting with //)
        if url.startswith('//'):
            url = 'https:' + url
            
        # Fix URLs missing protocol entirely
        elif not url.startswith(('http://', 'https://')):
            if url.startswith('www.') or '.' in url:
                url = 'https://' + url
        
        # URL encode special characters, but preserve the scheme and domain
        if '://' in url:
            scheme_and_domain, path = url.split('://', 1)
            if '/' in path:
                domain, path_part = path.split('/', 1)
                # Only encode the path part, not the domain
                # Use quote with safe characters to preserve slashes and other URL structure
                encoded_path = quote(path_part, safe='/:@!$&\'()*+,;=?#[]')
                url = f"{scheme_and_domain}://{domain}/{encoded_path}"
            else:
                # No path, just domain
                url = f"{scheme_and_domain}://{path}"
                
        return url
    

    
    def _generate_part_variations(self, part_number):
        """Return the original part number as-is without variations"""
        return [part_number]
    def download_datasheet(self, url, filename, output_dir):
        """Download a datasheet PDF with enhanced timeout handling and minimal headers strategy"""
        # Reset last error message and store the fixed URL
        self.last_download_error = None
        self.last_fixed_url = url  # Store original URL initially
        
        if not url:
            self.last_download_error = "Missing URL"
            return False
        
        # Fix malformed URLs
        original_url = url
        url = self._fix_malformed_url(url)
        self.last_fixed_url = url  # Store the fixed URL
        if url != original_url:
            logger.debug(f"🔧 Fixed malformed URL: {original_url} -> {url}")
        
        os.makedirs(output_dir, exist_ok=True)
        filepath = os.path.join(output_dir, filename)
        
        # Skip if file already exists and resume is enabled
        if RESUME_ON_RESTART and os.path.exists(filepath):
            return True
        
        domain = url.split('/')[2] if '://' in url else ''
        
        # Special handling for known problematic domains
        problematic_domains = ['st.com', 'stmicroelectronics.com']
        is_problematic = any(domain_part in domain.lower() for domain_part in problematic_domains)
        
        max_attempts = 2 if is_problematic else 3
        timeout_override = 15 if is_problematic else TIMEOUT_SECONDS
        
        for attempt in range(max_attempts):
            # Check for shutdown before each attempt
            if shutdown_flag.is_set():
                return False
                
            try:
                # Add small random delay
                time.sleep(random.uniform(0.5, 2.0))
                
                # Strategy: Try without headers first, then with headers if we get 403
                use_headers = False
                
                # On first attempt, try without headers (many servers are more lenient)
                # On subsequent attempts after 403, try with headers
                if attempt == 0:
                    # First attempt: minimal headers to avoid detection
                    headers = {'Accept': '*/*'}
                else:
                    # Subsequent attempts: use full browser headers
                    use_headers = True
                    headers = self._get_headers(domain)
                
                # Override timeout for this specific request if problematic
                if is_problematic:
                    # Use even more aggressive timeout for problematic domains
                    kwargs = {
                        'url': url,
                        'headers': headers,
                        'timeout': (5, timeout_override),
                        'stream': True
                    }
                    
                    # Set very aggressive socket timeout
                    old_timeout = socket.getdefaulttimeout()
                    socket.setdefaulttimeout(10)  # 10 second max
                    
                    try:
                        response = requests.get(**kwargs)
                    finally:
                        socket.setdefaulttimeout(old_timeout)
                else:
                    response = self._make_request(url, headers, stream=True)
                
                # Handle common errors
                if response.status_code == 403:
                    if not use_headers and attempt < max_attempts - 1:
                        # If we got 403 without headers, try again with headers
                        time.sleep(2 ** attempt)  # Exponential backoff
                        continue
                    elif attempt < max_attempts - 1:
                        # If we got 403 with headers, wait longer and try again
                        time.sleep(2 ** attempt)  # Exponential backoff
                        continue
                    logger.debug(f"🔍 403 Forbidden for {filename} after {max_attempts} attempts")
                    return False
                elif response.status_code == 429:
                    if attempt == max_attempts - 1:
                        logger.debug(f"🔍 Rate limited for {filename} after {max_attempts} attempts")
                        return False
                    time.sleep(5)
                    continue
                
                response.raise_for_status()
                
                # Check if it's actually a PDF
                content_type = response.headers.get('content-type', '').lower()
                if 'html' in content_type:
                    # Store error message for HTML responses
                    self.last_download_error = f"Got HTML instead of PDF: {content_type}"
                    return False
                
                # Download the file with timeout protection
                with open(filepath, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        # Check for shutdown during download
                        if shutdown_flag.is_set():
                            f.close()
                            if os.path.exists(filepath):
                                os.remove(filepath)
                            return False
                        if chunk:
                            f.write(chunk)
                
                # Validate it's a PDF
                try:
                    with open(filepath, 'rb') as f:
                        header = f.read(4)
                        if header != b'%PDF':
                            # Check what we actually got
                            file_size = os.path.getsize(filepath)
                            self.last_download_error = f"Invalid PDF content: got {header[:4]} instead of %PDF (size: {file_size} bytes)"
                            os.remove(filepath)
                            return False
                except Exception as e:
                    self.last_download_error = f"PDF validation error: {str(e)}"
                    if os.path.exists(filepath):
                        os.remove(filepath)
                    return False
                
                return True
                
            except (requests.exceptions.Timeout, requests.exceptions.ConnectTimeout, 
                    requests.exceptions.ReadTimeout, OSError, ConnectionError) as e:
                # Store timeout errors for detailed reporting
                if attempt == max_attempts - 1:
                    self.last_download_error = f"Timeout error: {type(e).__name__}"
                    return False
                time.sleep(2)
            except requests.exceptions.HTTPError as e:
                # Store HTTP errors for detailed reporting
                if attempt == max_attempts - 1:
                    self.last_download_error = f"HTTP error: {e.response.status_code}"
                    return False
                time.sleep(1)
            except Exception as e:
                # Store unexpected errors for detailed reporting
                if attempt == max_attempts - 1:
                    self.last_download_error = f"{type(e).__name__}: {str(e)}"
                    return False
                time.sleep(1)
        
        return False
    
    def download_worker(self, download_queue, results_queue, progress, worker_id):
        """Download worker that processes jobs from the download queue"""
        while not shutdown_flag.is_set():
            try:
                # Get next download job from queue (with timeout)
                try:
                    job = download_queue.get(timeout=1)
                except queue.Empty:
                    continue
                
                if job is None:  # Sentinel value to stop
                    break
                
                if progress:
                    progress.update_worker_status(worker_id, "📥 Downloading", f"{job.internal_pn}")
                
                # Check if file already exists and resume is enabled
                if RESUME_ON_RESTART:
                    filepath = os.path.join("datasheets", job.filename)
                    try:
                        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                            # Get the fixed URL for consistency in reporting
                            actual_url = self._fix_malformed_url(job.datasheet_url)
                            
                            result = {
                                'status': 'success',
                                'internal_pn': job.internal_pn,
                                'manufacturer_pn': job.manufacturer_pn,
                                'found_part': job.found_part,
                                'manufacturer': job.manufacturer_found,
                                'digikey_pn': job.digikey_pn,
                                'filename': job.filename,
                                'url': actual_url,
                                'skipped': True
                            }
                            results_queue.put(result)
                            download_queue.task_done()
                            continue
                    except (OSError, IOError):
                        # File system error checking file, proceed with download
                        pass
                
                # Download the datasheet
                success = self.download_datasheet(job.datasheet_url, job.filename, "datasheets")
                
                # Get the fixed URL that was actually used for download
                actual_url = getattr(self, 'last_fixed_url', job.datasheet_url)
                
                if success:
                    if progress:
                        progress.update_worker_status(worker_id, "✅ Success", f"{job.internal_pn}")
                    
                    result = {
                        'status': 'success',
                        'internal_pn': job.internal_pn,
                        'manufacturer_pn': job.manufacturer_pn,
                        'found_part': job.found_part,
                        'manufacturer': job.manufacturer_found,
                        'digikey_pn': job.digikey_pn,
                        'filename': job.filename,
                        'url': actual_url
                    }
                    results_queue.put(result)
                else:
                    if progress:
                        progress.update_worker_status(worker_id, "⚠️ Download failed", f"{job.internal_pn}")
                    
                    # Get detailed error message if available
                    error_detail = getattr(self, 'last_download_error', None)
                    error_message = f"Download failed: {error_detail}" if error_detail else 'Part found but download blocked (try manual download from URL)'
                    
                    result = {
                        'status': 'download_failed',
                        'internal_pn': job.internal_pn,
                        'manufacturer_pn': job.manufacturer_pn,
                        'found_part': job.found_part,
                        'manufacturer': job.manufacturer_found,
                        'url': actual_url,
                        'message': error_message
                    }
                    results_queue.put(result)
                
                download_queue.task_done()
                
            except Exception as e:
                # Handle unexpected errors
                if 'job' in locals():
                    result = {
                        'status': 'error',
                        'internal_pn': job.internal_pn,
                        'manufacturer_pn': job.manufacturer_pn,
                        'error': f'Download worker error: {str(e)}'
                    }
                    results_queue.put(result)
                    try:
                        download_queue.task_done()
                    except:
                        pass
        
        if progress:
            progress.update_worker_status(worker_id, "⚪ Stopped", "")
    
    def process_part(self, part_info, progress=None, worker_id=None):
        """Process a single part with timeout handling (LEGACY METHOD - kept for compatibility)"""
        internal_pn, manufacturer_pn, manufacturer_name = part_info
        
        # Check if shutdown requested
        if shutdown_flag.is_set():
            return {
                'status': 'error',
                'internal_pn': internal_pn,
                'manufacturer_pn': manufacturer_pn,
                'error': 'Shutdown requested'
            }
        
        # Get consistent worker ID from progress bar
        if progress and worker_id is None:
            worker_id = progress.get_worker_id()
        elif worker_id is None:
            worker_id = f"Worker-{threading.current_thread().ident % 10}"
        
        try:
            if progress:
                progress.update_worker_status(worker_id, "🔍 Searching", f"{internal_pn}")
            
            # Search for the part
            product = self.search_part(manufacturer_pn, manufacturer_name)
            
            if not product:
                if progress:
                    progress.update_worker_status(worker_id, "❌ Not found", f"{internal_pn}")
                return {
                    'status': 'not_found',
                    'internal_pn': internal_pn,
                    'manufacturer_pn': manufacturer_pn
                }
            
            if isinstance(product, dict) and product.get('error'):
                if progress:
                    progress.update_worker_status(worker_id, "❌ Error", f"{internal_pn}")
                return {
                    'status': 'error',
                    'internal_pn': internal_pn,
                    'manufacturer_pn': manufacturer_pn,
                    'error': product.get('message', 'Unknown error')
                }
            
            # Get datasheet URL
            datasheet_url = product.get('DatasheetUrl')
            if not datasheet_url:
                if progress:
                    progress.update_worker_status(worker_id, "⚠️ No datasheet", f"{internal_pn}")
                return {
                    'status': 'no_datasheet',
                    'internal_pn': internal_pn,
                    'manufacturer_pn': manufacturer_pn,
                    'found_part': product.get('ManufacturerPartNumber', 'Unknown')
                }
            
            if progress:
                progress.update_worker_status(worker_id, "📥 Downloading", f"{internal_pn}")
            
            # Download datasheet
            filename = sanitize_filename(f"{internal_pn} {manufacturer_pn}") + ".pdf"
            success = self.download_datasheet(datasheet_url, filename, "datasheets")
            
            if success:
                if progress:
                    progress.update_worker_status(worker_id, "✅ Success", f"{internal_pn}")
                
                # Safely extract manufacturer name
                manufacturer_info = product.get('Manufacturer', {})
                if isinstance(manufacturer_info, dict):
                    manufacturer_name = manufacturer_info.get('Value', 'Unknown')
                else:
                    manufacturer_name = str(manufacturer_info) if manufacturer_info else 'Unknown'
                
                return {
                    'status': 'success',
                    'internal_pn': internal_pn,
                    'manufacturer_pn': manufacturer_pn,
                    'found_part': product.get('ManufacturerPartNumber', manufacturer_pn),
                    'manufacturer': manufacturer_name,
                    'digikey_pn': product.get('DigiKeyPartNumber', ''),
                    'filename': filename,
                    'url': datasheet_url
                }
            else:
                if progress:
                    progress.update_worker_status(worker_id, "⚠️ Download failed", f"{internal_pn}")
                
                # Safely extract manufacturer name for failed downloads too
                manufacturer_info = product.get('Manufacturer', {})
                if isinstance(manufacturer_info, dict):
                    manufacturer_name_extracted = manufacturer_info.get('Value', 'Unknown')
                else:
                    manufacturer_name_extracted = str(manufacturer_info) if manufacturer_info else 'Unknown'
                
                # Get detailed error message if available
                error_detail = getattr(self.downloader, 'last_download_error', None)
                error_message = f"Download failed: {error_detail}" if error_detail else 'Part found but download blocked (try manual download from URL)'
                
                return {
                    'status': 'download_failed',
                    'internal_pn': internal_pn,
                    'manufacturer_pn': manufacturer_pn,
                    'found_part': product.get('ManufacturerPartNumber', manufacturer_pn),
                    'manufacturer': manufacturer_name_extracted,
                    'url': datasheet_url,
                    'message': error_message
                }
                
        except Exception as e:
            if progress:
                progress.update_worker_status(worker_id, "❌ Exception", f"{internal_pn}")
            return {
                'status': 'error',
                'internal_pn': internal_pn,
                'manufacturer_pn': manufacturer_pn,
                'error': str(e)
            }
        finally:
            if progress:
                progress.update_worker_status(worker_id, "⚪ Idle", "")
    
    def load_parts_list(self, filename="parts_list.txt"):
        """Load parts list from file (supports both CSV and text formats)"""
        parts = []
        
        try:
            # Determine file format based on extension
            is_csv = filename.lower().endswith('.csv')
            
            with open(filename, 'r') as f:
                if is_csv:
                    # CSV format parsing
                    reader = csv.DictReader(f)
                    
                    # Find matching column names (case-insensitive)
                    def normalize_col(name):
                        return name.split('(')[0].strip().lower().replace('_', ' ').replace('.', '')
                    
                    # Filter out empty column names
                    valid_columns = [col for col in reader.fieldnames if col and col.strip()]
                    fieldnames_norm = {normalize_col(col): col for col in valid_columns}
                    
                    # Map expected columns to actual column names
                    internal_col = None
                    mfr_pn_col = None
                    mfr_name_col = None
                    
                    for expected in ['item number', 'internal pn', 'internal p/n']:
                        if expected in fieldnames_norm:
                            internal_col = fieldnames_norm[expected]
                            break
                    
                    for expected in ['mfr part number', 'manufacturer pn', 'part number']:
                        if expected in fieldnames_norm:
                            mfr_pn_col = fieldnames_norm[expected]
                            break
                    
                    for expected in ['mfr name', 'manufacturer name', 'manufacturer']:
                        if expected in fieldnames_norm:
                            mfr_name_col = fieldnames_norm[expected]
                            break
                    
                    if not internal_col or not mfr_pn_col:
                        logger.error(f"❌ CSV missing required columns. Found: {valid_columns}")
                        return []
                    
                    for row in reader:
                        internal_pn = row.get(internal_col, '').strip()
                        manufacturer_pn = row.get(mfr_pn_col, '').strip()
                        manufacturer_name = row.get(mfr_name_col, '').strip() if mfr_name_col else None
                        
                        if internal_pn and manufacturer_pn:
                            parts.append((internal_pn, manufacturer_pn, manufacturer_name))
                
                else:
                    # Text format parsing
                    for line_num, line in enumerate(f, 1):
                        line = line.strip()
                        if not line or line.startswith('#'):
                            continue
                        
                        parts_data = line.split()
                        if len(parts_data) >= 2:
                            internal_pn = parts_data[0]
                            manufacturer_pn = parts_data[1]
                            manufacturer_name = parts_data[2] if len(parts_data) >= 3 else None
                            parts.append((internal_pn, manufacturer_pn, manufacturer_name))
            
            return parts
            
        except FileNotFoundError:
            logger.error(f"❌ File '{filename}' not found")
            return []
        except Exception as e:
            logger.error(f"❌ Error loading parts list: {e}")
            return []
    
    def _save_report(self, results):
        """Save a simple CSV report"""
        os.makedirs("datasheets", exist_ok=True)
        
        with open("datasheets/report.csv", 'w') as f:
            f.write("Status,Internal P/N,Manufacturer P/N,Found Part,Manufacturer,Filename/URL,Notes\n")
            
            for item in results['success']:
                filename = item.get('filename', 'Downloaded')
                f.write(f"Success,{item['internal_pn']},\"{item['manufacturer_pn']}\",\"{item['found_part']}\",{item['manufacturer']},\"{filename}\",Downloaded\n")
            
            for item in results['no_datasheet']:
                f.write(f"No Datasheet,{item['internal_pn']},\"{item['manufacturer_pn']}\",\"{item.get('found_part', '')}\",,,No datasheet available\n")
            
            for item in results['not_found']:
                f.write(f"Not Found,{item['internal_pn']},\"{item['manufacturer_pn']}\",,,,Part not found in DigiKey\n")
            
            for item in results['download_failed']:
                # Use the datasheet URL instead of filename for failed downloads
                url = item.get('url', '')
                # Use detailed error message from the result
                error_message = item.get('message', 'Manual download required')
                f.write(f"Download Failed,{item['internal_pn']},\"{item['manufacturer_pn']}\",\"{item.get('found_part', '')}\",{item.get('manufacturer', '')},\"{url}\",{error_message}\n")
            
            for item in results['errors']:
                error_msg = item.get('error', 'Unknown error')
                f.write(f"Error,{item['internal_pn']},\"{item['manufacturer_pn']}\",,,,{error_msg}\n")
        
        logger.info(f"📄 Report saved: datasheets/report.csv")
    
    def run(self, input_file="parts_list.txt"):
        """Main function to download datasheetse"""
        logger.info("🚀 DigiKey Datasheet Downloader)")
        logger.info("=" * 50)
        
        # Load parts list
        parts = self.load_parts_list(input_file)
        if not parts:
            return
        
        # Check for existing files if resume is enabled
        existing_count = 0
        if RESUME_ON_RESTART:
            for internal_pn, manufacturer_pn, _ in parts:
                filename = sanitize_filename(f"{internal_pn} {manufacturer_pn}") + ".pdf"
                if os.path.exists(os.path.join("datasheets", filename)):
                    existing_count += 1
            
            if existing_count > 0:
                logger.info(f"📂 Found {existing_count} existing files (will skip)")
        
        # Validate we have enough API keys for the desired number of workers
        available_api_keys = len(API_KEYS)
        actual_api_workers = min(MAX_API_WORKERS, available_api_keys)
        
        if actual_api_workers < MAX_API_WORKERS:
            logger.warning(f"⚠️  Only {available_api_keys} API keys available, using {actual_api_workers} API workers instead of {MAX_API_WORKERS}")
        
        logger.info(f"📋 Found {len(parts)} parts to process")
        logger.info(f"⚙️  Settings: {actual_api_workers} API workers + {MAX_WORKERS} download workers, {REQUESTS_PER_MINUTE} req/min per API worker")
        
        # Estimate time (with multiple API workers, search should be faster)
        estimated_time = (len(parts) * 60) / (REQUESTS_PER_MINUTE * actual_api_workers)
        logger.info(f"⏱️  Estimated time: {estimated_time/60:.1f} minutes")
        logger.info("💡 Press Ctrl+C to stop safely at any time")
        logger.info("=" * 50)
        
        # Initialize progress bar with API + download workers
        progress = ProgressBar(len(parts), MAX_WORKERS + actual_api_workers)
        
        # Create queues
        parts_queue = queue.Queue()
        download_queue = queue.Queue()
        results_queue = queue.Queue()
        
        # Fill parts queue
        for part in parts:
            parts_queue.put(part)
        
        # Track results
        results = {'success': [], 'no_datasheet': [], 'not_found': [], 'download_failed': [], 'errors': []}
        consecutive_errors = 0
        completed_count = 0
        
        # Create and start API workers (multiple workers with different API keys)
        api_workers = []
        for i in range(actual_api_workers):
            worker_id = f"API-Worker-{i+1}" if actual_api_workers > 1 else "API-Worker"
            api_worker = APIWorker(self, download_queue, results_queue, progress, API_KEYS[i], worker_id)
            api_worker.set_parts_queue(parts_queue)
            api_worker.start()
            api_workers.append(api_worker)
        
        try:
            # Start download workers
            download_workers = []
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                # Submit download worker tasks
                for i in range(MAX_WORKERS):
                    worker_id = f"DL-Worker-{i+1}"
                    future = executor.submit(self.download_worker, download_queue, results_queue, progress, worker_id)
                    download_workers.append(future)
                
                # Process results as they come in
                while completed_count < len(parts) and not shutdown_flag.is_set():
                    try:
                        # Check for results with timeout
                        try:
                            result = results_queue.get(timeout=1)
                        except queue.Empty:
                            # Check if API worker is still running and parts queue is empty
                            if not api_worker.is_running and parts_queue.empty():
                                # API worker finished, check if download queue is empty
                                if download_queue.empty():
                                    # No more work to do
                                    break
                            continue
                        
                        completed_count += 1
                        
                        # Categorize result
                        status = result['status']
                        if status == 'success':
                            results['success'].append(result)
                            skipped_msg = " (skipped - exists)" if result.get('skipped') else ""
                            progress.update(f"✅ Downloaded {result['internal_pn']}{skipped_msg}")
                            consecutive_errors = 0  # Reset error counter
                        elif status == 'no_datasheet':
                            results['no_datasheet'].append(result)
                            progress.update(f"⚠️  No datasheet: {result['internal_pn']}")
                            consecutive_errors = 0
                        elif status == 'not_found':
                            results['not_found'].append(result)
                            progress.update(f"❌ Not found: {result['internal_pn']}")
                            consecutive_errors = 0
                        elif status == 'download_failed':
                            results['download_failed'].append(result)
                            progress.update(f"⚠️  Download failed: {result['internal_pn']}")
                            consecutive_errors = 0
                        else:
                            results['errors'].append(result)
                            progress.update(f"❌ Error: {result['internal_pn']}")
                            consecutive_errors += 1
                            
                            # Auto-pause on too many consecutive errors (5 errors)
                            if consecutive_errors >= 5:
                                logger.warning(f"\n⚠️  Too many consecutive errors ({consecutive_errors}). Pausing for 30 seconds...")
                                time.sleep(30)
                                consecutive_errors = 0
                        
                        results_queue.task_done()
                        
                    except KeyboardInterrupt:
                        logger.info("\n⏹️  Shutdown requested...")
                        shutdown_flag.set()
                        break
                    except Exception as e:
                        logger.error(f"\n❌ Unexpected error in result processing: {e}")
                        break
                
                # Signal workers to stop
                shutdown_flag.set()
                
                # Stop all API workers
                for api_worker in api_workers:
                    api_worker.stop()
                
                # Signal download workers to stop by adding sentinel values
                for _ in range(MAX_WORKERS):
                    download_queue.put(None)
                
                # Wait for download workers to complete (with timeout)
                for future in download_workers:
                    try:
                        future.result(timeout=10)
                    except:
                        pass
                
                # Force executor shutdown
                executor.shutdown(wait=False)
        
        except KeyboardInterrupt:
            logger.info("\n⏹️  Download stopped by user")
            shutdown_flag.set()
            logger.info("📊 Partial results:")
        except Exception as e:
            logger.error(f"\n❌ Unexpected error: {e}")
            shutdown_flag.set()
        finally:
            # Ensure all API workers are stopped
            if 'api_workers' in locals():
                for api_worker in api_workers:
                    api_worker.stop()
        
        # Print summary
        logger.info("\n" + "=" * 50)
        logger.info("📊 SUMMARY")
        logger.info("=" * 50)
        logger.info(f"✅ Downloaded: {len(results['success'])}")
        logger.info(f"⚠️  No datasheet: {len(results['no_datasheet'])}")
        logger.info(f"❌ Not found: {len(results['not_found'])}")
        logger.info(f"⚠️  Download failed: {len(results['download_failed'])}")
        logger.info(f"❌ Errors: {len(results['errors'])}")
        
        # Save simple report
        self._save_report(results)
        
        return results

def main():
    """Main function"""
    # Check for existing files and run accordingly
    if os.path.exists("parts.csv"):
        logger.info("📋 Found parts.csv")
        downloader = DigiKeyDownloader()
        downloader.run("parts.csv")
    elif os.path.exists("parts.txt"):
        logger.info("📋 Found parts.txt")
        downloader = DigiKeyDownloader()
        downloader.run("parts.txt")
    else:
        logger.error("❌ No parts list found!")
        logger.info("📝 Please create either:")
        logger.info("   • parts.csv (recommended)")
        logger.info("   • parts.txt")
        logger.info("\n💡 See README.md for format details")


if __name__ == "__main__":
    main()
