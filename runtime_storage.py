"""Keep this tool's private runtime files beside its source, outside Codex data."""
from datetime import datetime
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent / 'runtime'


def create_backup_folder(kind: str = 'switch') -> Path:
    """Create a private, unique backup directory under the tool repository."""
    backups = ROOT / 'backups'
    backups.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(ROOT, 0o700)
    os.chmod(backups, 0o700)
    folder = backups / (
        datetime.now().strftime('%Y%m%d-%H%M%S-') + kind + '-' + os.urandom(3).hex()
    )
    folder.mkdir(mode=0o700)
    return folder
