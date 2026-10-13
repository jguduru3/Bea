# PyInstaller recipe for the standalone Bea server bundled in the desktop installers.
# Build with:  python build_server.py   (it runs this spec, adds Chromium and self-tests the result)
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hiddenimports = (
    collect_submodules("app")            # routers, agent tools and connectors are imported dynamically
    + collect_submodules("uvicorn")
    + ["aiosqlite", "sqlalchemy.dialects.sqlite.aiosqlite", "python_multipart", "multipart"]
)
datas = (
    collect_data_files("playwright")     # the Playwright driver (Node) and its package
    + collect_data_files("jsonschema_specifications")
    + collect_data_files("tzdata")       # Windows has no system time zone database
    + collect_data_files("certifi")
)

a = Analysis(
    ["server_main.py"],
    pathex=["."],
    hiddenimports=hiddenimports,
    datas=datas,
    excludes=["tkinter", "pytest", "respx", "asyncpg", "IPython"],  # asyncpg: the desktop build uses SQLite
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="bea-server", console=True, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="bea-server", upx=False)
