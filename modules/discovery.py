import socket
from concurrent.futures import ThreadPoolExecutor
from modules.logger import log_info, log_success, log_warn

def get_local_ip():
    """
    Gets the local IP address of this machine.
    Used to determine which subnet to scan.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

def check_rtsp_port(ip):
    """
    Checks if port 554 (RTSP) is open on the given IP.
    Returns the IP if open, None otherwise.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.3)  # Fast timeout for scanning
        result = sock.connect_ex((ip, 554))
        sock.close()
        if result == 0:
            return ip
    except:
        pass
    return None

def scan_network(custom_subnet=None):
    """
    Scans the local network for devices with port 554 open (RTSP cameras).
    
    Args:
        custom_subnet: Optional subnet to scan (e.g., "192.168.100")
                      If not provided, uses the local machine's subnet
    
    Returns:
        List of IP addresses with RTSP port open
    """
    if custom_subnet:
        base_ip = custom_subnet
    else:
        local_ip = get_local_ip()
        base_ip = ".".join(local_ip.split('.')[:3])
    
    log_info(f"Scanning Network: {base_ip}.0/24 (This may take 10-30 seconds)")
    
    # Generate all IPs in the subnet
    ips = [f"{base_ip}.{i}" for i in range(1, 255)]
    found_cameras = []
    
    # Parallel scanning for speed
    with ThreadPoolExecutor(max_workers=100) as executor:
        results = executor.map(check_rtsp_port, ips)
        
    for ip in results:
        if ip:
            found_cameras.append(ip)
            log_success(f"Found Camera: {ip}")
    
    if not found_cameras:
        log_warn(f"No cameras found on {base_ip}.0/24")
    
    return found_cameras

def scan_specific_ip(ip):
    """
    Checks if a specific IP has RTSP port open.
    Useful for manual IP entry.
    
    Args:
        ip: IP address to check
    
    Returns:
        True if RTSP port is open, False otherwise
    """
    log_info(f"Checking {ip}:554...")
    result = check_rtsp_port(ip)
    if result:
        log_success(f"{ip} has RTSP port open")
        return True
    else:
        log_warn(f"{ip} is not responding on port 554")
        return False
