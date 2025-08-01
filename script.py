import requests
import os
import json
import time
from urllib.parse import urlparse, quote
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

class DigiKeyDatasheetDownloader:
    def __init__(self, client_id, client_secret):
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = None
        self.token_expiry = None
        self.manufacturers = {}  # Cache for manufacturer lookups
        self.api_lock = threading.Lock()  # Thread-safe API calls
        self.load_manufacturers()
    
    def load_manufacturers(self):
        """Load manufacturers database from JSON file"""
        try:
            with open('manufacturers.json', 'r') as f:
                data = json.load(f)
                # Create a lookup dictionary by manufacturer name (case-insensitive)
                for manufacturer in data.get('Manufacturers', []):
                    name = manufacturer.get('Name', '').upper()
                    self.manufacturers[name] = manufacturer.get('Id')
                print(f"✅ Loaded {len(self.manufacturers)} manufacturers from database")
        except FileNotFoundError:
            print("⚠️  manufacturers.json not found - manufacturer filtering will be disabled")
            self.manufacturers = {}
        except Exception as e:
            print(f"⚠️  Error loading manufacturers.json: {e}")
            self.manufacturers = {}
    
    def find_manufacturer_id(self, manufacturer_name):
        """Find manufacturer ID by name (case-insensitive search)"""
        if not manufacturer_name:
            return None
        
        # Try exact match first
        name_upper = manufacturer_name.upper()
        if name_upper in self.manufacturers:
            return self.manufacturers[name_upper]
        
        # Try partial match (contains)
        for mfr_name, mfr_id in self.manufacturers.items():
            if manufacturer_name.upper() in mfr_name or mfr_name in manufacturer_name.upper():
                print(f"   🔍 Found partial match: '{manufacturer_name}' -> '{mfr_name}' (ID: {mfr_id})")
                return mfr_id
        
        print(f"   ⚠️  No manufacturer ID found for '{manufacturer_name}'")
        return None
    
    def authenticate(self):
        """Get access token using client credentials"""
        data = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'grant_type': 'client_credentials'
        }
        
        response = requests.post("https://api.digikey.com/v1/oauth2/token", data=data)
        
        if response.status_code == 200:
            token_data = response.json()
            self.access_token = token_data['access_token']
            expires_in = token_data.get('expires_in', 1800)
            self.token_expiry = datetime.now() + timedelta(seconds=expires_in - 60)
            print("✅ Authentication successful!")
            print(f"   Token type: {token_data.get('token_type', 'N/A')}")
            print(f"   Expires in: {expires_in} seconds")
            return True
        else:
            print(f"❌ Authentication failed: {response.status_code}")
            print(f"   Response: {response.text}")
            return False
    
    def ensure_authenticated(self):
        """Check if token is valid, refresh if needed"""
        if not self.access_token or datetime.now() >= self.token_expiry:
            print(f"   🔄 Token expired or missing - refreshing authentication...")
            return self.authenticate()
        return True
    
    def search_products(self, keyword, manufacturer_id=None, max_retries=3):
        """Search for products by keyword, optionally filtered by manufacturer"""
        for attempt in range(max_retries):
            if not self.ensure_authenticated():
                return None
            
            headers = {
                'Authorization': f'Bearer {self.access_token}',
                'X-DIGIKEY-Client-Id': self.client_id,
                'X-DIGIKEY-Locale-Site': 'US',
                'X-DIGIKEY-Locale-Language': 'en',
                'X-DIGIKEY-Locale-Currency': 'USD'
            }
            
            # Build URL with query parameters for part number search
            encoded_keyword = quote(keyword)
            url = f"https://api.digikey.com/products/v4/search/{encoded_keyword}/productdetails"
            
            # Add manufacturer ID as query parameter if provided
            params = {}
            if manufacturer_id:
                params['manufacturerId'] = manufacturer_id
                print(f"   🏭 Including manufacturer ID: {manufacturer_id}")
            
            print(f"   🔍 API Call: {url}")
            if params:
                print(f"   📋 Query params: {params}")
            print(f"   🔑 Using ProductSearchV4 GET for part number '{keyword}'")
            if attempt > 0:
                print(f"   🔄 Retry attempt {attempt + 1}/{max_retries}")
            
            try:
                response = requests.get(url, headers=headers, params=params, timeout=60)
                
                if response.status_code == 200:
                    print(f"   ✅ Search successful (200)")
                    result = response.json()
                    print(f"   🔍 Raw response keys: {list(result.keys())}")
                    
                    # The product details endpoint returns a single product, not an array
                    if 'Product' in result:
                        print(f"   📦 Single product found in response")
                        
                        # Debug: Show the actual product structure
                        product = result['Product']
                        print(f"   🔍 Debug - Product keys: {list(product.keys())}")
                        # The API response uses different field names than expected
                        mfr_part = product.get('ManufacturerProductNumber') or product.get('ManufacturerPartNumber')
                        dk_part = product.get('DigiKeyPartNumber') 
                        manufacturer = product.get('Manufacturer', {})
                        print(f"   🔍 Debug - ManufacturerProductNumber: {mfr_part}")
                        print(f"   🔍 Debug - DigiKeyPartNumber: {dk_part}")
                        print(f"   🔍 Debug - Manufacturer: {manufacturer}")
                        
                        # Fix the field names for compatibility with the rest of the code
                        if 'ManufacturerProductNumber' in product and 'ManufacturerPartNumber' not in product:
                            product['ManufacturerPartNumber'] = product['ManufacturerProductNumber']
                        
                        # Convert single product to Products array format for compatibility
                        result['Products'] = [result['Product']]
                        print(f"   📊 Converted to Products array with 1 item")
                    else:
                        print(f"   ⚠️  No 'Product' key in response")
                        print(f"   📄 Full response: {result}")
                    return result
                elif response.status_code == 404:
                    # Handle the "Duplicate Products found" error
                    try:
                        error_response = response.json()
                        if "Duplicate Products found" in error_response.get('detail', ''):
                            print(f"   ⚠️  Multiple products found for '{keyword}' - manufacturer filter needed")
                            print(f"   📄 Error details: {error_response.get('detail', 'No details')}")
                            return {'error': 'duplicate_products', 'detail': error_response.get('detail', '')}
                        else:
                            print(f"   ❌ Search failed: {response.status_code}")
                            print(f"   Response: {response.text}")
                            return None
                    except:
                        print(f"   ❌ Search failed: {response.status_code}")
                        print(f"   Response: {response.text}")
                        return None
                else:
                    print(f"   ❌ Search failed: {response.status_code}")
                    print(f"   Response: {response.text}")
                    return None
                    
            except requests.exceptions.Timeout:
                print(f"   ⏰ Request timeout on attempt {attempt + 1}")
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 5  # Progressive backoff: 5s, 10s, 15s
                    print(f"   ⏳ Waiting {wait_time} seconds before retry...")
                    time.sleep(wait_time)
                    continue
                else:
                    print(f"   ❌ Max retries reached - giving up")
                    return None
            except requests.exceptions.RequestException as e:
                print(f"   ❌ Request error on attempt {attempt + 1}: {str(e)}")
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 5
                    print(f"   ⏳ Waiting {wait_time} seconds before retry...")
                    time.sleep(wait_time)
                    continue
                else:
                    print(f"   ❌ Max retries reached - giving up")
                    return None
            except Exception as e:
                print(f"   ❌ Unexpected error: {str(e)}")
                return None
        
        return None
    
    def get_product_details(self, digikey_part_number):
        """Get detailed information about a specific product"""
        if not self.ensure_authenticated():
            return None
        
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'X-DIGIKEY-Client-Id': self.client_id
        }
        
        url = f"https://api.digikey.com/products/v4/search/{digikey_part_number}/productdetails"
        print(f"   📋 API Call: {url}")
        print(f"   🔍 Getting details for DigiKey P/N: {digikey_part_number}")
        
        response = requests.get(url, headers=headers, timeout=60)
        
        if response.status_code == 200:
            print(f"   ✅ Product details successful (200)")
            return response.json()
        else:
            print(f"   ❌ Product details failed: {response.status_code}")
            print(f"   Response: {response.text}")
            return None
    
    def download_datasheet(self, datasheet_url, output_dir, filename, max_retries=3):
        """Download a datasheet from the provided URL with validation and anti-blocking measures"""
        if not datasheet_url:
            print(f"   ❌ No datasheet URL provided")
            return False
        
        os.makedirs(output_dir, exist_ok=True)
        filepath = os.path.join(output_dir, filename)
        
        # Different User-Agent strings to try if we get blocked
        user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/120.0'
        ]
        
        for attempt in range(max_retries):
            try:
                print(f"   🌐 Datasheet URL: {datasheet_url}")
                if attempt > 0:
                    print(f"   🔄 Retry attempt {attempt + 1}/{max_retries}")
                
                # Use different User-Agent on each attempt
                user_agent = user_agents[attempt % len(user_agents)]
                
                # Enhanced headers to bypass bot detection
                headers = {
                    'User-Agent': user_agent,
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1',
                    'Sec-Fetch-Dest': 'document',
                    'Sec-Fetch-Mode': 'navigate',
                    'Sec-Fetch-Site': 'none',
                    'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
                    'Sec-Ch-Ua-Mobile': '?0',
                    'Sec-Ch-Ua-Platform': '"Windows"',
                    'Cache-Control': 'no-cache',
                    'Pragma': 'no-cache'
                }
                
                # Add referer for some sites that check it
                if 'onsemi.com' in datasheet_url:
                    headers['Referer'] = 'https://www.onsemi.com/'
                elif 'st.com' in datasheet_url:
                    headers['Referer'] = 'https://www.st.com/'
                elif 'analog.com' in datasheet_url:
                    headers['Referer'] = 'https://www.analog.com/'
                elif 'ti.com' in datasheet_url:
                    headers['Referer'] = 'https://www.ti.com/'
                
                print(f"   🔄 Using User-Agent: {user_agent[:50]}...")
                
                # Create a session to maintain cookies
                session = requests.Session()
                session.headers.update(headers)
                
                # Add a small delay to appear more human-like
                if attempt > 0:
                    delay = attempt * 2  # 2s, 4s, 6s delays
                    print(f"   ⏳ Waiting {delay} seconds before retry...")
                    time.sleep(delay)
                
                response = session.get(datasheet_url, stream=True, timeout=15, allow_redirects=True)
                
                # Handle different response codes
                if response.status_code == 403:
                    print(f"   ⚠️  403 Forbidden on attempt {attempt + 1} - trying different approach...")
                    if attempt < max_retries - 1:
                        continue
                    else:
                        print(f"   ❌ All attempts failed with 403 Forbidden - skipping this datasheet")
                        return False
                elif response.status_code == 429:
                    print(f"   ⚠️  429 Rate Limited - waiting briefly...")
                    if attempt < max_retries - 1:
                        time.sleep(5)  # Reduced from 10 seconds
                        continue
                    else:
                        print(f"   ❌ Rate limited on all attempts")
                        return False
                
                response.raise_for_status()
                
                # Check content type to ensure it's a PDF
                content_type = response.headers.get('content-type', '').lower()
                print(f"   📄 Content-Type: {content_type}")
                
                # If we get HTML, it might be a viewer page - try to extract the real PDF URL
                if 'html' in content_type:
                    print(f"   ⚠️  Received HTML instead of PDF - this might be a viewer page")
                    print(f"   🔍 Attempting to extract direct PDF URL...")
                    
                    # Read the HTML content to look for PDF URLs
                    html_content = response.text
                    
                    # Look for common PDF URL patterns
                    import re
                    pdf_patterns = [
                        r"window\.viewerPdfUrl\s*=\s*['\"]([^'\"]+)['\"]",  # Widen viewer pattern
                        r'https?://[^"\s]+\.pdf[^"\s]*',  # Direct PDF URLs
                        r'data-pdf-url="([^"]+)"',        # data-pdf-url attribute
                        r'pdf_url["\s]*:["\s]*([^"]+)',   # pdf_url in JSON
                        r'src="([^"]+\.pdf[^"]*)"',       # src attribute with PDF
                    ]
                    
                    extracted_url = None
                    for pattern in pdf_patterns:
                        matches = re.findall(pattern, html_content, re.IGNORECASE)
                        if matches:
                            extracted_url = matches[0] if isinstance(matches[0], str) else matches[0][0]
                            print(f"   🎯 Found potential PDF URL: {extracted_url}")
                            break
                    
                    if extracted_url and extracted_url != datasheet_url:
                        print(f"   🔄 Retrying with extracted URL...")
                        return self.download_datasheet(extracted_url, output_dir, filename, max_retries)
                    else:
                        print(f"   ❌ Could not extract direct PDF URL from HTML page")
                        return False
                
                if 'pdf' not in content_type and 'application/octet-stream' not in content_type:
                    print(f"   ⚠️  Warning: Content type '{content_type}' may not be a PDF")
                
                # Check content length
                content_length = response.headers.get('content-length')
                if content_length:
                    size_mb = int(content_length) / (1024 * 1024)
                    print(f"   📏 File size: {size_mb:.2f} MB")
                    
                    # Check if file is suspiciously small (likely an error page)
                    if int(content_length) < 1024:  # Less than 1KB
                        print(f"   ⚠️  Warning: File very small ({content_length} bytes) - might be an error page")
                
                # Download the file
                total_size = 0
                with open(filepath, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            total_size += len(chunk)
                
                print(f"   💾 Downloaded: {total_size} bytes")
                
                # Basic PDF validation - check if file starts with PDF magic bytes
                with open(filepath, 'rb') as f:
                    first_bytes = f.read(4)
                    if first_bytes != b'%PDF':
                        print(f"   ❌ Warning: File does not appear to be a valid PDF (magic bytes: {first_bytes})")
                        # Don't delete the file, let user inspect it
                        print(f"   💡 File saved anyway for inspection: {filepath}")
                        return False
                    else:
                        print(f"   ✅ PDF validation passed")
                
                return True
                
            except requests.exceptions.Timeout:
                print(f"   ❌ Download timeout after 15 seconds on attempt {attempt + 1}")
                if attempt < max_retries - 1:
                    continue
                else:
                    return False
            except requests.exceptions.RequestException as e:
                print(f"   ❌ Download failed on attempt {attempt + 1}: {str(e)}")
                if attempt < max_retries - 1:
                    continue
                else:
                    return False
            except Exception as e:
                print(f"   ❌ Unexpected error during download on attempt {attempt + 1}: {str(e)}")
                if attempt < max_retries - 1:
                    continue
                else:
                    return False
        
        return False
    
    def process_single_part(self, part_info, output_dir):
        """Process a single part - designed for parallel execution"""
        i, total_parts, internal_part, manufacturer_part, manufacturer_name = part_info
        
        print(f"\n🔄 [{i}/{total_parts}] Processing:")
        print(f"   📦 Internal P/N: {internal_part}")
        print(f"   🏭 Manufacturer P/N: {manufacturer_part}")
        if manufacturer_name:
            print(f"   🏢 Manufacturer: {manufacturer_name}")
        
        # Show progress percentage
        progress_pct = (i / total_parts) * 100
        print(f"   📊 Progress: {progress_pct:.1f}% complete")
        
        try:
            # Thread-safe API calls
            with self.api_lock:
                # First, try searching without manufacturer filter
                print(f"   🔍 Initial search without manufacturer filter...")
                search_results = self.search_products(manufacturer_part)
                
                # Check if we got a duplicate products error
                if isinstance(search_results, dict) and search_results.get('error') == 'duplicate_products':
                    print(f"   ⚠️  Multiple products found, attempting with manufacturer filter...")
                    
                    if not manufacturer_name:
                        print(f"   ❌ No manufacturer specified in parts list to filter duplicates")
                        print(f"   💡 Add manufacturer name as third column to resolve duplicates")
                        return {
                            "status": "error",
                            "internal_pn": internal_part,
                            "manufacturer_pn": manufacturer_part,
                            "error": "Multiple products found - need manufacturer name"
                        }
                    
                    # Try to find manufacturer ID
                    manufacturer_id = self.find_manufacturer_id(manufacturer_name)
                    if not manufacturer_id:
                        print(f"   ❌ Could not find manufacturer ID for '{manufacturer_name}'")
                        return {
                            "status": "error",
                            "internal_pn": internal_part,
                            "manufacturer_pn": manufacturer_part,
                            "error": f"Manufacturer '{manufacturer_name}' not found in database"
                        }
                    
                    # Retry search with manufacturer filter
                    print(f"   🔄 Retrying search with manufacturer ID: {manufacturer_id}")
                    search_results = self.search_products(manufacturer_part, manufacturer_id)
                    
                    # Check if it still failed
                    if isinstance(search_results, dict) and search_results.get('error') == 'duplicate_products':
                        print(f"   ❌ Still multiple products found even with manufacturer filter")
                        return {
                            "status": "error",
                            "internal_pn": internal_part,
                            "manufacturer_pn": manufacturer_part,
                            "error": f"Multiple products found for manufacturer '{manufacturer_name}'"
                        }
            
            if not search_results or not search_results.get('Products'):
                print(f"   ❌ No products found for '{manufacturer_part}'")
                return {
                    "status": "not_found",
                    "internal_pn": internal_part,
                    "manufacturer_pn": manufacturer_part,
                    "manufacturer": manufacturer_name
                }
            
            # Show what we found
            products = search_results['Products']
            print(f"   📊 Found {len(products)} products in search results")
            
            # Debug: Show all found products
            for idx, product in enumerate(products[:3]):  # Show first 3 products
                mfr_part = product.get('ManufacturerPartNumber', 'N/A')
                digikey_part = product.get('DigiKeyPartNumber', 'N/A')
                manufacturer = product.get('Manufacturer', {}).get('Value', 'N/A')
                print(f"   🔍 Product {idx+1}: {manufacturer} {mfr_part} (DK: {digikey_part})")
            
            # Find the best matching product (look for exact manufacturer part number match)
            best_product = None
            products = search_results['Products']
            
            # First, try to find an exact match
            for product in products:
                found_mfr_part = product.get('ManufacturerPartNumber', '')
                if found_mfr_part.upper() == manufacturer_part.upper():
                    best_product = product
                    break
            
            # If no exact match, use the first result
            if not best_product and products:
                best_product = products[0]
            
            if not best_product:
                print(f"   ❌ No suitable product found for '{manufacturer_part}'")
                return {
                    "status": "not_found",
                    "internal_pn": internal_part,
                    "manufacturer_pn": manufacturer_part
                }
            
            # Use the best matching product
            product = best_product
            digikey_part = product.get('DigiKeyPartNumber', '')
            manufacturer = product.get('Manufacturer', {}).get('Value', 'Unknown')
            found_manufacturer_part = product.get('ManufacturerPartNumber', 'Unknown')
            
            print(f"   ✅ Found: {manufacturer} {found_manufacturer_part}")
            print(f"   🔗 DigiKey P/N: {digikey_part}")
            
            # Show if this was an exact match
            if found_manufacturer_part.upper() == manufacturer_part.upper():
                print(f"   🎯 Exact manufacturer part number match")
            else:
                print(f"   ⚠️  Partial match - searched: '{manufacturer_part}', found: '{found_manufacturer_part}'")
            
            # Get datasheet URL from the product details (already have it from search)
            datasheet_url = product.get('DatasheetUrl')
            
            if datasheet_url:
                # Use internal part number as filename
                filename = f"{internal_part} {manufacturer_part}.pdf"
                filepath = os.path.join(output_dir, filename)
                
                # Skip if file already exists
                if os.path.exists(filepath):
                    print(f"   ✅ File already exists, skipping download: {filename}")
                    return {
                        "status": "success",
                        "internal_pn": internal_part,
                        "manufacturer_pn": manufacturer_part,
                        "specified_manufacturer": manufacturer_name,
                        "found_pn": found_manufacturer_part,
                        "manufacturer": manufacturer,
                        "digikey_pn": digikey_part,
                        "filename": filename,
                        "url": datasheet_url,
                        "note": "File already existed"
                    }
                else:
                    print(f"   📥 Downloading datasheet as '{filename}'...")
                    download_result = self.download_datasheet(datasheet_url, output_dir, filename)
                    
                    if download_result:
                        print(f"   ✅ Downloaded successfully")
                        return {
                            "status": "success",
                            "internal_pn": internal_part,
                            "manufacturer_pn": manufacturer_part,
                            "specified_manufacturer": manufacturer_name,
                            "found_pn": found_manufacturer_part,
                            "manufacturer": manufacturer,
                            "digikey_pn": digikey_part,
                            "filename": filename,
                            "url": datasheet_url
                        }
                    else:
                        print(f"   ❌ Download failed or file corrupted")
                        return {
                            "status": "error",
                            "internal_pn": internal_part,
                            "manufacturer_pn": manufacturer_part,
                            "error": "Download failed or corrupted file",
                            "url": datasheet_url
                        }
            else:
                print(f"   ⚠️  No datasheet available")
                return {
                    "status": "no_datasheet",
                    "internal_pn": internal_part,
                    "manufacturer_pn": manufacturer_part,
                    "found": f"{manufacturer} {found_manufacturer_part}"
                }
            
        except Exception as e:
            print(f"   ❌ Error: {str(e)}")
            return {
                "status": "error",
                "internal_pn": internal_part,
                "manufacturer_pn": manufacturer_part,
                "error": str(e)
            }
    
    def parse_parts_file(self, input_file):
        """Parse the input file and return a list of (internal_part, manufacturer_part, manufacturer_name) tuples"""
        parts_list = []
        
        try:
            with open(input_file, 'r') as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    
                    # Skip empty lines and comments
                    if not line or line.startswith('#'):
                        continue
                    
                    # Split by whitespace (tab, space, etc.)
                    parts = line.split()
                    
                    if len(parts) >= 2:
                        internal_part = parts[0]
                        manufacturer_part = parts[1]
                        # Optional third column for manufacturer name
                        manufacturer_name = parts[2] if len(parts) >= 3 else None
                        parts_list.append((internal_part, manufacturer_part, manufacturer_name))
                        
        except FileNotFoundError:
            return []
        
        return parts_list
    
    def download_from_file(self, input_file, output_dir="datasheets", create_report=True, max_workers=3):
        """Read part numbers from a text file and download their datasheets using parallel processing"""
        
        # Parse the input file
        parts_list = self.parse_parts_file(input_file)
        
        if not parts_list:
            print("❌ No valid parts found in input file")
            return
        
        print(f"📋 Found {len(parts_list)} part numbers to process")
        estimated_time = len(parts_list) * 0.2  # Faster estimate with parallel processing
        print(f"⏱️  Estimated time: {estimated_time:.1f} seconds ({estimated_time/60:.1f} minutes) with {max_workers} parallel workers")
        print("=" * 60)
        
        # Track results
        results = {
            "success": [],
            "no_datasheet": [],
            "not_found": [],
            "errors": []
        }
        
        # Prepare part info for parallel processing
        part_infos = []
        for i, (internal_part, manufacturer_part, manufacturer_name) in enumerate(parts_list, 1):
            part_infos.append((i, len(parts_list), internal_part, manufacturer_part, manufacturer_name))
        
        # Process parts in parallel
        completed_count = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            future_to_part = {
                executor.submit(self.process_single_part, part_info, output_dir): part_info 
                for part_info in part_infos
            }
            
            # Process completed tasks as they finish
            for future in as_completed(future_to_part):
                part_info = future_to_part[future]
                completed_count += 1
                
                try:
                    result = future.result()
                    
                    # Categorize results
                    if result["status"] == "success":
                        results["success"].append(result)
                    elif result["status"] == "no_datasheet":
                        results["no_datasheet"].append(result)
                    elif result["status"] == "not_found":
                        results["not_found"].append(result)
                    elif result["status"] == "error":
                        results["errors"].append(result)
                        
                    print(f"\n📈 Overall Progress: {completed_count}/{len(parts_list)} parts completed")
                    
                except Exception as exc:
                    print(f"❌ Part {part_info[2]} generated an exception: {exc}")
                    results["errors"].append({
                        "internal_pn": part_info[2],
                        "manufacturer_pn": part_info[3],
                        "error": f"Exception: {str(exc)}"
                    })
        
        # Print summary
        print("\n" + "=" * 60)
        print("📊 DOWNLOAD SUMMARY")
        print("=" * 60)
        print(f"✅ Successfully downloaded: {len(results['success'])}")
        print(f"⚠️  No datasheet available: {len(results['no_datasheet'])}")
        print(f"❌ Part not found: {len(results['not_found'])}")
        print(f"❌ Errors: {len(results['errors'])}")
        
        # Create detailed report if requested
        if create_report:
            self.create_reports(output_dir, input_file, parts_list, results)
        
        return results
    
    def create_reports(self, output_dir, input_file, parts_list, results):
        """Create JSON and CSV reports of the download results"""
        
        # Ensure output directory exists
        os.makedirs(output_dir, exist_ok=True)
        
        # JSON Report
        report_file = os.path.join(output_dir, "download_report.json")
        report = {
            "generated_at": datetime.now().isoformat(),
            "input_file": input_file,
            "total_parts": len(parts_list),
            "results": results
        }
        
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=2)
        
        print(f"📄 JSON report saved to: {report_file}")
        
        # CSV Report for easy viewing in Excel
        csv_file = os.path.join(output_dir, "download_report.csv")
        with open(csv_file, 'w') as f:
            f.write("Status,Internal P/N,Manufacturer P/N,Specified Manufacturer,Found Part,Actual Manufacturer,DigiKey P/N,Filename,Notes\n")
            
            # Success entries
            for item in results['success']:
                specified_mfr = item.get('specified_manufacturer', '')
                f.write(f"Success,{item['internal_pn']},{item['manufacturer_pn']},{specified_mfr},{item['found_pn']},{item['manufacturer']},{item['digikey_pn']},{item['filename']},Downloaded\n")
            
            # No datasheet entries
            for item in results['no_datasheet']:
                specified_mfr = item.get('specified_manufacturer', '')
                found_info = item.get('found', '')
                f.write(f"No Datasheet,{item['internal_pn']},{item['manufacturer_pn']},{specified_mfr},{found_info},,,No datasheet available\n")
            
            # Not found entries
            for item in results['not_found']:
                specified_mfr = item.get('manufacturer', '')
                f.write(f"Not Found,{item['internal_pn']},{item['manufacturer_pn']},{specified_mfr},,,,Part not found in DigiKey\n")
            
            # Error entries
            for item in results['errors']:
                specified_mfr = item.get('specified_manufacturer', '')
                error_msg = item.get('error', '')
                f.write(f"Error,{item['internal_pn']},{item['manufacturer_pn']},{specified_mfr},,,,{error_msg}\n")
        
        print(f"📊 CSV report saved to: {csv_file}")

# Main script
def main():
    # Configuration
    CLIENT_ID = "GwpC5Mi9lcHxjqYA7sgKH8xlm2UbEUGYwvsA8C23wKkvKhQ7"
    CLIENT_SECRET = "es6wkGLi2UrbLR8QJVVwBTGWQoaEqNp1LQnRyIGHSLkNQrNjFSdC8xWNqGGO23DG"
    
    # Create sample input file if it doesn't exist
    sample_file = "parts_list.txt"
    if not os.path.exists(sample_file):
        with open(sample_file, 'w') as f:
            f.write("""# Parts List Format: [Internal P/N] [Manufacturer P/N]
# Lines starting with # are ignored
# Separate internal and manufacturer part numbers with spaces or tabs
""")
    
    # Initialize downloader
    downloader = DigiKeyDatasheetDownloader(CLIENT_ID, CLIENT_SECRET)
    
    # Download datasheets with parallel processing
    downloader.download_from_file(
        input_file=sample_file,
        output_dir="datasheets",
        create_report=True,
        max_workers=3  # Adjust this number based on your needs (1-5 recommended)
    )

if __name__ == "__main__":
    main()