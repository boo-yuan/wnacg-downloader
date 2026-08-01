import os
import shutil
from pathlib import Path

def get_data_dir() -> Path:
    if os.name == 'nt':
        base_dir = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
        data_dir = Path(base_dir) / "wnacg-downloader"
    else:
        data_dir = Path(os.path.expanduser("~")) / ".wnacg-downloader"
    
    data_dir.mkdir(parents=True, exist_ok=True)
    
    # 自动迁移旧版文件
    old_data = Path("data")
    if old_data.exists() and old_data.is_dir():
        try:
            for item in old_data.iterdir():
                dest = data_dir / item.name
                if not dest.exists():
                    if item.is_file():
                        shutil.copy2(item, dest)
                    elif item.is_dir():
                        shutil.copytree(item, dest)
            try:
                shutil.rmtree("data.bak", ignore_errors=True)
            except Exception:
                pass
            old_data.rename("data.bak")
        except Exception:
            pass
            
    old_log = Path("app.log")
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
