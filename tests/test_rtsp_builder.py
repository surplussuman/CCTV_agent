import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from modules.rtsp_builder import RTSPBuilder

b = RTSPBuilder('192.168.100.11','admin','admin@123')
print('single url:', b.build_url(2,0))
urls = RTSPBuilder().build_url('192.168.100.11','admin','admin@123')
print('candidate count:', len(urls))
print('first candidate:', urls[0])
