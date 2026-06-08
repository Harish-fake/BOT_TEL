import os
import zipfile
import shutil
import uuid
from pathlib import Path
from typing import Optional
from config import config


class ZipValidationError(Exception):
    pass


class ZipService:

    @staticmethod
    def validate(zip_path: str) -> None:
        max_bytes = config.MAX_FILE_SIZE_MB * 1024 * 1024
        file_size = os.path.getsize(zip_path)
        if file_size > max_bytes:
            raise ZipValidationError(
                f"File too large ({file_size / 1024 / 1024:.1f} MB). "
                f"Maximum allowed: {config.MAX_FILE_SIZE_MB} MB."
            )
        if not zipfile.is_zipfile(zip_path):
            raise ZipValidationError("File is not a valid ZIP archive.")

        with zipfile.ZipFile(zip_path, "r") as zf:
            entries = len(zf.infolist())
            if entries > config.MAX_ZIP_ENTRIES:
                raise ZipValidationError(
                    f"ZIP contains {entries} entries, exceeding the limit "
                    f"of {config.MAX_ZIP_ENTRIES}."
                )
            for info in zf.infolist():
                if info.file_size > max_bytes:
                    raise ZipValidationError(
                        f"Entry '{info.filename}' exceeds size limit."
                    )

    @staticmethod
    def _flatten(dest_dir: str) -> None:
        entries = sorted(os.listdir(dest_dir))
        if len(entries) != 1:
            return
        sole = os.path.join(dest_dir, entries[0])
        if not os.path.isdir(sole):
            return
        for child in os.listdir(sole):
            shutil.move(os.path.join(sole, child), os.path.join(dest_dir, child))
        os.rmdir(sole)

    @staticmethod
    def extract(zip_path: str, dest_dir: str) -> str:
        os.makedirs(dest_dir, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            for info in zf.infolist():
                resolved = Path(dest_dir) / info.filename
                resolved = resolved.resolve()
                if not str(resolved).startswith(os.path.abspath(dest_dir)):
                    raise ZipValidationError(
                        f"Path traversal detected in entry: {info.filename}"
                    )
            zf.extractall(dest_dir)
        ZipService._flatten(dest_dir)
        return dest_dir

    @staticmethod
    def save_temp_file(file_bytes: bytes, filename: str) -> str:
        temp_dir = os.path.join("storage", "temp")
        os.makedirs(temp_dir, exist_ok=True)
        safe_name = f"{uuid.uuid4().hex}_{filename}"
        dest = os.path.join(temp_dir, safe_name)
        with open(dest, "wb") as f:
            f.write(file_bytes)
        return dest

    @staticmethod
    def cleanup(path: str) -> None:
        if os.path.isfile(path):
            os.remove(path)
        elif os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
