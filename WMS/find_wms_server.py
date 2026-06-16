"""
WMS Server Discovery Client
This script automatically finds the WMS server on your network
"""
from zeroconf import ServiceBrowser, Zeroconf, ServiceListener
import time
import webbrowser

class WMSListener(ServiceListener):
    def __init__(self):
        self.server_found = False
        self.server_url = None
    
    def add_service(self, zeroconf, service_type, name):
        if "WMS-Warehouse-Management" in name:
            info = zeroconf.get_service_info(service_type, name)
            if info:
                # Get IP address
                ip = ".".join(str(b) for b in info.addresses[0])
                port = info.port
                self.server_url = f"http://{ip}:{port}"
                self.server_found = True
                print(f"\n✅ WMS Server Found!")
                print(f"📍 URL: {self.server_url}")
                print(f"🌐 Opening browser...\n")
    
    def remove_service(self, zeroconf, service_type, name):
        pass
    
    def update_service(self, zeroconf, service_type, name):
        pass

def find_wms_server():
    print("="*60)
    print("🔍 Searching for WMS Server on network...")
    print("="*60)
    print("Please wait...")
    
    zeroconf = Zeroconf()
    listener = WMSListener()
    browser = ServiceBrowser(zeroconf, "_http._tcp.local.", listener)
    
    # Wait up to 10 seconds for server discovery
    timeout = 10
    elapsed = 0
    while not listener.server_found and elapsed < timeout:
        time.sleep(1)
        elapsed += 1
        print(".", end="", flush=True)
    
    print("\n")
    
    if listener.server_found:
        # Open browser
        webbrowser.open(listener.server_url)
    else:
        print("❌ WMS Server not found!")
        print("   Make sure the server is running and on the same network.")
    
    zeroconf.close()
    return listener.server_url

if __name__ == "__main__":
    try:
        find_wms_server()
        input("\nPress Enter to exit...")
    except KeyboardInterrupt:
        print("\n\n🛑 Search cancelled by user")
