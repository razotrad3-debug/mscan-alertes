# -*- mode: python ; coding: utf-8 -*-
# Build : pyinstaller MSCAN.spec --noconfirm
# Produit une APPLICATION FENETREE (sans console), epinglable a la barre des taches.

a = Analysis(
    ['app.py'],
    pathex=['.'],
    binaries=[],
    datas=[('mscan.ico', '.')],
    hiddenimports=[
        'config',
        'mmscanner', 'mmscanner.model', 'mmscanner.indicators',
        'mmscanner.sources_gecko', 'mmscanner.sources_dex', 'mmscanner.sources_helius',
        'mmscanner.helius_tx', 'mmscanner.holder_flow', 'mmscanner.wallet_store',
        'mmscanner.discover_wallets', 'mmscanner.followed', 'mmscanner.checker',
        'mmscanner.phases', 'mmscanner.scoring', 'mmscanner.engine',
        'mmscanner.telegram', 'mmscanner.demo', 'mmscanner.whaleflow',
        'mmscanner.fomoscan', 'mmscanner.telegram_alerts', 'mmscanner.telegram_bot', 'mmscanner.insider_watch', 'mmscanner.safety', 'mmscanner.sources_evm', 'mmscanner.clans', 'mmscanner.fomoscan_web', 'mmscanner.holdings', 'mmscanner.expansion',
        'web', 'web.server',
        'dotenv', 'flask', 'jinja2', 'markupsafe', 'werkzeug', 'requests',
        'webview', 'webview.platforms.edgechromium', 'clr_loader', 'pythonnet',
    ],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'pandas', 'scipy'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='MSCAN',
    icon='mscan.ico',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=True,
    console=False,                 # application fenetree : pas de console noire
    disable_windowed_traceback=False,
    argv_emulation=False, target_arch=None, codesign_identity=None,
    entitlements_file=None,
)
