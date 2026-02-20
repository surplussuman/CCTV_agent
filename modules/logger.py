import time
from colorama import Fore, Style, init

init(autoreset=True)

def log_info(msg):
    print(f"{Fore.CYAN}[INFO] {time.strftime('%H:%M:%S')} - {msg}")

def log_success(msg):
    print(f"{Fore.GREEN}[SUCCESS] {time.strftime('%H:%M:%S')} - {msg}")

def log_warn(msg):
    print(f"{Fore.YELLOW}[WARN] {time.strftime('%H:%M:%S')} - {msg}")

def log_error(msg):
    print(f"{Fore.RED}[ERROR] {time.strftime('%H:%M:%S')} - {msg}")

def log_stream(camera_ip, status):
    color = Fore.GREEN if "Sent" in status or "Uploaded" in status else Fore.RED
    print(f"{color}[STREAM] {camera_ip} -> {status}", end='\r')
