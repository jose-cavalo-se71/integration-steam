import platform

if platform.system().lower() == "windows":

    import winreg
    import shlex
    import os

    def is_uri_handler_installed(protocol):
        try:
            key = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"{}\shell\open\command".format(protocol))
            executable_template = winreg.QueryValue(key, None)
            splitted_exec = shlex.split(executable_template)
            if not splitted_exec:
                return False
            return os.path.exists(splitted_exec[0])
        except OSError:
            return False
        except ValueError:
            return False
        finally:
            winreg.CloseKey(key)
        return True

elif platform.system().lower() == "darwin":

    from CoreServices.LaunchServices import LSCopyDefaultHandlerForURLScheme
    from AppKit import NSWorkspace

    def is_uri_handler_installed(protocol):
        bundle_id = LSCopyDefaultHandlerForURLScheme(protocol)
        if not bundle_id:
            return False
        return NSWorkspace.sharedWorkspace().absolutePathForAppBundleWithIdentifier_(bundle_id) is not None

else:

    def is_uri_handler_installed(protocol):
        return False
