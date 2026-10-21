
import network
import socket
import time
import os
import gc
import machine
import hashlib

try:
    import urequests as requests
except ImportError:
    import requests


# =====================================
# Wi-Fi 設定
# =====================================
SSID = "labor"
PASSWORD = "98668800"

# =====================================
# OTA 設定
# =====================================
HOST = "esp32"
CURRENT_VERSION = "0.1.6"

VERSION_URL = (
    "https://simonch101.github.io/"
    "v0.0.5/version.json"
)

ALLOWED_PREFIX = (
    "https://simonch101.github.io/v0.0.5/"
)

OTA_CHECK_INTERVAL = 6 * 60 * 60
last_ota_check = time.time()

# 網頁登入帳密
USERNAME = "admin"
LOGIN_PASSWORD = "admin"

# 這是簡易登入 Session，重啟後會失效
SESSION_ID = "esp32-ota-session"

MAX_FILE_SIZE = 256 * 1024


# =====================================
# Wi-Fi 連線
# =====================================
def setup_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    if not wlan.isconnected():
        print("Connecting to WiFi...")

        wlan.connect(SSID, PASSWORD)

        timeout = 30

        while not wlan.isconnected() and timeout > 0:
            print(".", end="")
            time.sleep(1)
            timeout -= 1

    if not wlan.isconnected():
        raise RuntimeError("WiFi connection failed")

    print("\nWiFi connected")
    print("IP:", wlan.ifconfig()[0])

    return wlan


# =====================================
# 版本比較
# 支援 1.0.0、1.2.3、2.0.0
# =====================================
def is_newer_version(remote, current):
    try:
        r = tuple(int(x) for x in remote.split("."))
        c = tuple(int(x) for x in current.split("."))

        if len(r) != 3 or len(c) != 3:
            return False

        if any(x < 0 for x in r + c):
            return False

        return r > c

    except (ValueError, AttributeError):
        return False


# =====================================
# HTTP GET
# =====================================
def http_get(url):
    response = requests.get(url, timeout=20)

    try:
        if response.status_code != 200:
            raise RuntimeError(
                "HTTP error: {}".format(response.status_code)
            )

        return response.content

    finally:
        response.close()


# =====================================
# GitHub OTA 檢查
#
# version.json 格式：
# {
#   "version": "1.0.2",
#   "firmware": "https://.../main.py"
# }
#
# 可選 sha256 欄位：
# {
#   "version": "1.0.2",
#   "firmware": "https://.../main.py",
#   "sha256": "新版 main.py 的 SHA-256"
# }
# =====================================
def check_for_update():
    global CURRENT_VERSION

    print("[OTA] Checking GitHub...")

    try:
        # 讀取版本資訊
        raw = http_get(VERSION_URL)
        import ujson

        manifest = ujson.loads(raw.decode("utf-8"))

        remote_version = manifest.get("version")
        firmware_url = manifest.get("firmware")
        expected_sha256 = manifest.get("sha256")

        if not isinstance(remote_version, str):
            raise ValueError("Missing version")

        if not isinstance(firmware_url, str):
            raise ValueError("Missing firmware URL")

        # 僅接受指定 GitHub Pages 網址
        if not firmware_url.startswith(ALLOWED_PREFIX):
            raise ValueError("Firmware URL rejected")

        if not firmware_url.endswith(".py"):
            raise ValueError("Only Python files are allowed")

        print("[OTA] Current:", CURRENT_VERSION)
        print("[OTA] Remote:", remote_version)

        if not is_newer_version(
            remote_version, CURRENT_VERSION
        ):
            print("[OTA] Already up to date")
            return False

        print("[OTA] New version found")

        # 下載新版 Python 程式
        firmware = http_get(firmware_url)

        if not firmware:
            raise ValueError("Empty firmware")

        if len(firmware) > MAX_FILE_SIZE:
            raise ValueError("File too large")

        # 檢查 Python 語法
        source = firmware.decode("utf-8")
        compile(source, "main.py", "exec")

        # 若 manifest 有 SHA-256，則驗證檔案完整性
        if expected_sha256:
            digest = hashlib.sha256(firmware).hexdigest()

            if digest.lower() != expected_sha256.lower():
                raise ValueError("SHA-256 mismatch")

        # 先寫入暫存檔
        with open("/main.new", "wb") as f:
            f.write(firmware)

        # 驗證暫存檔已成功寫入
        with open("/main.new", "rb") as f:
            check = f.read()

        if check != firmware:
            raise ValueError("File verification failed")

        # 備份舊程式，再替換主程式
        try:
            os.remove("/main.bak")
        except OSError:
            pass

        try:
            os.rename("/main.py", "/main.bak")
        except OSError:
            pass

        try:
            os.rename("/main.new", "/main.py")
        except Exception:
            try:
                os.rename("/main.bak", "/main.py")
            except Exception:
                pass
            raise

        # 儲存版本號
        with open("/version.txt", "w") as f:
            f.write(remote_version)

        CURRENT_VERSION = remote_version

        print("[OTA] Update successful")
        print("[OTA] Version:", CURRENT_VERSION)
        print("[OTA] Rebooting...")

        time.sleep(1)
        machine.reset()

    except Exception as e:
        print("[OTA] Update failed:", e)

        try:
            os.remove("/main.new")
        except OSError:
            pass

        return False


# =====================================
# HTML 登入頁
# =====================================
LOGIN_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width">
<title>ESP32 Login</title>
</head>
<body style="font-family:Arial;text-align:center">
<h2>ESP32 Login Page</h2>
<form action="/login" method="POST">
<p>Username: <input name="userid" required></p>
<p>Password: <input type="password"
name="pwd" required></p>
<button type="submit">Login</button>
</form>
</body>
</html>
"""


# =====================================
# OTA 管理頁
# =====================================
OTA_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width">
<title>ESP32 GitHub OTA</title>
</head>
<body style="font-family:Arial;text-align:center">
<h2>ESP32 GitHub OTA</h2>
<p>Automatic version checking is enabled.</p>
<p>Current version: VERSION_PLACEHOLDER</p>
<form action="/check" method="POST">
<button type="submit">Check GitHub Version Now</button>
</form>
</body>
</html>
"""


# =====================================
# HTTP 回應
# =====================================
def send_response(client, status, content_type, body,
                  extra=""):
    if isinstance(body, str):
        body = body.encode()

    header = (
        "HTTP/1.1 " + status + "\r\n"
        "Content-Type: " + content_type + "\r\n"
        "Content-Length: " + str(len(body)) + "\r\n"
        "Connection: close\r\n"
        + extra + "\r\n"
    )

    client.sendall(header.encode())
    client.sendall(body)


def redirect(client, location, cookie=""):
    extra = "Location: " + location + "\r\n"

    if cookie:
        extra += "Set-Cookie: " + cookie + "\r\n"

    send_response(
        client, "303 See Other", "text/plain",
        "Redirecting", extra
    )


# =====================================
# 讀取 HTTP 請求
# =====================================
def read_request(client):
    client.settimeout(5)
    data = b""

    while b"\r\n\r\n" not in data:
        chunk = client.recv(1024)

        if not chunk:
            raise ValueError("Incomplete request")

        data += chunk

        if len(data) > 8192:
            raise ValueError("Header too large")

    head, body = data.split(b"\r\n\r\n", 1)
    lines = head.decode().split("\r\n")
    parts = lines[0].split()

    if len(parts) < 2:
        raise ValueError("Invalid request")

    method = parts[0]
    path = parts[1].split("?", 1)[0]
    headers = {}

    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.lower().strip()] = v.strip()

    length = int(headers.get("content-length", "0"))

    if length > 4096:
        raise ValueError("Request too large")

    while len(body) < length:
        chunk = client.recv(length - len(body))

        if not chunk:
            raise ValueError("Incomplete body")

        body += chunk

    return method, path, headers, body[:length]


def parse_form(body):
    result = {}

    for item in body.decode().split("&"):
        if "=" in item:
            k, v = item.split("=", 1)
            result[k] = v.replace("+", " ")

    return result


def logged_in(headers):
    cookie = headers.get("cookie", "")
    return ("session=" + SESSION_ID) in cookie


# =====================================
# Web Server
# =====================================
def start_server():
    wlan = network.WLAN(network.STA_IF)

    addr = socket.getaddrinfo("0.0.0.0", 80)[0][-1]
    server = socket.socket()

    server.setsockopt(
        socket.SOL_SOCKET, socket.SO_REUSEADDR, 1
    )

    server.bind(addr)
    server.listen(2)
    server.settimeout(1)

    print("Web server started")
    print("Open http://" + wlan.ifconfig()[0])
    print("Version:", CURRENT_VERSION)

    return server


# =====================================
# 主程式
# =====================================
def main():
    global last_ota_check

    # 若先前儲存過版本號，讀取它
    try:
        with open("/version.txt", "r") as f:
            saved_version = f.read().strip()

        if is_newer_version(saved_version, CURRENT_VERSION):
            CURRENT_VERSION = saved_version
    except OSError:
        pass

    wlan = setup_wifi()

    # mDNS API 依韌體版本而異，失敗時使用 IP
    try:
        import mdns
        mdns_server = mdns.Server(wlan)
        mdns_server.set_hostname(HOST)
        mdns_server.start()
        print("mDNS started")
    except Exception as e:
        print("mDNS unavailable:", e)

    server = start_server()

    # 開機後檢查一次
    check_for_update()
    last_ota_check = time.time()

    while True:
        client = None

        try:
            # 每 6 小時自動檢查
            if time.time() - last_ota_check >= OTA_CHECK_INTERVAL:
                last_ota_check = time.time()
                check_for_update()

            try:
                client, remote = server.accept()
            except OSError:
                continue

            method, path, headers, body = read_request(client)

            if method == "GET" and path == "/":
                send_response(
                    client, "200 OK", "text/html",
                    LOGIN_HTML
                )

            elif method == "POST" and path == "/login":
                form = parse_form(body)

                if (
                    form.get("userid") == USERNAME
                    and form.get("pwd") == LOGIN_PASSWORD
                ):
                    redirect(
                        client,
                        "/serverIndex",
                        "session=" + SESSION_ID
                        + "; HttpOnly; SameSite=Strict; Path=/"
                    )
                else:
                    send_response(
                        client, "401 Unauthorized",
                        "text/plain",
                        "Wrong username or password"
                    )

            elif method == "GET" and path == "/serverIndex":
                if not logged_in(headers):
                    redirect(client, "/")
                else:
                    page = OTA_HTML.replace(
                        "VERSION_PLACEHOLDER",
                        CURRENT_VERSION
                    )
                    send_response(
                        client, "200 OK", "text/html", page
                    )

            elif method == "POST" and path == "/check":
                if not logged_in(headers):
                    redirect(client, "/")
                else:
                    send_response(
                        client, "200 OK", "text/plain",
                        "Checking GitHub. See Serial Monitor."
                    )
                    client.close()
                    client = None

                    last_ota_check = time.time()
                    check_for_update()

            else:
                send_response(
                    client, "404 Not Found",
                    "text/plain", "Not Found"
                )

        except Exception as e:
            print("HTTP error:", e)

            if client:
                try:
                    send_response(
                        client, "400 Bad Request",
                        "text/plain", "Bad Request"
                    )
                except:
                    pass

        finally:
            if client:
                try:
                    client.close()
                except:
                    pass

            gc.collect()


main()
