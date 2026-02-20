import urllib.parse
from modules.logger import log_info, log_warn


class RTSPBuilder:
    def __init__(self, ip=None, username=None, password=None):
        """
        RTSP URL builder. Supports two usage styles:
        1) Legacy: builder = RTSPBuilder(); urls = builder.build_url(ip, username, password)
           -> returns a list of candidate URLs for many vendor templates.
        2) New: builder = RTSPBuilder(ip, username, password); url = builder.build_url(channel, subtype)
           -> returns a single RTSP URL string for the given channel/subtype using a sensible default template.

        This keeps backward compatibility while matching the `gui_agent` expectations.
        """
        # Store optional instance credentials
        self.ip = ip
        self.username = username
        self.password = password

        # Base path templates with {channel} placeholder
        self.vendor_templates = [
            "/cam/realmonitor?channel={channel}&subtype=0",
            "/cam/realmonitor?channel={channel}&subtype=1",
            "/Streaming/Channels/{channel}01",
            "/Streaming/Channels/{channel}02",
            "/live/ch{channel}",
            "/h264_ch{channel}_main_stream",
            "/h264_ch{channel}_sub_stream",
            "/{channel}",
            "/ch{channel}",
            "/stream{channel}",
        ]

        # How many channels to scan (most cameras have 1-16 channels)
        self.max_channels = 16

    def _safe(self, s):
        try:
            return urllib.parse.quote(str(s), safe='')
        except:
            return str(s)

    def build_url(self, *args, path_override=None):
        """
        Flexible builder:
        - If called as build_url(channel:int, subtype:int) and the instance was created
          with ip/username/password, returns a single RTSP URL string.
        - Otherwise falls back to legacy behaviour: build_url(ip, username, password, path_override=None)
          and returns a list of candidate URLs.
        """
        # Channel/subtype usage (new)
        if len(args) == 2 and isinstance(args[0], int):
            channel, subtype = args
            if not all([self.ip, self.username, self.password]):
                raise ValueError("RTSPBuilder requires ip, username and password when building channel URLs")

            safe_user = self._safe(self.username)
            safe_pass = self._safe(self.password)

            # If a path_override is provided use it; otherwise construct a common CP Plus-like path
            if path_override:
                path = path_override
            else:
                path = f"/cam/realmonitor?channel={channel}&subtype={subtype}"

            return f"rtsp://{safe_user}:{safe_pass}@{self.ip}:554{path}"

        # Legacy behaviour: build_url(ip, username, password, path_override=None) -> list
        # Support both explicit args or instance-stored credentials
        if len(args) >= 3 and isinstance(args[0], str):
            ip = args[0]
            username = args[1]
            password = args[2]
        else:
            ip = self.ip
            username = self.username
            password = self.password

        if not all([ip, username, password]):
            raise ValueError("IP, username and password required to build RTSP URL list")

        safe_user = self._safe(username)
        safe_pass = self._safe(password)

        urls_to_try = []
        if path_override:
            urls_to_try.append(f"rtsp://{safe_user}:{safe_pass}@{ip}:554{path_override}")
            return urls_to_try

        # Generate many vendor template combinations
        priority_channels = [1, 2, 3, 4]
        remaining_channels = [c for c in range(5, self.max_channels + 1)]
        all_channels = priority_channels + remaining_channels

        for channel in all_channels:
            for template in self.vendor_templates:
                path = template.format(channel=channel)
                url = f"rtsp://{safe_user}:{safe_pass}@{ip}:554{path}"
                urls_to_try.append(url)

        return urls_to_try

    def extract_path(self, full_url):
        """
        Extracts just the path portion from a full RTSP URL.
        Example: rtsp://user:pass@192.168.1.10:554/cam/realmonitor?channel=2&subtype=0
                 Returns: /cam/realmonitor?channel=2&subtype=0
        """
        try:
            if ':554' in full_url:
                return full_url.split(':554', 1)[1]
            return None
        except:
            return None
