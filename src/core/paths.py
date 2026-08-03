import os
import shutil
import sys
from pathlib import Path

def get_data_dir() -> Path:
    # 按照系统规范，数据默认保存在系统应用数据目录
    if os.name == 'nt':
        appdata_base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
        data_dir = Path(appdata_base) / "wnacg-downloader"
    else:
        data_dir = Path(os.path.expanduser("~")) / ".wnacg-downloader"
        
    data_dir.mkdir(parents=True, exist_ok=True)
    
    # 尝试恢复刚才可能被备份的 AppData 数据 (被改名为 _bak 的目录)
    bak_appdata = Path(str(data_dir) + "_bak")
    if bak_appdata.exists() and bak_appdata.is_dir():
        try:
            for item in bak_appdata.iterdir():
                dest = data_dir / item.name
                if not dest.exists():
                    if item.is_file():
                        shutil.copy2(item, dest)
                    elif item.is_dir():
                        shutil.copytree(item, dest)
            # 恢复后清除备份
            shutil.rmtree(bak_appdata, ignore_errors=True)
        except Exception:
            pass

    # 如果有在本地目录（便携模式）产生的数据，也无缝迁移回系统目录
    if getattr(sys, 'frozen', False):
        local_base = Path(sys.executable).parent
    else:
        local_base = Path(__file__).parent.parent.parent
        
    local_data = local_base / "data"
    if local_data.exists() and local_data.is_dir():
        try:
            for item in local_data.iterdir():
                dest = data_dir / item.name
                if not dest.exists():
                    if item.is_file():
                        shutil.copy2(item, dest)
                    elif item.is_dir():
                        shutil.copytree(item, dest)
            # 迁移后将本地 data 目录重命名为 .bak，避免冲突
            local_bak = Path(str(local_data) + "_bak")
            if local_bak.exists():
                shutil.rmtree(local_bak, ignore_errors=True)
            local_data.rename(local_bak)
        except Exception:
            pass
            
    # 兼容老版本在根目录的 app.log
    old_log = local_base / "app.log"
    if old_log.exists() and old_log.is_file():
        try:
            dest = data_dir / "app.log"
            if not dest.exists():
                shutil.copy2(old_log, dest)
            old_log.unlink()
        except Exception:
            pass

    return data_dir

DATA_DIR = get_data_dir()
