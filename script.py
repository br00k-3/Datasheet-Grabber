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
import atexit
import socket
import fcntl
import re
from urllib.parse import quote
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============================================================================
# CONFIGURATION
# ============================================================================

# Performance Settings
MAX_WORKERS = 3         # Number of parallel downloads (1-5 recommended)  
REQUESTS_PER_MINUTE = 15  # Rate limit to avoid being blocked
TIMEOUT_SECONDS = 30    # Request timeout

# User-Friendly Options
RESUME_ON_RESTART = True    # Skip files that already exist

# Global shutdown flag for clean exit
shutdown_flag = threading.Event()

def load_api_keys():
    """Load API keys from external file"""
    try:
        with open('api_keys.json', 'r') as f:
            config = json.load(f)
            return config.get('api_keys', [])
    except FileNotFoundError:
        print("❌ api_keys.json not found!")
        print("📝 Please create api_keys.json with your DigiKey API credentials:")
        print("""
{
  "api_keys": [
    {
      "CLIENT_ID": "your_client_id_here",
      "CLIENT_SECRET": "your_client_secret_here"
    }
  ]
}
        """)
        return []
    except Exception as e:
        print(f"❌ Error loading API keys: {e}")
        return []

# Load API keys from file
API_KEYS = load_api_keys()

# Validate API keys are available
if not API_KEYS:
    print("❌ No API keys available - script will not function!")
    print("📝 Please configure api_keys.json first")
    exit(1)


# ============================================================================
# SIGNAL HANDLING & PROCESS LOCK
# ============================================================================

class ProcessLock:
    """Prevent multiple instances of the script from running simultaneously"""
    
    def __init__(self, lockfile_path="script.lock"):
        self.lockfile_path = lockfile_path
        self.lockfile = None
        
    def acquire(self):
        """Acquire the process lock"""
        try:
            self.lockfile = open(self.lockfile_path, 'w')
            fcntl.flock(self.lockfile.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            
            # Write current process info to lock file
            self.lockfile.write(f"PID: {os.getpid()}\n")
            self.lockfile.write(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            self.lockfile.flush()
            
            return True
            
        except (IOError, OSError):
            if self.lockfile:
                self.lockfile.close()
                self.lockfile = None
            return False
    
    def release(self):
        """Release the process lock"""
        if self.lockfile:
            try:
                fcntl.flock(self.lockfile.fileno(), fcntl.LOCK_UN)
                self.lockfile.close()
                os.remove(self.lockfile_path)
            except (IOError, OSError):
                pass
            finally:
                self.lockfile = None
    
    def __enter__(self):
        if not self.acquire():
            print("❌ Another instance of the script is already running!")
            print("💡 If you're sure no other instance is running, delete 'script.lock' file")
            
            # Check if lock file exists and show info
            if os.path.exists(self.lockfile_path):
                try:
                    with open(self.lockfile_path, 'r') as f:
                        content = f.read().strip()
                        print(f"📄 Lock file contents:\n{content}")
                except:
                    pass
            
            sys.exit(1)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()

# Global process lock instance
process_lock = ProcessLock()

def signal_handler(signum, frame):
    """Handle Ctrl+C gracefully"""
    if shutdown_flag.is_set():
        # Already shutting down, force exit
        print("\n⚠️  Force terminating...")
        process_lock.release()  # Ensure lock file is cleaned up
        os._exit(1)
    
    print("\n⏹️  Shutdown requested...")
    shutdown_flag.set()
    
    # Clean up lock file
    process_lock.release()

# Register signal handler and cleanup
signal.signal(signal.SIGINT, signal_handler)
atexit.register(lambda: process_lock.release())

# ============================================================================
# UTILITY CLASSES
# ============================================================================

class ProgressBar:
    """Simple progress bar with compact worker status"""
    
    def __init__(self, total, max_workers=1):
        self.total = total
        self.current = 0
        self.start_time = time.time()
        self.lock = threading.Lock()
        self.max_workers = max_workers
        self.worker_status = {}
        self.last_display = ""
        self.last_refresh = 0
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
        """Update worker status without forcing immediate refresh"""
        with self.lock:
            self.worker_status[worker_id] = {
                'status': status,
                'part': part_name,
                'timestamp': time.time()
            }
            # Don't refresh display automatically - only refresh on progress.update()
    
    def update(self, status="Processing..."):
        """Update progress counter and force refresh"""
        with self.lock:
            self.current += 1
            self._refresh_display(status)
            
            if self.current >= self.total:
                elapsed = time.time() - self.start_time
                total_time = f"{int(elapsed//60)}:{int(elapsed%60):02d}"
                print(f'\n✅ Completed in {total_time}')
    
    def force_refresh(self):
        """Force a display refresh (useful for periodic updates)"""
        with self.lock:
            self._refresh_display()
    
    def _refresh_display(self, main_status="Processing..."):
        """Refresh display as a single line with compact worker info"""
        # Update last refresh time
        self.last_refresh = time.time()
        
        # Calculate progress
        percent = (self.current / self.total) * 100 if self.total > 0 else 0
        elapsed = time.time() - self.start_time
        
        # Progress bar (make it shorter to leave more room for status)
        bar_length = 20  # Reduced from 30 to 20
        filled = int(bar_length * self.current / self.total) if self.total > 0 else 0
        bar = '█' * filled + '░' * (bar_length - filled)
        
        # Calculate ETA
        if self.current > 0:
            rate = self.current / elapsed
            eta_seconds = (self.total - self.current) / rate if rate > 0 else 0
            eta = f"{int(eta_seconds//60)}:{int(eta_seconds%60):02d}"
        else:
            eta = "--:--"
        
        # Build compact worker status - only show active workers
        worker_statuses = []
        current_time = time.time()
        
        # Sort workers by ID for consistent display
        active_workers = sorted([wid for wid in self.worker_status.keys() if self.worker_status[wid]])
        
        for worker_id in active_workers:
            info = self.worker_status[worker_id]
            elapsed_worker = current_time - info['timestamp']
            
            # Use short status indicators
            if "Searching" in info['status'] or "🔍" in info['status']:
                worker_statuses.append("🔍")
            elif "Downloading" in info['status'] or "📥" in info['status']:
                worker_statuses.append("📥")
            elif "Success" in info['status'] or "✅" in info['status']:
                worker_statuses.append("✅")
            elif "Not found" in info['status'] or "❌" in info['status']:
                worker_statuses.append("❌")
            elif "Error" in info['status'] or "⚠️" in info['status']:
                worker_statuses.append("⚠️")
            elif "Idle" in info['status'] or "⚪" in info['status']:
                worker_statuses.append("⚪")
            else:
                worker_statuses.append("🔧")
                
            # Add timing warning if slow
            if elapsed_worker > 30:
                worker_statuses[-1] += f"({int(elapsed_worker)}s)"
        
        # Fill remaining spots with idle indicators if needed
        while len(worker_statuses) < self.max_workers:
            worker_statuses.append("⚪")
        
        workers_display = " ".join(worker_statuses)
        
        # Build a more compact progress display to leave more room for status
        # Use shorter progress format
        progress_part = f'[{bar}] {self.current}/{self.total} ({percent:.1f}%)'
        eta_part = f'ETA: {eta}'
        workers_part = f'Workers: {workers_display}'
        
        # Combine the fixed parts
        fixed_parts = f'\r{progress_part} | {eta_part} | {workers_part}'
        
        # Calculate available space for status message (assume 120 char terminal width)
        terminal_width = 120
        available_space = terminal_width - len(fixed_parts) - 3  # Leave 3 chars for " | "
        
        # Trim main status if needed, but be more generous with space
        if len(main_status) > available_space and available_space > 10:
            main_status = main_status[:available_space-3] + "..."
        elif available_space <= 10:
            # If very little space, just show first few characters
            main_status = main_status[:max(available_space, 0)]
        
        display_line = f'{fixed_parts} | {main_status}'
        
        # Clear any extra characters from previous longer line
        if len(display_line) < len(self.last_display):
            display_line += " " * (len(self.last_display) - len(display_line))
        
        print(display_line, end='', flush=True)
        self.last_display = display_line


class SimpleRateLimiter:
    """Basic rate limiter to avoid overwhelming servers"""
    
    def __init__(self, requests_per_minute):
        self.requests_per_minute = requests_per_minute
        self.request_times = []
        self.lock = threading.Lock()
    
    def wait_if_needed(self):
        with self.lock:
            now = time.time()
            # Remove old requests (older than 1 minute)
            self.request_times = [t for t in self.request_times if now - t < 60]
            
            # If we've made too many requests, wait
            if len(self.request_times) >= self.requests_per_minute:
                oldest = min(self.request_times)
                wait_time = 60 - (now - oldest) + 1
                if wait_time > 0:
                    time.sleep(wait_time)
            
            self.request_times.append(now)

# ============================================================================
# MAIN DOWNLOADER CLASS
# ============================================================================

class DigiKeyDownloader:
    """Simple DigiKey datasheet downloader with bot protection"""
    
    def __init__(self):
        self.access_token = None
        self.token_expiry = None
        self.manufacturers = {}
        self.rate_limiter = SimpleRateLimiter(REQUESTS_PER_MINUTE)
        self.session_lock = threading.Lock()
        
        # Load manufacturer database
        self._load_manufacturers()
        
        # User agents for rotation
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/120.0'
        ]
    
    # ========================================================================
    # INITIALIZATION & UTILITY METHODS
    # ========================================================================
    
    def _load_manufacturers(self):
        """Load manufacturer database if available"""
        try:
            with open('manufacturers.json', 'r') as f:
                data = json.load(f)
                for mfr in data.get('Manufacturers', []):
                    name = mfr.get('Name', '').upper()
                    self.manufacturers[name] = mfr.get('Id')
        except FileNotFoundError:
            print("⚠️  manufacturers.json not found - manufacturer filtering disabled")
        except Exception as e:
            print(f"⚠️  Error loading manufacturers: {e}")
    
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
    
    # ========================================================================
    # AUTHENTICATION METHODS
    # ========================================================================
    
    def authenticate(self):
        """Get access token from DigiKey"""
        print("🔐 Authenticating with DigiKey...")
        
        # Use the first API key from the loaded configuration
        api_key = API_KEYS[0]
        
        data = {
            'client_id': api_key['CLIENT_ID'],
            'client_secret': api_key['CLIENT_SECRET'],
            'grant_type': 'client_credentials'
        }
        
        try:
            response = requests.post("https://api.digikey.com/v1/oauth2/token", data=data, timeout=30)
            
            if response.status_code == 200:
                token_data = response.json()
                self.access_token = token_data['access_token']
                expires_in = token_data.get('expires_in', 1800)
                self.token_expiry = datetime.now() + timedelta(seconds=expires_in - 60)
                
                print("✅ Authentication successful!")
                return True
            else:
                print(f"❌ Authentication failed: {response.status_code}")
                return False
                
        except Exception as e:
            print(f"❌ Authentication error: {e}")
            return False
    
    def _ensure_authenticated(self):
        """Check if token is valid, refresh if needed (thread-safe)"""
        with self.session_lock:  # Serialize authentication to avoid multiple simultaneous auth attempts
            if not self.access_token or datetime.now() >= self.token_expiry:
                return self.authenticate()
            return True
    
    # ========================================================================
    # MANUFACTURER MATCHING METHODS
    # ========================================================================
    
    def _find_manufacturer_variants(self, manufacturer_name):
        """Find closest matching manufacturer IDs using fuzzy string matching"""
        if not manufacturer_name or not self.manufacturers:
            return []
        
        name_upper = manufacturer_name.upper().strip()
        matches = []
        
        # 1. Handle specific common manufacturer mappings FIRST
        common_mappings = {
            # Abbreviations to full names and variations
            'ST': ['STMICROELECTRONICS', 'STM'],
            'TI': ['TEXAS INSTRUMENTS'],
            'ADI': ['ANALOG DEVICES'],
            'ON': ['ONSEMI', 'ON SEMICONDUCTOR'],
            'NXP': ['NXP SEMICONDUCTORS'],
            'INFINEON': ['INFINEON TECHNOLOGIES'],
            'MICROCHIP': ['MICROCHIP TECHNOLOGY'],
            'MAXIM': ['MAXIM INTEGRATED'],
            'LINEAR': ['LINEAR TECHNOLOGY'],
            'FAIRCHILD': ['ONSEMI', 'ON SEMICONDUCTOR', 'FAIRCHILD/ON SEMICONDUCTOR'],
            'ROHM': ['ROHM SEMICONDUCTOR'],
            'VISHAY': ['VISHAY INTERTECHNOLOGY'],
            
            # Common full name variations that DigiKey uses differently
            'ON SEMICONDUCTOR': ['ONSEMI', 'ON', 'FAIRCHILD/ON SEMICONDUCTOR'],
            'ST MICROELECTRONICS': ['STMICROELECTRONICS', 'ST', 'STM'],
            'TEXAS INSTRUMENTS': ['TI'],
            'ANALOG DEVICES': ['ADI'],
            'NXP SEMICONDUCTORS': ['NXP'],
            'INFINEON TECHNOLOGIES': ['INFINEON'],
            'MICROCHIP TECHNOLOGY': ['MICROCHIP'],
            'MAXIM INTEGRATED': ['MAXIM'],
            'LINEAR TECHNOLOGY': ['LINEAR', 'LTC'],
            'ROHM SEMICONDUCTOR': ['ROHM'],
            'VISHAY INTERTECHNOLOGY': ['VISHAY'],
        }
        
        # Apply common mappings first - search both directions
        search_terms = [name_upper]
        
        # Add mapped variations for the input name
        for key, variations in common_mappings.items():
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
    
    # ========================================================================
    # API SEARCH METHODS
    # ========================================================================
    
    def search_part(self, part_number, manufacturer_name=None):
        """Search for a part in DigiKey database with sequential manufacturer ID attempts"""
        if not self._ensure_authenticated():
            return None
        
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'X-DIGIKEY-Client-Id': API_KEYS[0]['CLIENT_ID'],
            'X-DIGIKEY-Locale-Site': 'US',
        }
        
        url = f"https://api.digikey.com/products/v4/search/{quote(part_number)}/productdetails"
        
        # First, try without manufacturer filter
        self.rate_limiter.wait_if_needed()
        try:
            response = self._make_request(url, headers)
            
            if response.status_code == 200:
                result = response.json()
                if 'Product' in result:
                    product = result['Product']
                    # Normalize field names
                    if 'ManufacturerProductNumber' in product and 'ManufacturerPartNumber' not in product:
                        product['ManufacturerPartNumber'] = product['ManufacturerProductNumber']
                    return product
                    
            elif response.status_code == 404:
                # Check if it's a "duplicate products" error
                try:
                    error_data = response.json()
                    if "Duplicate Products found" not in error_data.get('detail', ''):
                        # Regular 404 - part not found
                        return None
                except:
                    # Regular 404 - part not found
                    return None
                    
                # Multiple products found - try with manufacturer filters
                if manufacturer_name:
                    manufacturer_ids = self._find_manufacturer_variants(manufacturer_name)
                    
                    for i, mfr_id in enumerate(manufacturer_ids):
                        # Rate limit each attempt
                        self.rate_limiter.wait_if_needed()
                        
                        try:
                            response = self._make_request(url, headers, params={'manufacturerId': mfr_id})
                            
                            if response.status_code == 200:
                                result = response.json()
                                if 'Product' in result:
                                    product = result['Product']
                                    # Normalize field names
                                    if 'ManufacturerProductNumber' in product and 'ManufacturerPartNumber' not in product:
                                        product['ManufacturerPartNumber'] = product['ManufacturerProductNumber']
                                    return product
                            # If 404 or other error, continue to next manufacturer ID
                            
                        except Exception:
                            # Error with this manufacturer ID, try next one
                            continue
                    
                    # If we get here, none of the manufacturer IDs worked
                    return {'error': 'multiple_products', 'message': f'Multiple products found for "{manufacturer_name}" - tried {len(manufacturer_ids)} manufacturer variants'}
                else:
                    return {'error': 'multiple_products', 'message': 'Multiple products found - manufacturer name needed to filter results'}
            
            # Other HTTP errors
            return None
            
        except Exception as e:
            return None
    
    # ========================================================================
    # DOWNLOAD METHODS
    # ========================================================================
    
    def download_datasheet(self, url, filename, output_dir):
        """Download a datasheet PDF with enhanced timeout handling"""
        if not url:
            return False
        
        os.makedirs(output_dir, exist_ok=True)
        filepath = os.path.join(output_dir, filename)
        
        # Skip if file already exists and resume is enabled
        if RESUME_ON_RESTART and os.path.exists(filepath):
            return True
        
        domain = url.split('/')[2] if '://' in url else ''
        headers = self._get_headers(domain)
        
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
                    if attempt < max_attempts - 1:
                        time.sleep(2 ** attempt)  # Exponential backoff
                        continue
                    return False
                elif response.status_code == 429:
                    time.sleep(5)
                    continue
                
                response.raise_for_status()
                
                # Check if it's actually a PDF
                content_type = response.headers.get('content-type', '').lower()
                if 'html' in content_type:
                    # Try to extract PDF URL from HTML
                    pdf_patterns = [
                        r'https?://[^"\s]+\.pdf[^"\s]*',
                        r'window\.viewerPdfUrl\s*=\s*[\'"]([^\'"]+)[\'"]',
                    ]
                    
                    html_content = response.text
                    for pattern in pdf_patterns:
                        matches = re.findall(pattern, html_content, re.IGNORECASE)
                        if matches:
                            extracted_url = matches[0]
                            if extracted_url != url:
                                return self.download_datasheet(extracted_url, filename, output_dir)
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
                with open(filepath, 'rb') as f:
                    if f.read(4) != b'%PDF':
                        os.remove(filepath)
                        return False
                
                return True
                
            except (requests.exceptions.Timeout, requests.exceptions.ConnectTimeout, 
                    requests.exceptions.ReadTimeout, OSError, ConnectionError):
                # Silent retry on timeout
                if attempt == max_attempts - 1:
                    return False
                time.sleep(2)
            except Exception:
                if attempt == max_attempts - 1:
                    return False
                time.sleep(1)
        
        return False
    
    # ========================================================================
    # PART PROCESSING METHODS
    # ========================================================================
    
    def process_part(self, part_info, progress=None, worker_id=None):
        """Process a single part with timeout handling"""
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
            filename = f"{internal_pn} {manufacturer_pn}.pdf"
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
                
                return {
                    'status': 'download_failed',
                    'internal_pn': internal_pn,
                    'manufacturer_pn': manufacturer_pn,
                    'found_part': product.get('ManufacturerPartNumber', manufacturer_pn),
                    'manufacturer': manufacturer_name_extracted,
                    'url': datasheet_url,
                    'message': 'Part found but download blocked (try manual download from URL)'
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
    
    # ========================================================================
    # FILE HANDLING METHODS
    # ========================================================================
    
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
                        print(f"❌ CSV missing required columns. Found: {valid_columns}")
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
            print(f"❌ File '{filename}' not found")
            return []
        except Exception as e:
            print(f"❌ Error loading parts list: {e}")
            return []
    
    def _save_report(self, results):
        """Save a simple CSV report"""
        os.makedirs("datasheets", exist_ok=True)
        
        with open("datasheets/report.csv", 'w') as f:
            f.write("Status,Internal P/N,Manufacturer P/N,Found Part,Manufacturer,Filename/URL,Notes\n")
            
            for item in results['success']:
                f.write(f"Success,{item['internal_pn']},{item['manufacturer_pn']},{item['found_part']},{item['manufacturer']},{item['filename']},Downloaded\n")
            
            for item in results['no_datasheet']:
                f.write(f"No Datasheet,{item['internal_pn']},{item['manufacturer_pn']},{item.get('found_part', '')},,,No datasheet available\n")
            
            for item in results['not_found']:
                f.write(f"Not Found,{item['internal_pn']},{item['manufacturer_pn']},,,,Part not found in DigiKey\n")
            
            for item in results['download_failed']:
                # Use the datasheet URL instead of filename for failed downloads
                url = item.get('url', '')
                f.write(f"Download Failed,{item['internal_pn']},{item['manufacturer_pn']},{item.get('found_part', '')},{item.get('manufacturer', '')},{url},Manual download required\n")
            
            for item in results['errors']:
                error_msg = item.get('error', 'Unknown error')
                f.write(f"Error,{item['internal_pn']},{item['manufacturer_pn']},,,,{error_msg}\n")
        
        print(f"📄 Report saved: datasheets/report.csv")
    
    # ========================================================================
    # MAIN EXECUTION METHOD
    # ========================================================================
    
    def run(self, input_file="parts_list.txt"):
        """Main function to download datasheets"""
        print("🚀 DigiKey Datasheet Downloader")
        print("=" * 50)
        
        # Load parts list
        parts = self.load_parts_list(input_file)
        if not parts:
            return
        
        # Check for existing files if resume is enabled
        existing_count = 0
        if RESUME_ON_RESTART:
            for internal_pn, manufacturer_pn, _ in parts:
                filename = f"{internal_pn} {manufacturer_pn}.pdf"
                if os.path.exists(os.path.join("datasheets", filename)):
                    existing_count += 1
            
            if existing_count > 0:
                print(f"📂 Found {existing_count} existing files (will skip)")
        
        print(f"📋 Found {len(parts)} parts to process")
        print(f"⚙️  Settings: {MAX_WORKERS} workers, {REQUESTS_PER_MINUTE} req/min")
        
        # Estimate time
        estimated_time = (len(parts) * 60) / REQUESTS_PER_MINUTE / MAX_WORKERS
        print(f"⏱️  Estimated time: {estimated_time/60:.1f} minutes")
        print("💡 Press Ctrl+C to stop safely at any time")
        print("=" * 50)
        
        # Initialize progress bar with worker tracking
        progress = ProgressBar(len(parts), MAX_WORKERS)
        
        # Track results
        results = {'success': [], 'no_datasheet': [], 'not_found': [], 'download_failed': [], 'errors': []}
        consecutive_errors = 0
        
        try:
            # Process parts in parallel with timeout
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                # Submit all tasks - let progress bar assign worker IDs automatically
                future_to_part = {}
                for part in parts:
                    # Check for shutdown before submitting new tasks
                    if shutdown_flag.is_set():
                        break
                    future = executor.submit(self.process_part, part, progress)
                    future_to_part[future] = part
                
                # Use as_completed with timeout to prevent infinite hanging
                completed_count = 0
                for future in as_completed(future_to_part, timeout=300):  # 5 minute timeout per batch
                    # Check for shutdown during processing
                    if shutdown_flag.is_set():
                        print("\n⏹️  Cancelling remaining tasks...")
                        # Cancel all remaining futures
                        for f in future_to_part.keys():
                            if not f.done():
                                f.cancel()
                        break
                    
                    try:
                        result = future.result(timeout=120)  # 2 minute timeout per individual task
                        completed_count += 1
                        
                        # Categorize result
                        status = result['status']
                        if status == 'success':
                            results['success'].append(result)
                            progress.update(f"✅ Downloaded {result['internal_pn']}")
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
                                print(f"\n⚠️  Too many consecutive errors ({consecutive_errors}). Pausing for 30 seconds...")
                                time.sleep(30)
                                consecutive_errors = 0
                                
                    except concurrent.futures.TimeoutError:
                        print(f"\n⏰ Task timeout - some workers may be hung. Continuing with remaining tasks...")
                        results['errors'].append({
                            'status': 'error',
                            'internal_pn': 'timeout',
                            'manufacturer_pn': 'timeout',
                            'error': 'Task timed out'
                        })
                        progress.update("⏰ Task timeout")
                        
                # Check for any remaining uncompleted futures
                remaining_futures = [f for f in future_to_part.keys() if not f.done()]
                if remaining_futures:
                    print(f"\n⚠️  Warning: {len(remaining_futures)} tasks did not complete")
                    for future in remaining_futures:
                        future.cancel()  # Try to cancel stuck tasks
                        
                # Force executor shutdown
                executor.shutdown(wait=False)  # Don't wait for stuck threads
        
        except KeyboardInterrupt:
            print("\n⏹️  Download stopped by user")
            shutdown_flag.set()
            print("📊 Partial results:")
        
        except Exception as e:
            print(f"\n❌ Unexpected error: {e}")
            shutdown_flag.set()
        
        # Print summary
        print("\n" + "=" * 50)
        print("📊 SUMMARY")
        print("=" * 50)
        print(f"✅ Downloaded: {len(results['success'])}")
        print(f"⚠️  No datasheet: {len(results['no_datasheet'])}")
        print(f"❌ Not found: {len(results['not_found'])}")
        print(f"⚠️  Download failed: {len(results['download_failed'])}")
        print(f"❌ Errors: {len(results['errors'])}")
        
        # Save simple report
        self._save_report(results)
        
        return results

# ============================================================================
# MAIN EXECUTION
# ============================================================================

def main():
    """Main function"""
    # Use process lock to prevent multiple instances
    with process_lock:
        # Check for existing files and run accordingly
        if os.path.exists("parts_list.csv"):
            print("📋 Found parts_list.csv")
            downloader = DigiKeyDownloader()
            downloader.run("parts_list.csv")
        elif os.path.exists("parts_list.txt"):
            print("📋 Found parts_list.txt")
            downloader = DigiKeyDownloader()
            downloader.run("parts_list.txt")
        else:
            print("❌ No parts list found!")
            print("📝 Please create either:")
            print("   • parts_list.csv (recommended)")
            print("   • parts_list.txt")
            print("\n💡 See README.md for format details")


if __name__ == "__main__":
    main()
