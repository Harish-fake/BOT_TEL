import os
import shutil
from pathlib import Path
from typing import Optional


class FileServiceError(Exception):
    pass


class FileService:

    @staticmethod
    def list_dir(path: str, max_items: int = 30) -> dict:
        if not os.path.isdir(path):
            raise FileServiceError(f"Not a directory: {path}")
        items = sorted(os.listdir(path))
        dirs = []
        files = []
        for name in items:
            full = os.path.join(path, name)
            if os.path.isdir(full):
                dirs.append({"name": name, "type": "dir"})
            else:
                size = os.path.getsize(full)
                files.append({"name": name, "type": "file", "size": size})
        total = len(dirs) + len(files)
        truncated = total > max_items
        return {
            "current_path": path,
            "dirs": dirs[:max_items],
            "files": files[:max_items],
            "total": total,
            "truncated": truncated,
        }

    @staticmethod
    def read_file(path: str, max_chars: int = 4000) -> str:
        if not os.path.isfile(path):
            raise FileServiceError(f"Not a file: {path}")
        with open(path, "r", errors="replace") as f:
            content = f.read(max_chars + 1)
        if len(content) > max_chars:
            content = content[:max_chars] + "\n\n... (truncated)"
        return content

    @staticmethod
    def delete(path: str) -> None:
        if not os.path.exists(path):
            raise FileServiceError(f"Path does not exist: {path}")
        if os.path.isfile(path):
            os.remove(path)
        else:
            shutil.rmtree(path, ignore_errors=True)

    @staticmethod
    def rename(path: str, new_name: str) -> str:
        parent = os.path.dirname(path)
        new_path = os.path.join(parent, new_name)
        if os.path.exists(new_path):
            raise FileServiceError(f"Target already exists: {new_name}")
        os.rename(path, new_path)
        return new_path

    @staticmethod
    def create_file(path: str, content: str = "") -> str:
        if os.path.exists(path):
            raise FileServiceError(f"File already exists: {path}")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return path

    @staticmethod
    def create_folder(path: str) -> str:
        if os.path.exists(path):
            raise FileServiceError(f"Path already exists: {path}")
        os.makedirs(path, exist_ok=True)
        return path

    @staticmethod
    def get_parent(path: str) -> str:
        parent = os.path.dirname(path)
        if not parent or parent == path:
            return os.path.abspath(os.sep)
        return parent

    @staticmethod
    def get_basename(path: str) -> str:
        return os.path.basename(path)

    @staticmethod
    def cleanup(path: str) -> None:
        if os.path.isfile(path):
            os.remove(path)
        elif os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
