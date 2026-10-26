import copy

import pytest
from app.blog.content import validate_document


def table_document():
    cell = {
        "type": "tableHeader",
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Header"}]}],
    }
    return {
        "type": "doc",
        "content": [{"type": "table", "content": [{"type": "tableRow", "content": [cell]}]}],
    }


def test_official_table_json_preserves_text_and_default_attributes():
    document = table_document()
    cell = document["content"][0]["content"][0]["content"][0]
    cell["attrs"] = {"colspan": 1, "rowspan": 1, "colwidth": [120], "align": None}
    assert validate_document(document) == document
    assert validate_document(document) is not document


@pytest.mark.parametrize(
    "attrs",
    [
        {"colspan": 0},
        {"rowspan": True},
        {"colwidth": [-1]},
        {"colspan": 2, "colwidth": [20]},
        {"align": "unsafe"},
        {"onclick": "evil"},
    ],
)
def test_invalid_table_attributes_rejected(attrs):
    document = table_document()
    document["content"][0]["content"][0]["content"][0]["attrs"] = attrs
    with pytest.raises(ValueError):
        validate_document(document)


def test_orphan_cells_and_non_row_children_rejected():
    document = table_document()
    cell = copy.deepcopy(document["content"][0]["content"][0]["content"][0])
    for invalid in [
        {"type": "doc", "content": [cell]},
        {"type": "doc", "content": [{"type": "table", "content": [cell]}]},
    ]:
        with pytest.raises(ValueError):
            validate_document(invalid)


def test_table_link_defaults_and_titles_are_safe():
    document = table_document()
    text = document["content"][0]["content"][0]["content"][0]["content"][0]["content"][0]
    text["marks"] = [{"type": "link", "attrs": {"href": "https://example.test", "title": None}}]
    validate_document(document)
    text["marks"][0]["attrs"]["title"] = "Helpful title"
    assert (
        validate_document(document)["content"][0]["content"][0]["content"][0]["content"][0][
            "content"
        ][0]["marks"][0]["attrs"]["title"]
        == "Helpful title"
    )
    text["marks"][0]["attrs"]["title"] = {"unsafe": True}
    with pytest.raises(ValueError):
        validate_document(document)
