# -*- coding: utf-8 -*-
"""临时脚本：GCM 挂死时经 Windows 凭据管理器直读 token 完成 git push。"""
import base64
import ctypes
import ctypes.wintypes as wintypes
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)), ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR),
    ]


adv = ctypes.WinDLL("advapi32")
adv.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                          ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
adv.CredReadW.restype = wintypes.BOOL

cp = ctypes.POINTER(CREDENTIAL)()
if not adv.CredReadW("git:https://github.com", 1, 0, ctypes.byref(cp)):
    print("[x] CredReadW 失败 %d" % ctypes.GetLastError())
    sys.exit(1)
c = cp.contents
size = c.CredentialBlobSize
blob = bytes(ctypes.cast(c.CredentialBlob,
                         ctypes.POINTER(ctypes.c_byte * size)).contents)
user = c.UserName
ctypes.windll.advapi32.CredFree(cp)

if size % 2 == 0 and all(b == 0 for b in blob[1::2]):
    tok = blob.decode("utf-16-le").rstrip("\x00")
else:
    tok = blob.decode("utf-8").rstrip("\x00")
tok = tok.strip()
if ":" in tok:
    user, tok = tok.rsplit(":", 1)

b64 = base64.b64encode(("%s:%s" % (user, tok)).encode()).decode()
env = dict(os.environ,
           GIT_CONFIG_COUNT="1",
           GIT_CONFIG_KEY_0="http.extraheader",
           GIT_CONFIG_VALUE_0="Authorization: Basic " + b64,
           NO_PROXY="github.com")
r = subprocess.run(["git", "-c", "http.sslBackend=schannel",
                    "-c", "credential.helper=", "push", "origin", "main"],
                   cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
print("push rc=%d" % r.returncode)
print(((r.stdout or "") + (r.stderr or ""))[-300:])
sys.exit(r.returncode)
