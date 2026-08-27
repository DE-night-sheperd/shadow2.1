"""
On-demand physical sensors for Shadow Core: camera and location.

Design rule (do not weaken this): every function here performs exactly one
discrete capture/lookup and returns. Nothing in this module polls, loops, or
keeps a device open in the background. If you're tempted to add a "watch
mode" here, don't -- that belongs nowhere in this project (see watcher.py,
which deliberately only watches files and screenshots, never mic/camera/GPS).

Every call is meant to be paired with a visible HUD log line and an
action_log entry in the caller (shadow_core.py / agent_loop.py) so there is
never a silent capture.
"""
import os
import subprocess
import time
import json
import urllib.request
import urllib.error

CAMERA_PATH = "/tmp/holo_camera.png"


def capture_camera_frame(path=CAMERA_PATH):
    """
    Grab exactly one frame from the default camera and save it to `path`.
    Returns (True, path) on success or (False, error_message) on failure.
    Tries OpenCV first, then falls back to common CLI grabbers.
    """
    try:
        import cv2
        cap = cv2.VideoCapture(0)
        try:
            if not cap.isOpened():
                return False, "Camera device could not be opened."
            ok, frame = cap.read()
            if not ok:
                return False, "Camera opened but returned no frame."
            cv2.imwrite(path, frame)
            return True, path
        finally:
            cap.release()  # always release immediately -- never leave the device open
    except ImportError:
        pass
    except Exception as e:
        return False, f"OpenCV camera error: {e}"

    # CLI fallbacks for machines without opencv-python installed
    cli_tools = [
        ["fswebcam", "-r", "1280x720", "--no-banner", path],   # Linux
        ["imagesnap", path],                                    # macOS
    ]
    for cmd in cli_tools:
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            if os.path.exists(path):
                return True, path
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            continue

    return False, "No working camera backend found (tried OpenCV, fswebcam, imagesnap)."


def get_location(timeout=8):
    """
    One-shot, IP-based approximate location lookup. Not GPS-precision, not
    continuous, not stored anywhere by this function -- the caller decides
    whether to log/remember it.

    If a real GPS device is present via gpsd, prefer that instead (more
    precise) -- falls back to IP geolocation otherwise.
    Returns a dict on success, or {"error": "..."} on failure.
    """
    gps_result = _try_gpsd(timeout=1.5)
    if gps_result:
        return gps_result

    try:
        req = urllib.request.Request(
            "http://ip-api.com/json/", headers={"User-Agent": "ShadowCore-Agent/1.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("status") == "success":
            return {
                "source": "ip_geolocation",
                "lat": data.get("lat"),
                "lon": data.get("lon"),
                "city": data.get("city"),
                "region": data.get("regionName"),
                "country": data.get("country"),
                "accuracy_note": "IP-based -- city-level accuracy, not precise GPS.",
            }
        return {"error": f"Geolocation service returned: {data.get('message', 'unknown error')}"}
    except urllib.error.URLError as e:
        return {"error": f"Location lookup failed (network): {e}"}
    except Exception as e:
        return {"error": f"Location lookup failed: {e}"}


def _try_gpsd(timeout=1.5):
    """Best-effort single fix from a local gpsd daemon, if present. Never blocks long."""
    try:
        import gps
        session = gps.gps(mode=gps.WATCH_ENABLE)
        deadline = time.time() + timeout
        fix = None
        while time.time() < deadline:
            report = session.next()
            if report.get("class") == "TPV" and getattr(report, "lat", None):
                fix = {
                    "source": "gpsd",
                    "lat": report.lat,
                    "lon": report.lon,
                    "accuracy_note": "Hardware GPS fix via gpsd.",
                }
                break
        session.close()
        return fix
    except Exception:
        return None
