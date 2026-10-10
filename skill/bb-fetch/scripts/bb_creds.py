# -*- coding: utf-8 -*-
"""bb_creds.py — 跨平台凭据存储（优先用系统原生凭据库，尽力零依赖）。

存什么：一条 secret = JSON `{"username": ..., "password": ...}`，
以 `service` 为键；用户名与密码打包成一个 secret，这样各种后端
（有的分账号字段、有的只有一条 value）都能统一处理。

后端优先级（自动探测）：
  1. env        —— 环境变量 BB_USER / BB_PASS（只读，最高优先）
  2. wincred    —— Windows 凭据管理器（ctypes，零依赖）
  3. macos      —— macOS 钥匙串（`security` CLI，系统自带）
  4. secret-tool—— Linux Secret Service（libsecret 的 CLI）
  5. keyring    —— Python keyring（装了就可用，跨平台）
  6. file       —— ~/.bb-fetch/credentials.json（0600，最后兜底，会告警）

CLI：
  python bb_creds.py set <user> <pass> [--service S]
  python bb_creds.py show [--service S]
  python bb_creds.py del  [--service S]
  python bb_creds.py where            # 打印各后端可用性
"""
import json
import os
import shutil
import stat
import subprocess
import sys

DEFAULT_SERVICE = "bb-fetch"
ACCOUNT = "bb-fetch"          # 后端里作为「账号字段」的占位名
HOME_DIR = os.path.join(os.path.expanduser("~"), ".bb-fetch")
FILE_STORE = os.path.join(HOME_DIR, "credentials.json")


# ─────────────────────────────── 后端 ───────────────────────────────
class _Backend:
    name = "?"

    @classmethod
    def available(cls):
        return False

    @classmethod
    def get(cls, service):
        return None

    @classmethod
    def set(cls, service, secret):
        raise NotImplementedError

    @classmethod
    def delete(cls, service):
        return False


class EnvBackend(_Backend):
    name = "env"

    @classmethod
    def available(cls):
        return bool(os.environ.get("BB_USER") and os.environ.get("BB_PASS"))

    @classmethod
    def get(cls, service):
        u = os.environ.get("BB_USER")
        p = os.environ.get("BB_PASS")
        if u and p:
            return json.dumps({"username": u, "password": p})
        return None


class WinCredBackend(_Backend):
    name = "wincred"

    @classmethod
    def available(cls):
        return sys.platform == "win32"

    @classmethod
    def _api(cls):
        import ctypes
        import ctypes.wintypes as wt
        adv = ctypes.WinDLL("advapi32", use_last_error=True)

        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", wt.DWORD), ("dwHighDateTime", wt.DWORD)]

        class CREDENTIAL(ctypes.Structure):
            _fields_ = [
                ("Flags", wt.DWORD), ("Type", wt.DWORD), ("TargetName", wt.LPWSTR),
                ("Comment", wt.LPWSTR), ("LastWritten", FILETIME),
                ("CredentialBlobSize", wt.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
                ("Persist", wt.DWORD), ("AttributeCount", wt.DWORD),
                ("Attributes", ctypes.c_void_p), ("TargetAlias", wt.LPWSTR),
                ("UserName", wt.LPWSTR),
            ]
        return adv, wt, CREDENTIAL

    @classmethod
    def get(cls, service):
        adv, wt, CREDENTIAL = cls._api()
        p = __import__("ctypes").POINTER(CREDENTIAL)()
        if not adv.CredReadW(service, 1, 0, __import__("ctypes").byref(p)):
            return None
        try:
            c = p.contents
            n = c.CredentialBlobSize
            import ctypes as _c
            raw = _c.string_at(c.CredentialBlob, n) if n else b""
            return raw.decode("utf-16-le", "replace")
        finally:
            adv.CredFree(p)

    @classmethod
    def set(cls, service, secret):
        import ctypes
        adv, wt, CREDENTIAL = cls._api()
        blob = secret.encode("utf-16-le")
        buf = (ctypes.c_byte * len(blob)).from_buffer_copy(blob)
        cred = CREDENTIAL()
        cred.Type = 1
        cred.TargetName = service
        cred.Comment = "bb-fetch"
        cred.CredentialBlobSize = len(blob)
        cred.CredentialBlob = ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))
        cred.Persist = 2  # LOCAL_MACHINE
        cred.UserName = ACCOUNT
        if not adv.CredWriteW(ctypes.byref(cred), 0):
            raise OSError("CredWriteW failed, WinError=%d" % ctypes.get_last_error())
        return True

    @classmethod
    def delete(cls, service):
        import ctypes
        adv, _, _ = cls._api()
        if not adv.CredDeleteW(service, 1, 0):
            err = ctypes.get_last_error()
            if err == 1168:  # NOT_FOUND
                return False
            raise OSError("CredDeleteW failed, WinError=%d" % err)
        return True


class MacKeychainBackend(_Backend):
    name = "macos"

    @classmethod
    def available(cls):
        return sys.platform == "darwin" and shutil.which("security") is not None

    @classmethod
    def get(cls, service):
        r = subprocess.run(["security", "find-generic-password", "-s", service,
                            "-a", ACCOUNT, "-w"], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None

    @classmethod
    def set(cls, service, secret):
        subprocess.run(["security", "delete-generic-password", "-s", service, "-a", ACCOUNT],
                       capture_output=True)
        r = subprocess.run(["security", "add-generic-password", "-U", "-s", service,
                            "-a", ACCOUNT, "-w", secret], capture_output=True, text=True)
        if r.returncode != 0:
            raise OSError("security add-generic-password failed: %s" % r.stderr.strip()[:120])
        return True

    @classmethod
    def delete(cls, service):
        r = subprocess.run(["security", "delete-generic-password", "-s", service, "-a", ACCOUNT],
                           capture_output=True)
        return r.returncode == 0


class SecretToolBackend(_Backend):
    name = "secret-tool"

    @classmethod
    def available(cls):
        return sys.platform.startswith("linux") and shutil.which("secret-tool") is not None

    @classmethod
    def get(cls, service):
        r = subprocess.run(["secret-tool", "lookup", "service", service, "account", ACCOUNT],
                           capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None

    @classmethod
    def set(cls, service, secret):
        r = subprocess.run(["secret-tool", "store", "--label=bb-fetch",
                            "service", service, "account", ACCOUNT],
                           input=secret, capture_output=True, text=True)
        if r.returncode != 0:
            raise OSError("secret-tool store failed: %s" % r.stderr.strip()[:120])
        return True

    @classmethod
    def delete(cls, service):
        r = subprocess.run(["secret-tool", "clear", "service", service, "account", ACCOUNT],
                           capture_output=True)
        return r.returncode == 0


class KeyringBackend(_Backend):
    name = "keyring"

    @classmethod
    def _kr(cls):
        import keyring
        return keyring

    @classmethod
    def available(cls):
        try:
            cls._kr()
            return True
        except Exception:
            return False

    @classmethod
    def get(cls, service):
        try:
            return cls._kr().get_password(service, ACCOUNT)
        except Exception:
            return None

    @classmethod
    def set(cls, service, secret):
        cls._kr().set_password(service, ACCOUNT, secret)
        return True

    @classmethod
    def delete(cls, service):
        try:
            cls._kr().delete_password(service, ACCOUNT)
            return True
        except Exception:
            return False


class FileBackend(_Backend):
    name = "file"

    @classmethod
    def available(cls):
        return True

    @classmethod
    def _read(cls):
        if not os.path.exists(FILE_STORE):
            return {}
        try:
            with open(FILE_STORE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    @classmethod
    def get(cls, service):
        return cls._read().get(service)

    @classmethod
    def set(cls, service, secret):
        os.makedirs(HOME_DIR, exist_ok=True)
        d = cls._read()
        d[service] = secret
        with open(FILE_STORE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        try:
            os.chmod(FILE_STORE, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        except OSError:
            pass
        return True

    @classmethod
    def delete(cls, service):
        d = cls._read()
        if service in d:
            del d[service]
            with open(FILE_STORE, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=1)
            return True
        return False


READ_ORDER = [EnvBackend, WinCredBackend, MacKeychainBackend, SecretToolBackend, KeyringBackend, FileBackend]
WRITE_ORDER = [WinCredBackend, MacKeychainBackend, SecretToolBackend, KeyringBackend, FileBackend]


# ─────────────────────────────── API ───────────────────────────────
def load(service=DEFAULT_SERVICE):
    """返回 (username, password)；找不到返回 (None, None)。"""
    for be in READ_ORDER:
        try:
            if not be.available():
                continue
            raw = be.get(service)
            if not raw:
                continue
            try:
                d = json.loads(raw)
                if d.get("username") and d.get("password"):
                    return d["username"], d["password"]
            except (ValueError, TypeError):
                continue
        except Exception:
            continue
    return None, None


def store(username, password, service=DEFAULT_SERVICE):
    """写入凭据；返回实际使用的后端名。"""
    secret = json.dumps({"username": username, "password": password}, ensure_ascii=False)
    last = None
    for be in WRITE_ORDER:
        try:
            if not be.available():
                continue
            be.set(service, secret)
            return be.name
        except Exception as e:  # noqa: BLE001
            last = "%s: %s" % (be.name, str(e)[:80])
    raise RuntimeError("所有凭据后端均写入失败（最后一个错误：%s）" % last)


def delete(service=DEFAULT_SERVICE):
    ok = False
    for be in WRITE_ORDER:
        try:
            if be.available() and be.delete(service):
                ok = True
        except Exception:
            pass
    return ok


def which_backend(service=DEFAULT_SERVICE):
    """当前实际会用于读取的后端名。"""
    for be in READ_ORDER:
        try:
            if be.available() and be.get(service):
                return be.name
        except Exception:
            continue
    return None


def _cli():
    args = sys.argv[1:]
    service = DEFAULT_SERVICE
    if "--service" in args:
        i = args.index("--service")
        service = args[i + 1]
        del args[i:i + 2]
    if not args:
        print(__doc__)
        return 2
    cmd = args[0]
    if cmd == "where":
        for be in READ_ORDER:
            print("  %-12s %s" % (be.name, "可用" if be.available() else "不可用"))
        print("当前读取后端:", which_backend(service) or "(无)")
        return 0
    if cmd == "set":
        if len(args) < 3:
            print("用法: set <user> <pass> [--service S]")
            return 2
        used = store(args[1], args[2], service)
        print("已写入（后端=%s）service=%s" % (used, service))
        return 0
    if cmd == "show":
        u, p = load(service)
        if u is None:
            print("(无凭据) service=%s" % service)
            return 1
        print("service=%s\nuser=%s\npass=%s (%d 字符)\n后端=%s"
              % (service, u, "★" * len(p), len(p), which_backend(service)))
        return 0
    if cmd == "del":
        print("已删除" if delete(service) else "(本就无)")
        return 0
    print("未知命令:", cmd)
    return 2


if __name__ == "__main__":
    sys.exit(_cli())
