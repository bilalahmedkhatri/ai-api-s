"""Allowlisted official Tiptap TableKit JSON; rendering/editing remains Tiptap-owned."""

TABLE_ATTRIBUTES = {
    "table": set(),
    "tableRow": set(),
    "tableCell": {"colspan", "rowspan", "colwidth", "align"},
    "tableHeader": {"colspan", "rowspan", "colwidth", "align"},
}
TABLE_NODES = set(TABLE_ATTRIBUTES)


def validate_table_node(kind, attrs, parent, children):
    if kind == "tableRow" and parent != "table":
        raise ValueError("Table rows belong inside tables.")
    if kind in {"tableCell", "tableHeader"}:
        if parent != "tableRow":
            raise ValueError("Table cells belong inside rows.")
        for key in ("colspan", "rowspan"):
            value = attrs.get(key, 1)
            if type(value) is not int or not 1 <= value <= 1000:
                raise ValueError("Invalid table span.")
        widths = attrs.get("colwidth")
        if widths is not None and (
            not isinstance(widths, list)
            or len(widths) != attrs.get("colspan", 1)
            or any(type(width) is not int or not 0 <= width <= 100000 for width in widths)
        ):
            raise ValueError("Invalid table column widths.")
        if attrs.get("align") is not None and attrs["align"] not in ("left", "center", "right"):
            raise ValueError("Invalid cell alignment.")
    if kind == "table" and (
        not children
        or any(not isinstance(c, dict) or c.get("type") != "tableRow" for c in children)
    ):
        raise ValueError("Tables contain rows only.")
    if kind == "tableRow" and any(
        not isinstance(c, dict) or c.get("type") not in {"tableCell", "tableHeader"}
        for c in children
    ):
        raise ValueError("Rows contain cells only.")
    if kind in {"tableCell", "tableHeader"} and (
        not children
        or any(
            not isinstance(c, dict)
            or c.get("type")
            in {"text", "hardBreak", "listItem", "tableRow", "tableCell", "tableHeader"}
            for c in children
        )
    ):
        raise ValueError("Table cells contain blocks only.")
