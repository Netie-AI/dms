"""Build Library repository tree (RedisInsight/DbGate-style folders)."""

from __future__ import annotations

from typing import Any


def build_library_tree(
    *,
    sources: list[dict[str, Any]],
    bronze_tables: list[dict[str, Any]],
    warehouse_tables: list[dict[str, Any]],
    space_id: str | None = None,
    space_name: str | None = None,
) -> dict[str, Any]:
    """Return a foldable tree for Library left nav."""
    if space_id:
        sources = [s for s in sources if s.get("space_id") == space_id]

    # Group sources by path-like ref: folder/file or bare file
    source_children: list[dict[str, Any]] = []
    folders: dict[str, list[dict[str, Any]]] = {}
    for s in sources:
        ref = str(s.get("ref") or s.get("id") or "source")
        parts = ref.replace("\\", "/").split("/")
        leaf = {
            "id": f"source:{s.get('id') or ref}",
            "kind": "leaf",
            "node_type": "source",
            "label": parts[-1],
            "meta": {
                "kind": s.get("kind"),
                "scope": s.get("scope"),
                "space_id": s.get("space_id"),
                "space_name": s.get("space_name"),
                "ref": ref,
            },
        }
        if len(parts) > 1:
            folder = "/".join(parts[:-1])
            folders.setdefault(folder, []).append(leaf)
        else:
            source_children.append(leaf)

    for folder, leaves in sorted(folders.items()):
        source_children.append(
            {
                "id": f"folder:src:{folder}",
                "kind": "folder",
                "label": folder,
                "children": leaves,
            }
        )

    bronze_children = [
        {
            "id": f"bronze:{t.get('table')}",
            "kind": "leaf",
            "node_type": "bronze",
            "label": t.get("table"),
            "meta": {
                "row_count": t.get("row_count"),
                "table": t.get("table"),
                "source": t.get("source"),
                "extracted_at": t.get("extracted_at"),
                "truncated": t.get("truncated"),
                "source_kind": t.get("source_kind"),
            },
        }
        for t in bronze_tables
    ]
    wh_children = [
        {
            "id": f"warehouse:{t.get('table')}",
            "kind": "leaf",
            "node_type": "warehouse",
            "label": t.get("table"),
            "meta": {"row_count": t.get("row_count"), "table": t.get("table")},
        }
        for t in warehouse_tables
    ]

    nodes = [
        {
            "id": "folder:sources",
            "kind": "folder",
            "label": "Sources",
            "children": source_children,
        },
        {
            "id": "folder:bronze",
            "kind": "folder",
            "label": "Bronze",
            "children": bronze_children,
        },
        {
            "id": "folder:warehouse",
            "kind": "folder",
            "label": "Warehouse",
            "children": wh_children,
        },
    ]
    return {
        "space_id": space_id,
        "space_name": space_name,
        "nodes": nodes,
    }
