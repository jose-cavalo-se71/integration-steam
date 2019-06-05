import platform

if platform.system().lower() == "windows":
    import logging
    import ctypes
    from ctypes.wintypes import LONG, HKEY, LPCWSTR, DWORD, BOOL, HANDLE, LPVOID

    LPSECURITY_ATTRIBUTES = LPVOID

    RegOpenKeyEx = ctypes.windll.advapi32.RegOpenKeyExW
    RegOpenKeyEx.restype = LONG
    RegOpenKeyEx.argtypes = [HKEY, LPCWSTR, DWORD, DWORD, ctypes.POINTER(HKEY)]

    RegCloseKey = ctypes.windll.advapi32.RegCloseKey
    RegCloseKey.restype = LONG
    RegCloseKey.argtypes = [HKEY]

    RegNotifyChangeKeyValue = ctypes.windll.advapi32.RegNotifyChangeKeyValue
    RegNotifyChangeKeyValue.restype = LONG
    RegNotifyChangeKeyValue.argtypes = [HKEY, BOOL, DWORD, HANDLE, BOOL]

    CloseHandle = ctypes.windll.kernel32.CloseHandle
    CloseHandle.restype = BOOL
    CloseHandle.argtypes = [HANDLE]

    CreateEvent = ctypes.windll.kernel32.CreateEventW
    CreateEvent.restype = BOOL
    CreateEvent.argtypes = [LPSECURITY_ATTRIBUTES, BOOL, BOOL, LPCWSTR]

    WaitForSingleObject = ctypes.windll.kernel32.WaitForSingleObject
    WaitForSingleObject.restype = DWORD
    WaitForSingleObject.argtypes = [HANDLE, DWORD]

    ERROR_SUCCESS = 0x00000000
    HKEY_CURRENT_USER = 0x80000001

    KEY_READ = 0x00020019
    KEY_QUERY_VALUE = 0x00000001

    REG_NOTIFY_CHANGE_NAME = 0x00000001
    REG_NOTIFY_CHANGE_LAST_SET = 0x00000004

    WAIT_OBJECT_0 = 0x00000000
    WAIT_TIMEOUT = 0x00000102

    class WinRegistryMonitor:

        def __init__(self, root, subkey):
            self._root = root
            self._subkey = subkey
            self._event = CreateEvent(None, False, False, None)

            self._key = None
            self._open_key()
            if self._key:
                self._set_key_update_notification()

        def close(self):
            CloseHandle(self._event)
            if self._key:
                RegCloseKey(self._key)

        def check_if_updated(self):
            changed = False
            wait_result = WaitForSingleObject(self._event, 0)

            # previously watched
            if wait_result == WAIT_OBJECT_0:
                self._set_key_update_notification()
                changed = True
            # no changes or no key before
            elif wait_result == WAIT_TIMEOUT:
                if self._key is None:
                    self._open_key()
                    changed = self._key is not None
                if self._key is not None:
                    self._set_key_update_notification()
            else:
                # unexpected error
                logging.warning("Unexpected WaitForSingleObject result %s", wait_result)

            return changed

        def _set_key_update_notification(self):
            filter = REG_NOTIFY_CHANGE_NAME | REG_NOTIFY_CHANGE_LAST_SET
            status = RegNotifyChangeKeyValue(self._key, True, filter, self._event, True)
            if status != ERROR_SUCCESS:
                # key was deleted
                RegCloseKey(self._key)
                self._key = None

        def _open_key(self):
            access = KEY_QUERY_VALUE | KEY_READ
            key = HKEY()
            rc = RegOpenKeyEx(self._root, self._subkey, 0, access, ctypes.byref(key))
            if rc == ERROR_SUCCESS:
                self._key = key
            else:
                self._key = None

    def get_steam_registry_monitor():
        return WinRegistryMonitor(HKEY_CURRENT_USER, r"Software\Valve\Steam\Apps")

else:

    import os

    class FileRegistryMonitor:

        def __init__(self, filename):
            self._filename = filename
            self._stat = self._get_stat()

        def _get_stat(self):
            try:
                st = os.stat(self._filename)
            except OSError:
                return (0, 0)
            return (st.st_size, st.st_mtime_ns)

        def check_if_updated(self):
            current_stat = self._get_stat()
            changed = self._stat != current_stat
            self._stat = current_stat
            return changed

        def close(self):
            pass

    def get_steam_registry_monitor():
        return FileRegistryMonitor(os.path.expanduser("~/Library/Application Support/Steam/registry.vdf"))
