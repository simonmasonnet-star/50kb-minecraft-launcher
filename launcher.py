#!/usr/bin/env python3

import json
import os
import sys
import shutil
import platform
import subprocess
import zipfile
import uuid
import time
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

from lang import t, load_lang

# ---------- 下载加速配置 ----------
MAX_WORKERS = 10  # 并行下载线程数，可根据网络调整

# ---------- ANSI 颜色与清屏 ----------
class Colors:
    RESET = '\033[0m'
    BOLD = '\033[1m'
    CYAN = '\033[96m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    GREEN = '\033[92m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    WHITE = '\033[97m'

def clear_screen():
    """跨平台清屏"""
    os.system('cls' if os.name == 'nt' else 'clear')

def colored_print(text, color=Colors.RESET, bold=False, end='\n'):
    """带颜色美化打印"""
    prefix = Colors.BOLD if bold else ""
    print(f"{prefix}{color}{text}{Colors.RESET}", end=end)

def colored_input(prompt, color=Colors.WHITE):
    """彩色提示输入"""
    print(f"{color}{prompt}{Colors.RESET}", end='')
    return input()

# ---------- 配置目录 ----------
BASE_DIR = Path.cwd()
CONFIG_FILE = BASE_DIR / "launcher_config.json"
MINECRAFT_DIR = BASE_DIR / ".minecraft"
VERSIONS_DIR = MINECRAFT_DIR / "versions"
LIBRARIES_DIR = MINECRAFT_DIR / "libraries"
NATIVES_DIR = MINECRAFT_DIR / "natives"
ASSETS_DIR = MINECRAFT_DIR / "assets"

# 资源下载根 URL
RESOURCES_BASE_URL = "https://resources.download.minecraft.net/"

# 创建必要目录
for d in [VERSIONS_DIR, LIBRARIES_DIR, NATIVES_DIR, ASSETS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ---------- 配置结构 ----------
# 兼容旧版本配置，升级为包含 java_versions, accounts 等字段的结构
DEFAULT_CONFIG = {
    "java_versions": [],          # [{"alias": "JDK17", "path": "..."}]
    "current_java_alias": None,   # 当前使用的 Java 别名
    "accounts": [],               # [{"username": "...", "uuid": "...", "accessToken": "...", "userType": "...", "alias": "..."}]
    "current_account_index": -1,  # 当前使用的账号在列表中的索引（-1表示无）
    "current_version": None        # 当前选择的 Minecraft 版本
}

def load_config():
    """加载配置文件，自动升级旧版配置"""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            # 旧配置可能只有 java_path, auth_info, current_version
            if "java_path" in data and "java_versions" not in data:
                # 升级：将旧 java_path 转为 java_versions
                old_path = data.pop("java_path")
                data["java_versions"] = [{"alias": "default", "path": old_path}]
                data["current_java_alias"] = "default"
            else:
                # 确保 java_versions 存在
                if "java_versions" not in data:
                    data["java_versions"] = []
                if "current_java_alias" not in data:
                    data["current_java_alias"] = None
            # 升级 auth_info -> accounts
            if "auth_info" in data and "accounts" not in data:
                old_auth = data.pop("auth_info")
                if old_auth:
                    # 为旧账号添加别名
                    alias = f"{old_auth['username']}_{old_auth.get('userType','unknown')}"
                    # 自动判定离线账号（accessToken为0）
                    user_type = "offline" if old_auth.get("accessToken") == "0" else old_auth.get("userType", "mojang")
                    data["accounts"] = [{
                        "username": old_auth["username"],
                        "uuid": old_auth["uuid"],
                        "accessToken": old_auth["accessToken"],
                        "userType": user_type,  # 自动修正
                        "alias": alias
                    }]
                    data["current_account_index"] = 0
                else:
                    data["accounts"] = []
                    data["current_account_index"] = -1
            else:
                if "accounts" not in data:
                    data["accounts"] = []
                if "current_account_index" not in data:
                    data["current_account_index"] = -1
                # 自动修正现有账号的userType（离线账号accessToken为0）
                for acc in data["accounts"]:
                    if acc.get("accessToken") == "0" and acc.get("userType") == "mojang":
                        acc["userType"] = "offline"
            if "current_version" not in data:
                data["current_version"] = None
            return data
        except json.JSONDecodeError:
            colored_print("[警告] 配置文件损坏，将使用默认配置", Colors.YELLOW)
            return DEFAULT_CONFIG.copy()
    return DEFAULT_CONFIG.copy()
def save_config(cfg):
    """保存配置文件"""
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception as e:
        colored_print(f"[错误] 保存配置失败: {e}", Colors.RED)

# ---------- 工具函数 ----------
def download_file_parallel(url, dest, expected_size=None):
    """多线程环境下的文件下载，返回成功/失败"""
    dest = Path(dest)
    if dest.exists():
        if expected_size is not None and dest.stat().st_size != expected_size:
            dest.unlink()
        else:
            return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        urllib.request.urlretrieve(url, dest)
        return True
    except Exception:
        if dest.exists():
            dest.unlink()
        return False

def download_file(url, dest, desc="", expected_size=None):
    """单文件下载（带进度条和跳过检查）"""
    dest = Path(dest)
    desc_str = f"  {desc}" if desc else f"  {dest.name}"
    if dest.exists():
        if expected_size is not None and dest.stat().st_size != expected_size:
            colored_print(f"[重新下载] 文件大小不匹配: {desc_str}", Colors.YELLOW)
            dest.unlink()
        else:
            colored_print(f"[跳过] 已存在: {desc_str}", Colors.GREEN)
            return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    colored_print(f"[下载] {desc_str}", Colors.CYAN)
    try:
        urllib.request.urlretrieve(url, dest, reporthook=_progress_hook)
        print()
        return True
    except Exception as e:
        colored_print(f"\n[错误] 下载失败: {e}", Colors.RED)
        if dest.exists():
            dest.unlink()
        return False

def _progress_hook(block_num, block_size, total_size):
    downloaded = block_num * block_size
    if total_size > 0:
        percent = min(100, downloaded * 100 // total_size)
        print(f"\r  [{percent}%] {downloaded}/{total_size} 字节", end='', flush=True)
    else:
        print(f"\r  已下载: {downloaded} 字节", end='', flush=True)

def get_java_executable():
    """自动检测系统中的Java可执行文件"""
    if platform.system() == "Windows":
        java_home = os.environ.get("JAVA_HOME")
        if java_home:
            for exe in ("javaw.exe", "java.exe"):
                p = Path(java_home) / "bin" / exe
                if p.exists():
                    return str(p)
        for p in os.environ.get("PATH", "").split(os.pathsep):
            for exe in ("java.exe", "javaw.exe"):
                full = Path(p) / exe
                if full.exists():
                    return str(full)
    else:
        for p in os.environ.get("PATH", "").split(os.pathsep):
            exe = Path(p) / "java"
            if exe.exists() and os.access(exe, os.X_OK):
                return str(exe)
    return None

def check_java_version(java_path):
    """检查Java版本"""
    try:
        result = subprocess.run(
            [java_path, "-version"], 
            capture_output=True, 
            text=True, 
            timeout=10,
            encoding='utf-8'
        )
        for line in (result.stderr or result.stdout).splitlines():
            if 'version' in line:
                return line.strip()
    except Exception as e:
        colored_print(f"[错误] 检查Java版本失败: {e}", Colors.RED)
        return None
    return None

# ---------- 账号登录 ----------
def login_offline(username):
    """离线登录（返回账号信息）"""
    import hashlib
    import struct

    if not username or username.strip() == "":
        username = "Player"
    username = username.strip()

    name_bytes = f"OfflinePlayer:{username}".encode('utf-8')
    md5_digest = hashlib.md5(name_bytes).digest()
    md5_bytes = bytearray(md5_digest)
    md5_bytes[6] = (md5_bytes[6] & 0x0F) | 0x30
    md5_bytes[8] = (md5_bytes[8] & 0x3F) | 0x80
    time_low, time_mid, time_hi, clk_seq, node = struct.unpack(">IHHH6s", bytes(md5_bytes))
    uid = f"{time_low:08x}-{time_mid:04x}-{time_hi:04x}-{clk_seq:04x}-{node.hex()[:12]}"
    alias = f"{username}_offline"
    return {
        "username": username,
        "uuid": uid,
        "accessToken": "0",
        "userType": "offline",  
        "xuid": "0",
        "clientid": "0",
        "alias": alias
    }

def login_microsoft():
    """修复 AADSTS700016 的终极配置"""
    CLIENT_ID = "00000000402b5328"
    SCOPE = "XboxLive.signin offline_access"
    
    # 终极修复：使用 login.live.com 接口，这是微软专门为个人账户保留的 OAuth2 入口
    # 它会自动处理租户定位问题，绕过 Azure AD 的租户检查
    DEVICE_CODE_URL = "https://login.live.com/oauth20_device.srf"
    TOKEN_URL = "https://login.live.com/oauth20_token.srf"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    try:
        # 获取设备代码
        req_params = {
            "client_id": CLIENT_ID,
            "scope": SCOPE,
            "response_type": "device_code"
        }
        req_data = urllib.parse.urlencode(req_params).encode('utf-8')
        
        req = urllib.request.Request(DEVICE_CODE_URL, data=req_data, headers=headers)
        with urllib.request.urlopen(req) as resp:
            device = json.load(resp)
            
        print(f"\n请打开浏览器访问: {Colors.GREEN}{device['verification_uri']}{Colors.RESET}")
        print(f"输入验证码: {Colors.BOLD}{Colors.YELLOW}{device['user_code']}{Colors.RESET}")
        colored_print("等待你在浏览器中完成授权...", Colors.YELLOW)

        # --- 步骤 2: 轮询 Access Token ---
        interval = device.get("interval", 5)
        deadline = time.time() + device.get("expires_in", 900)
        ms_access_token = None
        
        while time.time() < deadline:
            time.sleep(interval)
            token_params = {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": CLIENT_ID,
                "device_code": device["device_code"]
            }
            token_data = urllib.parse.urlencode(token_params).encode('utf-8')
            token_req = urllib.request.Request(TOKEN_URL, data=token_data, headers=headers)
            
            try:
                with urllib.request.urlopen(token_req) as resp:
                    res = json.load(resp)
                    if "access_token" in res:
                        ms_access_token = res["access_token"]
                        break
            except urllib.error.HTTPError as e:
                # 400 错误可能是由于用户尚未完成点击授权，继续轮询
                err_content = e.read().decode()
                if "authorization_pending" not in err_content:
                    colored_print(f"[错误] 令牌请求失败: {err_content}", Colors.RED)
                    return None

        if not ms_access_token:
            colored_print("[错误] 登录超时或已取消", Colors.RED)
            return None

        # --- 步骤 3: Xbox Live (XBL) 认证 ---
        colored_print("[Xbox 认证] 正在获取 XBL 令牌...", Colors.CYAN)
        xbl_url = "https://user.auth.xboxlive.com/user/authenticate"
        xbl_payload = json.dumps({
            "Properties": {
                "AuthMethod": "RPS",
                "SiteName": "user.auth.xboxlive.com",
                "RpsTicket": f"d={ms_access_token}"
            },
            "RelyingParty": "http://auth.xboxlive.com",
            "TokenType": "JWT"
        }).encode('utf-8')
        
        xbl_req = urllib.request.Request(xbl_url, data=xbl_payload, headers={"Content-Type": "application/json", "Accept": "application/json"})
        with urllib.request.urlopen(xbl_req) as resp:
            xbl_res = json.load(resp)
        
        xbl_token = xbl_res["Token"]
        uhs = xbl_res["DisplayClaims"]["xui"][0]["uhs"]

        # --- 步骤 4: XSTS 认证 ---
        colored_print("[Xbox 认证] 正在获取 XSTS 令牌...", Colors.CYAN)
        xsts_url = "https://xsts.auth.xboxlive.com/xsts/authorize"
        xsts_payload = json.dumps({
            "Properties": {
                "SandboxId": "RETAIL",
                "UserTokens": [xbl_token]
            },
            "RelyingParty": "rp://api.minecraftservices.com/",
            "TokenType": "JWT"
        }).encode('utf-8')
        
        xsts_req = urllib.request.Request(xsts_url, data=xsts_payload, headers={"Content-Type": "application/json", "Accept": "application/json"})
        with urllib.request.urlopen(xsts_req) as resp:
            xsts_res = json.load(resp)
        
        xsts_token = xsts_res["Token"]

        # --- 步骤 5: Minecraft 令牌认证 ---
        colored_print("[Minecraft] 正在执行最后的身份验证...", Colors.CYAN)
        mc_login_url = "https://api.minecraftservices.com/authentication/login"
        mc_payload = json.dumps({
            "identityToken": f"XBL3.0 x={uhs};{xsts_token}"
        }).encode('utf-8')
        
        mc_req = urllib.request.Request(mc_login_url, data=mc_payload, headers={"Content-Type": "application/json", "Accept": "application/json"})
        with urllib.request.urlopen(mc_req) as resp:
            mc_res = json.load(resp)
        
        mc_access_token = mc_res["access_token"]

        # --- 步骤 6: 获取玩家 Profile ---
        profile_req = urllib.request.Request(
            "https://api.minecraftservices.com/minecraft/profile",
            headers={"Authorization": f"Bearer {mc_access_token}"}
        )
        with urllib.request.urlopen(profile_req) as resp:
            profile = json.load(resp)

        colored_print(f"[成功] 欢迎回来, {profile['name']}!", Colors.GREEN, bold=True)
        
        return {
            "username": profile["name"],
            "uuid": profile["id"],
            "accessToken": mc_access_token,
            "userType": "msa",
            "xuid": uhs,
            "clientid": "0",
            "alias": f"{profile['name']}_msa"
        }

    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        colored_print(f"[错误] 微软服务器返回错误 (HTTP {e.code}): {error_body}", Colors.RED)
        return None
    except Exception as e:
        colored_print(f"[错误] 登录流程异常: {e}", Colors.RED)
        return None

# ---------- 版本与资源下载（多线程加速） ----------
MANIFEST_URL = "https://launchermeta.mojang.com/mc/game/version_manifest.json"

def get_version_list():
    try:
        with urllib.request.urlopen(MANIFEST_URL, timeout=10) as resp:
            return json.load(resp)["versions"]
    except Exception as e:
        colored_print(f"[错误] 获取版本列表失败: {e}", Colors.RED)
        return []

def get_version_info(version_id):
    versions = get_version_list()
    for v in versions:
        if v["id"] == version_id:
            try:
                with urllib.request.urlopen(v["url"], timeout=10) as resp:
                    return json.load(resp)
            except Exception as e:
                colored_print(f"[错误] 获取版本{version_id}信息失败: {e}", Colors.RED)
                return None
    colored_print(f"[错误] 版本{version_id}不存在", Colors.RED)
    return None

def download_assets(asset_index_info):
    """多线程下载资源文件"""
    try:
        asset_id = asset_index_info["id"]
        index_url = asset_index_info["url"]
        index_size = asset_index_info.get("size")
        index_path = ASSETS_DIR / "indexes" / f"{asset_id}.json"
        download_file(index_url, index_path, f"资源索引 {asset_id}.json", index_size)

        with open(index_path, 'r', encoding='utf-8') as f:
            index_data = json.load(f)

        objects = index_data.get("objects", {})
        total = len(objects)
        if total == 0:
            colored_print("  无资源文件需要下载", Colors.YELLOW)
            return
        
        colored_print(f"资源文件总数: {total}，使用 {MAX_WORKERS} 线程并行下载...", Colors.CYAN)
        
        tasks = []
        for name, obj in objects.items():
            h = obj["hash"]
            size = obj["size"]
            prefix = h[:2]
            dest = ASSETS_DIR / "objects" / prefix / h
            url = f"{RESOURCES_BASE_URL}{prefix}/{h}"
            tasks.append((url, dest, size))
        
        success_count = 0
        lock = threading.Lock()
        completed = 0
        
        def download_task(url, dest, size):
            nonlocal completed, success_count
            result = download_file_parallel(url, dest, size)
            with lock:
                completed += 1
                if result:
                    success_count += 1
                if completed % 50 == 0 or completed == total:
                    print(f"  资源下载进度: {completed}/{total} (成功: {success_count})")
            return result
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(download_task, url, dest, size) for url, dest, size in tasks]
            for future in as_completed(futures):
                future.result()
        
        colored_print(f"资源下载完成: 总计{total}，成功{success_count}，失败{total-success_count}", Colors.GREEN if success_count == total else Colors.YELLOW)
    except Exception as e:
        colored_print(f"[错误] 下载资源失败: {e}", Colors.RED)

def get_native_classifier():
    system = platform.system()
    arch = platform.machine().lower()
    if system == "Windows":
        return "natives-windows" if ("64" in arch or "amd64" in arch or "x86_64" in arch) else "natives-windows-x86"
    elif system == "Darwin":
        return "natives-macos-arm64" if ("arm" in arch or "aarch64" in arch) else "natives-macos"
    elif system == "Linux":
        return "natives-linux"
    else:
        colored_print(f"[警告] 不支持的系统: {system}", Colors.YELLOW)
        return None

def check_rules(rules):
    os_map = {"Windows": "windows", "Darwin": "osx", "Linux": "linux"}
    current_os = os_map.get(platform.system(), platform.system().lower())
    allowed = False
    for rule in rules:
        action = rule["action"]
        os_rule = rule.get("os", {})
        os_name = os_rule.get("name", "")
        if action == "allow":
            if not os_name or os_name == current_os:
                allowed = True
        elif action == "disallow":
            if os_name == current_os:
                allowed = False
    return allowed

def download_version(version_id):
    """下载版本：客户端 jar 单线程，库文件多线程，资源多线程"""
    colored_print(f"\n准备下载版本: {version_id}", Colors.CYAN)
    info = get_version_info(version_id)
    if not info:
        return False

    version_dir = VERSIONS_DIR / version_id
    version_dir.mkdir(parents=True, exist_ok=True)

    # 客户端 JAR
    try:
        client_info = info["downloads"]["client"]
        client_url = client_info["url"]
        client_size = client_info.get("size")
        client_jar = version_dir / f"{version_id}.jar"
        download_file(client_url, client_jar, f"客户端 {version_id}.jar", client_size)
    except KeyError:
        colored_print(f"[错误] 版本{version_id}缺少客户端下载信息", Colors.RED)
        return False

    # 收集库下载任务
    libraries = info.get("libraries", [])
    native_platform = get_native_classifier()
    native_extract_dir = NATIVES_DIR / version_id
    native_extract_dir.mkdir(parents=True, exist_ok=True)

    lib_tasks = []
    for lib in libraries:
        rules = lib.get("rules")
        if rules and not check_rules(rules):
            continue
        if "downloads" in lib:
            if "artifact" in lib["downloads"]:
                art = lib["downloads"]["artifact"]
                lib_tasks.append((art["url"], LIBRARIES_DIR / art["path"], art.get("size"), False))
            if native_platform and "classifiers" in lib["downloads"]:
                for classifier, art in lib["downloads"]["classifiers"].items():
                    if classifier == native_platform:
                        lib_tasks.append((art["url"], LIBRARIES_DIR / art["path"], art.get("size"), True))
        else:
            parts = lib["name"].split(":")
            if len(parts) == 3:
                group, artifact, version = parts
                base_path = group.replace(".", "/") + "/" + artifact + "/" + version
                jar_name = f"{artifact}-{version}.jar"
                url = f"https://libraries.minecraft.net/{base_path}/{jar_name}"
                path = f"{base_path}/{jar_name}"
                lib_tasks.append((url, LIBRARIES_DIR / path, None, False))

    total_libs = len(lib_tasks)
    colored_print(f"开始下载依赖库（共 {total_libs} 个，{MAX_WORKERS} 线程）...", Colors.CYAN)
    
    success_libs = 0
    lock = threading.Lock()
    completed_libs = 0

    def download_lib_task(url, dest, size):
        nonlocal completed_libs, success_libs
        result = download_file_parallel(url, dest, size)
        with lock:
            completed_libs += 1
            if result:
                success_libs += 1
            if completed_libs % 20 == 0 or completed_libs == total_libs:
                print(f"  库下载进度: {completed_libs}/{total_libs} (成功: {success_libs})")
        return result, dest

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(download_lib_task, url, dest, size) for url, dest, size, _ in lib_tasks]
        for future in as_completed(futures):
            future.result()

    # 解压原生库
    for url, dest, size, is_native in lib_tasks:
        if is_native and dest.exists() and dest.suffix == ".jar":
            try:
                with zipfile.ZipFile(dest, 'r') as zf:
                    for name in zf.namelist():
                        if name.endswith((".dll", ".so", ".dylib", ".jnilib")) and not "__MACOSX" in name:
                            extract_path = native_extract_dir / name
                            extract_path.parent.mkdir(parents=True, exist_ok=True)
                            if not extract_path.exists() or not os.access(extract_path, os.X_OK):
                                with open(extract_path, 'wb') as f:
                                    f.write(zf.read(name))
                                if platform.system() != "Windows":
                                    os.chmod(extract_path, 0o755)
            except Exception as e:
                colored_print(f"[警告] 解压原生库 {dest.name} 失败: {e}", Colors.YELLOW)

    colored_print(f"依赖库下载完毕 (成功 {success_libs}/{total_libs})", Colors.GREEN if success_libs == total_libs else Colors.YELLOW)

    # 资源文件
    if "assetIndex" in info:
        colored_print("\n开始下载资源文件...", Colors.CYAN)
        download_assets(info["assetIndex"])
    else:
        colored_print("[提示] 此版本无 assetIndex，跳过资源下载", Colors.YELLOW)

    # 保存版本 JSON
    try:
        with open(version_dir / f"{version_id}.json", "w", encoding="utf-8") as f:
            json.dump(info, f, indent=2)
    except Exception as e:
        colored_print(f"[警告] 保存版本JSON失败: {e}", Colors.YELLOW)

    colored_print(f"[完成] 版本 {version_id} 下载完毕", Colors.GREEN)
    return True

# ---------- 删除版本 ----------
def delete_version(version_id):
    """删除指定版本的相关文件"""
    version_dir = VERSIONS_DIR / version_id
    native_dir = NATIVES_DIR / version_id
    version_json = version_dir / f"{version_id}.json"
    version_jar = version_dir / f"{version_id}.jar"

    removed = False
    # 删除 natives
    if native_dir.exists():
        shutil.rmtree(native_dir)
        removed = True
    # 删除 version 文件夹
    if version_dir.exists():
        shutil.rmtree(version_dir)
        removed = True
    if removed:
        colored_print(f"[成功] 已删除版本 {version_id} 的所有本地文件", Colors.GREEN)
    else:
        colored_print(f"[提示] 未找到版本 {version_id} 的文件", Colors.YELLOW)

#--------- 获取库文件路径 ----------
def get_library_path(lib_name: str) -> str:
    """
    根据 Maven 坐标字符串 'group:artifact:version' 返回相对于 LIBRARIES_DIR 的路径。
    """
    parts = lib_name.split(":")
    if len(parts) != 3:
        return None
    group, artifact, version = parts
    base = group.replace(".", "/") + "/" + artifact + "/" + version
    jar_name = f"{artifact}-{version}.jar"
    return str(LIBRARIES_DIR / base / jar_name)

# ---------- 启动游戏 ----------
def launch_game(java_path, version_id, auth):
    colored_print(f"[账号信息] 用户名: {auth['username']}, 类型: {auth.get('userType')}, UUID: {auth['uuid']}", Colors.CYAN)

    version_json_path = VERSIONS_DIR / version_id / f"{version_id}.json"
    if not version_json_path.exists():
        colored_print(f"错误：找不到版本 {version_id} 的 JSON，请先下载", Colors.RED)
        return False
    
    # 自动同意EULA
    eula_path = MINECRAFT_DIR / "eula.txt"
    if not eula_path.exists():
        with open(eula_path, 'w', encoding='utf-8') as f:
            f.write("# Generated by Python Launcher\n")
            f.write("eula=true\n")
        colored_print("[提示] 已自动创建并同意EULA", Colors.GREEN)

    with open(version_json_path, 'r', encoding='utf-8') as f:
        version_data = json.load(f)

    main_class = version_data["mainClass"]
    cp = [str(VERSIONS_DIR / version_id / f"{version_id}.jar")]

    # 构建 classpath
    if "libraries" in version_data:
        for lib in version_data["libraries"]:
            rules = lib.get("rules")
            if rules and not check_rules(rules):
                continue

            if "downloads" in lib:
                if "artifact" not in lib["downloads"]:
                    continue
                path = lib["downloads"]["artifact"]["path"]
            else:
                path = get_library_path(lib["name"])
                if not path:
                    continue
            lib_path = LIBRARIES_DIR / path
            if lib_path.exists() and lib_path.suffix == ".jar":
                cp.append(str(lib_path))
    
    classpath = os.pathsep.join(cp)
    native_dir = NATIVES_DIR / version_id

    auth_token = auth.get("accessToken")
    if auth_token == "0":
        auth_token = f"OfflineToken:{auth['uuid']}"

    replace_map = {
        "${auth_player_name}": auth["username"],
        "${auth_uuid}": auth["uuid"],
        "${auth_access_token}": auth_token,
        "${user_type}": auth.get("userType", "offline"),
        "${version_name}": version_id,
        "${game_directory}": str(MINECRAFT_DIR),
        "${game_assets}": str(ASSETS_DIR),
        "${assets_root}": str(ASSETS_DIR),
        "${assets_index_name}": version_data.get("assets", version_id),
        "${auth_xuid}": auth.get("xuid", "0"),
        "${clientid}": auth.get("clientid", "0"),
        "${auth_session}": auth_token,
        "${version_type}": "release",
        "${resolution_width}": "854",
        "${resolution_height}": "480",
        "${launcher_name}": "python-launcher",
        "${launcher_version}": "0.1",
        "${classpath}": classpath,
        "${user_properties}": "{}",
        "${profile_properties}": "{}",
        "${quickPlayPath}": "",
        "${quickPlaySingleplayer}": "",
        "${quickPlayMultiplayer}": "",
        "${quickPlayRealms}": "",
    }

    def clean_and_replace(arg):
        if not isinstance(arg, str):
            return ""
        for k, v in replace_map.items():
            arg = arg.replace(k, v)
        return arg.strip()

    # --- 1. 准备 JVM 参数 ---
    jvm_args_list = [
        f"-Djava.library.path={native_dir}",
        f"-Djna.tmpdir={native_dir}",
        f"-Dorg.lwjgl.system.SharedLibraryExtractPath={native_dir}",
        f"-Dio.netty.native.workdir={native_dir}",
        f"-Dminecraft.launcher.brand={replace_map['${launcher_name}']}",
        f"-Dminecraft.launcher.version={replace_map['${launcher_version}']}",
        "-Dminecraft.demo=false", # 强制非试玩
        "-Dcom.mojang.eula.agree=true",
        f"-Dminecraft.username={auth['username']}",
        f"-Dminecraft.uuid={auth['uuid']}",
    ]

    # 合并 JSON 中的 JVM 参数
    if "arguments" in version_data and "jvm" in version_data["arguments"]:
        for arg in version_data["arguments"]["jvm"]:
            if isinstance(arg, dict):
                if check_rules(arg.get("rules", [])) and "value" in arg:
                    val = arg["value"]
                    values = val if isinstance(val, list) else [val]
                    for v in values:
                        cleaned = clean_and_replace(v)
                        if cleaned: jvm_args_list.append(cleaned)
            elif isinstance(arg, str):
                cleaned = clean_and_replace(arg)
                if cleaned: jvm_args_list.append(cleaned)

    # --- 2. 准备游戏参数 (过滤 --demo) ---
    game_args = []
    if "arguments" in version_data and "game" in version_data["arguments"]:
        for arg in version_data["arguments"]["game"]:
            if isinstance(arg, dict):
                if check_rules(arg.get("rules", [])) and "value" in arg:
                    val = arg["value"]
                    values = val if isinstance(val, list) else [val]
                    for v in values:
                        cleaned = clean_and_replace(v)
                        if cleaned and cleaned != "--demo": 
                            game_args.append(cleaned)
            elif isinstance(arg, str):
                cleaned = clean_and_replace(arg)
                if cleaned and cleaned != "--demo": 
                    game_args.append(cleaned)
    else:
        mc_args = version_data.get("minecraftArguments", "").split()
        game_args = [clean_and_replace(a) for a in mc_args if clean_and_replace(a) and clean_and_replace(a) != "--demo"]

    # --- 3. 组装最终命令
    # 顺序：Java可执行文件 -> JVM参数 -> Classpath -> 主类 -> 游戏参数
    final_cmd = [java_path, "-Xss1M", "-XX:+HeapDumpOnOutOfMemoryError"] 
    
    # JVM 参数去重（以 -D 为 Key）
    d_map = {}
    other_jvm = []
    for arg in jvm_args_list:
        if arg.startswith("-D"):
            key = arg.split("=", 1)[0]
            d_map[key] = arg
        else:
            other_jvm.append(arg)
    
    final_cmd.extend(other_jvm)
    final_cmd.extend(list(d_map.values()))
    final_cmd.extend(["-cp", classpath, main_class])
    final_cmd.extend(game_args)

    colored_print("\n启动命令：", Colors.CYAN)
    print(" ".join(final_cmd))

    try:
        proc = subprocess.Popen(final_cmd, cwd=MINECRAFT_DIR)
        colored_print("游戏已启动，等待退出...", Colors.GREEN)
        proc.wait()
        colored_print(f"游戏退出 (退出码: {proc.returncode})", Colors.YELLOW)
        return proc.returncode == 0
    except Exception as e:
        colored_print(f"启动失败: {e}", Colors.RED)
        return False
    
# ---------- 主菜单与子菜单 ----------
def get_installed_versions():
    """获取已安装版本列表"""
    if not VERSIONS_DIR.exists():
        return []
    installed = []
    for d in VERSIONS_DIR.iterdir():
        if d.is_dir() and (d / f"{d.name}.json").exists():
            installed.append(d.name)
    return installed

def java_management(config):
    """Java 版本管理子菜单"""
    while True:
        clear_screen()
        colored_print("------ Java 版本管理 ------", Colors.CYAN, bold=True)
        versions = config.get("java_versions", [])
        current_alias = config.get("current_java_alias")
        if versions:
            colored_print("已保存的 Java 版本:", Colors.YELLOW)
            for i, jv in enumerate(versions, 1):
                marker = " (当前)" if jv["alias"] == current_alias else ""
                colored_print(f"  {i}. {jv['alias']}{marker}  => {jv['path']}", Colors.WHITE)
        else:
            colored_print("暂无 Java 版本", Colors.YELLOW)
        print("\n1. 添加 Java 路径")
        print("2. 删除 Java 版本")
        print("3. 切换默认 Java")
        print("0. 返回主菜单")
        choice = input("请选择操作: ").strip()

        if choice == "1":
            # 添加 Java
            detected = get_java_executable()
            path = None
            if detected:
                ver = check_java_version(detected)
                colored_print(f"\n检测到系统 Java: {detected}", Colors.GREEN)
                if ver:
                    colored_print(f"版本信息: {ver}", Colors.GREEN)
                if input("使用此 Java? (y/n): ").strip().lower() == 'y':
                    path = detected
            if not path:
                path = input("请输入 Java 可执行文件完整路径: ").strip()
            if not path or not Path(path).exists():
                colored_print("无效的路径！", Colors.RED)
                input("按 Enter 键继续...")
                continue
            alias = input("请为此 Java 取一个别名 (如 JDK17): ").strip()
            if not alias:
                alias = path
            # 检查别名是否已存在
            if any(jv["alias"] == alias for jv in versions):
                colored_print("别名已存在，请换一个", Colors.RED)
                input("按 Enter 键继续...")
                continue
            versions.append({"alias": alias, "path": path})
            if current_alias is None:
                config["current_java_alias"] = alias
            config["java_versions"] = versions
            save_config(config)
            colored_print(f"Java 版本 '{alias}' 已添加", Colors.GREEN)
            input("按 Enter 键继续...")
        elif choice == "2":
            if not versions:
                colored_print("没有可删除的版本", Colors.YELLOW)
                input("按 Enter 键继续...")
                continue
            try:
                idx = int(input("输入要删除的序号: ").strip()) - 1
                if 0 <= idx < len(versions):
                    removed_alias = versions[idx]["alias"]
                    del versions[idx]
                    config["java_versions"] = versions
                    if current_alias == removed_alias:
                        config["current_java_alias"] = versions[0]["alias"] if versions else None
                    save_config(config)
                    colored_print(f"已删除 Java 版本 '{removed_alias}'", Colors.GREEN)
                else:
                    colored_print("无效序号", Colors.RED)
            except ValueError:
                colored_print("请输入数字", Colors.RED)
            input("按 Enter 键继续...")
        elif choice == "3":
            if len(versions) <= 1:
                colored_print("至少需要两个 Java 版本才能切换", Colors.YELLOW)
                input("按 Enter 键继续...")
                continue
            try:
                idx = int(input("输入要使用的序号: ").strip()) - 1
                if 0 <= idx < len(versions):
                    config["current_java_alias"] = versions[idx]["alias"]
                    save_config(config)
                    colored_print(f"已切换至 '{versions[idx]['alias']}'", Colors.GREEN)
                else:
                    colored_print("无效序号", Colors.RED)
            except ValueError:
                colored_print("请输入数字", Colors.RED)
            input("按 Enter 键继续...")
        elif choice == "0":
            break
        else:
            colored_print("无效选项", Colors.RED)
            input("按 Enter 键继续...")

def account_management(config):
    """账号管理子菜单"""
    while True:
        clear_screen()
        colored_print("------ 账号管理 ------", Colors.CYAN, bold=True)
        accounts = config.get("accounts", [])
        current_idx = config.get("current_account_index", -1)
        if accounts:
            colored_print("已保存的账号:", Colors.YELLOW)
            for i, acc in enumerate(accounts):
                marker = " (当前)" if i == current_idx else ""
                colored_print(f"  {i+1}. {acc['alias']}{marker} ({acc['username']}, {acc['userType']})", Colors.WHITE)
        else:
            colored_print("暂无账号", Colors.YELLOW)
        print("\n1. 添加离线账号")
        print("2. 添加 Microsoft 账号(有问题，用不了)")
        print("3. 切换账号")
        print("4. 删除账号")
        print("0. 返回主菜单")
        choice = input("请选择操作: ").strip()

        if choice == "1":
            name = input("输入离线用户名: ").strip()
            if name:
                acc = login_offline(name)
                # 使用别名：用户名_offline
                accounts.append(acc)
                if current_idx == -1:
                    config["current_account_index"] = len(accounts) - 1
                config["accounts"] = accounts
                save_config(config)
                colored_print(f"离线账号 {acc['alias']} 已添加", Colors.GREEN)
            input("按 Enter 键继续...")
        elif choice == "2":
            colored_print("开始 Microsoft 登录...", Colors.CYAN)
            acc = login_microsoft()
            if acc:
                accounts.append(acc)
                if current_idx == -1:
                    config["current_account_index"] = len(accounts) - 1
                config["accounts"] = accounts
                save_config(config)
                colored_print(f"Microsoft 账号 {acc['alias']} 已添加", Colors.GREEN)
            input("按 Enter 键继续...")
        elif choice == "3":
            if len(accounts) == 0:
                colored_print("没有账号可切换", Colors.YELLOW)
                input("按 Enter 键继续...")
                continue
            try:
                idx = int(input("输入账号序号: ").strip()) - 1
                if 0 <= idx < len(accounts):
                    config["current_account_index"] = idx
                    save_config(config)
                    colored_print(f"已切换到账号 '{accounts[idx]['alias']}'", Colors.GREEN)
                else:
                    colored_print("无效序号", Colors.RED)
            except ValueError:
                colored_print("请输入数字", Colors.RED)
            input("按 Enter 键继续...")
        elif choice == "4":
            if len(accounts) == 0:
                colored_print("没有账号可删除", Colors.YELLOW)
                input("按 Enter 键继续...")
                continue
            try:
                idx = int(input("输入要删除的账号序号: ").strip()) - 1
                if 0 <= idx < len(accounts):
                    removed = accounts.pop(idx)
                    if current_idx == idx:
                        config["current_account_index"] = -1 if not accounts else 0
                    elif current_idx > idx:
                        config["current_account_index"] -= 1
                    config["accounts"] = accounts
                    save_config(config)
                    colored_print(f"已删除账号 '{removed['alias']}'", Colors.GREEN)
                else:
                    colored_print("无效序号", Colors.RED)
            except ValueError:
                colored_print("请输入数字", Colors.RED)
            input("按 Enter 键继续...")
        elif choice == "0":
            break
        else:
            colored_print("无效选项", Colors.RED)
            input("按 Enter 键继续...")

def version_management(config):
    """已安装版本管理（选择/删除）"""
    while True:
        clear_screen()
        colored_print("------ 已安装版本管理 ------", Colors.CYAN, bold=True)
        versions = get_installed_versions()
        if not versions:
            colored_print("暂无已安装的版本", Colors.YELLOW)
            input("按 Enter 键返回主菜单...")
            return config.get("current_version")  # 不变
        colored_print("已安装的版本:", Colors.YELLOW)
        for i, v in enumerate(versions, 1):
            mark = " (当前)" if v == config.get("current_version") else ""
            print(f"  {i}. {v}{mark}")
        print("\n请选择需要操作的版本序号 (或输入 0 返回): ")
        sel = input().strip()
        if sel == "0":
            break
        try:
            idx = int(sel) - 1
            if 0 <= idx < len(versions):
                target = versions[idx]
                print(f"\n对版本 {target} 的操作:")
                print("1. 切换到此版本")
                print("2. 删除此版本")
                op = input("请选择操作: ").strip()
                if op == "1":
                    config["current_version"] = target
                    save_config(config)
                    colored_print(f"已切换到版本 {target}", Colors.GREEN)
                elif op == "2":
                    confirm = input(f"确认删除版本 {target}？(y/n): ").strip().lower()
                    if confirm == 'y':
                        delete_version(target)
                        if config["current_version"] == target:
                            config["current_version"] = get_installed_versions()[0] if get_installed_versions() else None
                        save_config(config)
                    else:
                        colored_print("取消删除", Colors.YELLOW)
                else:
                    colored_print("无效操作", Colors.RED)
            else:
                colored_print("无效序号", Colors.RED)
        except ValueError:
            colored_print("请输入数字", Colors.RED)
        input("按 Enter 键继续...")
    return config.get("current_version")

def main():
    colored_print("欢迎使用Litelauncher", Colors.GREEN, bold=True)
    config = load_config()
    load_lang(config.get("language", "zh"))
    
    while True:
        clear_screen()
        # 获取当前 JAVA、当前账号、当前版本
        java_versions = config.get("java_versions", [])
        current_java_alias = config.get("current_java_alias")
        current_java_path = None
        if current_java_alias:
            for jv in java_versions:
                if jv["alias"] == current_java_alias:
                    current_java_path = jv["path"]
                    break
        # 显示 Java 状态
        if current_java_alias and current_java_path:
            java_display = f"{current_java_path} ({current_java_alias})"
        elif not java_versions:
            java_display = "未设置"
        else:
            java_display = "未知别名"

        accounts = config.get("accounts", [])
        current_account_index = config.get("current_account_index", -1)
        if 0 <= current_account_index < len(accounts):
            current_account = accounts[current_account_index]
            account_display = f"{current_account['username']} ({current_account['userType']})"
        else:
            account_display = "未登录"

        current_version = config.get("current_version", "未选择")

        colored_print("="*40, Colors.CYAN)
        colored_print("         Litelauncher", Colors.YELLOW, bold=True)
        colored_print("="*40, Colors.CYAN)
        colored_print(f"Java 路径: {java_display}", Colors.GREEN if current_java_path else Colors.RED)
        colored_print(f"当前账号: {account_display}", Colors.GREEN if account_display != "未登录" else Colors.RED)
        colored_print(f"当前版本: {current_version}", Colors.GREEN if current_version != "未选择" else Colors.RED)
        colored_print("-"*40, Colors.CYAN)
        print("1. 设置 Java 路径")
        print("2. Java 版本管理")
        print("3. 账号管理")
        print("4. 下载游戏版本")
        print("5. 已安装版本管理")
        print("6. 启动游戏")
        print("0. 退出")
        colored_print("-"*40, Colors.CYAN)

        choice = input("请选择操作: ").strip()

        if choice == "1":
            # 单次设置 Java 路径（保留兼容，实际已并入版本管理）
            java_management(config)
        elif choice == "2":
            java_management(config)
        elif choice == "3":
            account_management(config)
        elif choice == "4":
            versions = get_version_list()
            if not versions:
                colored_print("无法获取版本列表，请检查网络", Colors.RED)
                input("按 Enter 键继续...")
                continue
            releases = [v for v in versions if v["type"] == "release"]
            snapshots = [v for v in versions if v["type"] == "snapshot"]
            colored_print("\n=== 可用版本 ===", Colors.CYAN)
            colored_print("最近的正式版:", Colors.YELLOW)
            for i, v in enumerate(releases[:10], 1):
                print(f"  {i}. {v['id']}")
            colored_print("\n最近的快照版:", Colors.YELLOW)
            start_idx = len(releases[:10]) + 1
            for i, v in enumerate(snapshots[:5], start_idx):
                print(f"  {i}. {v['id']}")
            print("\n也可以直接输入完整版本号（如: 1.21.5）")
            sel = input("请选择版本（序号或版本号）: ").strip()
            version_id = None
            if sel.isdigit():
                idx = int(sel) - 1
                all_versions = releases[:10] + snapshots[:5]
                if 0 <= idx < len(all_versions):
                    version_id = all_versions[idx]["id"]
                else:
                    colored_print("无效的序号", Colors.RED)
                    input("按 Enter 键继续...")
                    continue
            else:
                version_id = sel
            if version_id:
                if download_version(version_id):
                    config["current_version"] = version_id
                    save_config(config)
                else:
                    colored_print("下载失败", Colors.RED)
            input("按 Enter 键继续...")
        elif choice == "5":
            version_management(config)
        elif choice == "6":
            # 启动游戏前获取当前 Java 和账号
            if not current_java_path:
                colored_print("错误：未设置 Java 路径，请先添加 Java 版本", Colors.RED)
                input("按 Enter 键继续...")
                continue
            if not Path(current_java_path).exists():
                colored_print(f"错误：Java 路径不存在 - {current_java_path}", Colors.RED)
                input("按 Enter 键继续...")
                continue
            if current_account_index < 0 or current_account_index >= len(accounts):
                colored_print("错误：未登录账号，请先在账号管理中登录", Colors.RED)
                input("按 Enter 键继续...")
                continue
            if not config.get("current_version"):
                colored_print("错误：未选择版本，请先选择或下载", Colors.RED)
                input("按 Enter 键继续...")
                continue
            launch_game(current_java_path, config["current_version"], accounts[current_account_index])
            input("按 Enter 键继续...")
        elif choice == "0":
            colored_print("\n感谢使用，再见！", Colors.CYAN, bold=True)
            sys.exit(0)
        else:
            colored_print("无效的选择，请输入0-6", Colors.RED)
            input("按 Enter 键继续...")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        colored_print("\n\n程序被用户中断，退出中...", Colors.YELLOW)
    except Exception as e:
        colored_print(f"\n[致命错误] 程序异常退出: {e}", Colors.RED)
        sys.exit(1)