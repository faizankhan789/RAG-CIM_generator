"""Flatten nested file tree JSON → categorised FileItem lists."""

from __future__ import annotations

from typing import Any

from core.mime_types import file_type_from_url
from models import FileItem, FileType


def flatten_tree(
    node: Any,
    label_prefix: str = "",
    _depth: int = 0,
) -> list[FileItem]:
    """
    Recursively flatten a nested file tree into a flat list of FileItems.

    Handles the CRM format:
        {"type": "file", "name": "...", "url": "...", "mime_type": "application/pdf"}
        {"type": "folder", "name": "...", "children": [...]}

    Also handles generic formats:
        {"url": "...", "label": "..."}
        {"children": [...]}
        {"folderName": [...]}
        "https://..."
    """
    items: list[FileItem] = []

    if isinstance(node, str):
        ft = file_type_from_url(node)
        items.append(FileItem(url=node, label=label_prefix or node, file_type=ft))

    elif isinstance(node, list):
        for child in node:
            items.extend(flatten_tree(child, label_prefix, _depth + 1))

    elif isinstance(node, dict):
        node_type = node.get("type", "")

        if node_type == "file" or "url" in node:
            url = node.get("url", "")
            if not url:
                return items
            name = node.get("name") or node.get("label") or label_prefix or url
            mime_type = node.get("mime_type", "")
            ft = file_type_from_url(url, content_type=mime_type)
            if ft == FileType.UNKNOWN and name:
                ft = file_type_from_url(name)
            folder_path = f"{label_prefix}/{name}" if label_prefix else name
            content = node.get("content", "")
            items.append(FileItem(url=url, label=folder_path, file_type=ft, content=content))

        elif node_type == "folder" or any(k in node for k in ("children", "files", "items")):
            folder_name = node.get("name", label_prefix)
            folder_label = f"{label_prefix}/{folder_name}" if label_prefix and folder_name != label_prefix else folder_name
            children_key = next((k for k in ("children", "files", "items") if k in node), None)
            if children_key:
                items.extend(flatten_tree(node[children_key], folder_label, _depth + 1))

        else:
            # Generic label→node dict
            for key, value in node.items():
                child_label = f"{label_prefix}/{key}" if label_prefix else key
                items.extend(flatten_tree(value, child_label, _depth + 1))

    return items


def categorise(items: list[FileItem]) -> dict[FileType, list[FileItem]]:
    """Group FileItems by FileType."""
    result: dict[FileType, list[FileItem]] = {ft: [] for ft in FileType}
    for item in items:
        result[item.file_type].append(item)
    return result
