import requests
import os
import json
import time
from urllib.parse import urlparse, quote
from datetime import datetime, timedelta

class DigiKeyDatasheetDownloader:
    def __init__(self, client_id, client_secret):
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = None
        self.token_expiry = None
    
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
            return self.authenticate()
        return True
    
    def search_products(self, keyword):
        """Search for products by keyword"""
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
        print(f"   🔍 API Call: {url}")
        print(f"   🔑 Using ProductSearchV4 GET for part number '{keyword}'")
        
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            print(f"   ✅ Search successful (200)")
            result = response.json()
            print(f"   🔍 Raw response keys: {list(result.keys())}")
            
            # The product details endpoint returns a single product, not an array
            if 'Product' in result:
                print(f"   📦 Single product found in response")
                # Convert single product to Products array format for compatibility
                result['Products'] = [result['Product']]
                print(f"   📊 Converted to Products array with 1 item")
            else:
                print(f"   ⚠️  No 'Product' key in response")
                print(f"   📄 Full response: {result}")
            return result
        else:
            print(f"   ❌ Search failed: {response.status_code}")
            print(f"   Response: {response.text}")
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
        
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            print(f"   ✅ Product details successful (200)")
            return response.json()
        else:
            print(f"   ❌ Product details failed: {response.status_code}")
            print(f"   Response: {response.text}")
            return None
    
    def download_datasheet(self, datasheet_url, output_dir, filename):
        """Download a datasheet from the provided URL"""
        if not datasheet_url:
            return False
        
        os.makedirs(output_dir, exist_ok=True)
        filepath = os.path.join(output_dir, filename)
        
        try:
            response = requests.get(datasheet_url, stream=True)
            response.raise_for_status()
            
            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            return True
            
        except Exception as e:
            return False
    
    def parse_parts_file(self, input_file):
        """Parse the input file and return a list of (internal_part, manufacturer_part) tuples"""
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
                        parts_list.append((internal_part, manufacturer_part))
                        
        except FileNotFoundError:
            return []
        
        return parts_list
    
    def download_from_file(self, input_file, output_dir="datasheets", create_report=True):
        """Read part numbers from a text file and download their datasheets"""
        
        # Parse the input file
        parts_list = self.parse_parts_file(input_file)
        
        if not parts_list:
            print("❌ No valid parts found in input file")
            return
        
        print(f"📋 Found {len(parts_list)} part numbers to process")
        print("=" * 60)
        
        # Track results
        results = {
            "success": [],
            "no_datasheet": [],
            "not_found": [],
            "errors": []
        }
        
        # Process each part
        for i, (internal_part, manufacturer_part) in enumerate(parts_list, 1):
            print(f"\n🔄 [{i}/{len(parts_list)}] Processing:")
            print(f"   📦 Internal P/N: {internal_part}")
            print(f"   🏭 Manufacturer P/N: {manufacturer_part}")
            
            try:
                # Search for the manufacturer part number
                search_results = self.search_products(manufacturer_part)
                
                if not search_results or not search_results.get('Products'):
                    print(f"   ❌ No products found for '{manufacturer_part}'")
                    results["not_found"].append({
                        "internal_pn": internal_part,
                        "manufacturer_pn": manufacturer_part
                    })
                    continue
                
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
                    results["not_found"].append({
                        "internal_pn": internal_part,
                        "manufacturer_pn": manufacturer_part
                    })
                    continue
                
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
                    filename = f"{internal_part}.pdf"
                    
                    print(f"   📥 Downloading datasheet as '{filename}'...")
                    if self.download_datasheet(datasheet_url, output_dir, filename):
                        print(f"   ✅ Downloaded successfully")
                        results["success"].append({
                            "internal_pn": internal_part,
                            "manufacturer_pn": manufacturer_part,
                            "found_pn": found_manufacturer_part,
                            "manufacturer": manufacturer,
                            "digikey_pn": digikey_part,
                            "filename": filename,
                            "url": datasheet_url
                        })
                    else:
                        print(f"   ❌ Download failed")
                        results["errors"].append({
                            "internal_pn": internal_part,
                            "manufacturer_pn": manufacturer_part,
                            "error": "Download failed"
                        })
                else:
                    print(f"   ⚠️  No datasheet available")
                    results["no_datasheet"].append({
                        "internal_pn": internal_part,
                        "manufacturer_pn": manufacturer_part,
                        "found": f"{manufacturer} {found_manufacturer_part}"
                    })
                
            except Exception as e:
                print(f"   ❌ Error: {str(e)}")
                results["errors"].append({
                    "internal_pn": internal_part,
                    "manufacturer_pn": manufacturer_part,
                    "error": str(e)
                })
            
            # Rate limiting
            if i < len(parts_list):
                time.sleep(1)
        
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
            f.write("Status,Internal P/N,Manufacturer P/N,Found Part,Manufacturer,DigiKey P/N,Filename,Notes\n")
            
            # Success entries
            for item in results['success']:
                f.write(f"Success,{item['internal_pn']},{item['manufacturer_pn']},{item['found_pn']},{item['manufacturer']},{item['digikey_pn']},{item['filename']},Downloaded\n")
            
            # No datasheet entries
            for item in results['no_datasheet']:
                f.write(f"No Datasheet,{item['internal_pn']},{item['manufacturer_pn']},{item['found']},,,,No datasheet available\n")
            
            # Not found entries
            for item in results['not_found']:
                f.write(f"Not Found,{item['internal_pn']},{item['manufacturer_pn']},,,,,Part not found in DigiKey\n")
            
            # Error entries
            for item in results['errors']:
                f.write(f"Error,{item['internal_pn']},{item['manufacturer_pn']},,,,{item['error']}\n")
        
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
    
    # Download datasheets
    downloader.download_from_file(
        input_file=sample_file,
        output_dir="datasheets",
        create_report=True
    )

if __name__ == "__main__":
    main()