"""PyInstaller entry point that sets up environment before importing main."""
import os
import sys
import platform

# Set up environment before any imports
if getattr(sys, 'frozen', False):
    # Running in PyInstaller bundle
    bundle_dir = sys._MEIPASS

    if platform.system() == 'Linux':
        # Detect Linux distribution
        distro = 'debian_ubuntu'  # Default
        try:
            with open('/etc/os-release', 'r') as f:
                os_release = f.read().lower()
                if 'ubuntu' in os_release or 'debian' in os_release:
                    distro = 'debian_ubuntu'
                elif 'rhel' in os_release or 'centos' in os_release or 'fedora' in os_release or 'arch' in os_release:
                    distro = 'rhel'
                elif 'suse' in os_release:
                    distro = 'suse'
                elif 'alpine' in os_release:
                    distro = 'alpine'
        except:
            pass

        # Set library path
        arch = platform.machine()
        lib_base = os.path.join(bundle_dir, 'mssql_python', 'libs', 'linux', distro, arch)
        lib_path = os.path.join(lib_base, 'lib')
        share_path = os.path.join(lib_base, 'share')

        if os.path.exists(lib_path):
            # Add to LD_LIBRARY_PATH
            ld_library_path = os.environ.get('LD_LIBRARY_PATH', '')
            if ld_library_path:
                os.environ['LD_LIBRARY_PATH'] = f"{lib_path}:{ld_library_path}"
            else:
                os.environ['LD_LIBRARY_PATH'] = lib_path

            # Set ODBC-specific environment variables
            import tempfile
            odbc_ini_dir = tempfile.mkdtemp(prefix='odbc_')
            os.environ['ODBCSYSINI'] = odbc_ini_dir

            # Set resource path if it exists
            if os.path.exists(share_path):
                os.environ['MSSQL_DRIVER_RESOURCES'] = share_path

            # Preload the ODBC driver libraries
            try:
                import ctypes

                odbcinst_lib = os.path.join(lib_path, 'libodbcinst.so.2')
                odbc_lib = os.path.join(lib_path, 'libmsodbcsql-18.5.so.1.1')

                RTLD_GLOBAL = ctypes.RTLD_GLOBAL if hasattr(ctypes, 'RTLD_GLOBAL') else 0x100
                RTLD_NOW = 0x002

                if os.path.exists(odbcinst_lib):
                    ctypes.CDLL(odbcinst_lib, mode=RTLD_GLOBAL)

                if os.path.exists(odbc_lib):
                    ctypes.CDLL(odbc_lib, mode=RTLD_GLOBAL | RTLD_NOW)
            except Exception:
                pass  # Silently fail - the app will show error if connection fails

    elif platform.system() == 'Windows':
        # For Windows, add the libs path to the DLL search path
        lib_path = os.path.join(bundle_dir, 'mssql_python', 'libs', 'windows')
        if os.path.exists(lib_path):
            os.add_dll_directory(lib_path)

# Now import and run the actual main
if __name__ == '__main__':
    import main
    main.main()
