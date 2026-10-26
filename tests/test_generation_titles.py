# ruff: noqa: F811
"""Optional titles: mocked generation, real validation/storage/auth in isolated SQL."""

import json

import pytest
from app.models.db_models import FinalResponse
from app.services.generation_output import OUTPUT_INSTRUCTIONS, split_generation_output, valid_title
from sqlalchemy import select

from tests.test_generation import result, setup_generation  # noqa: F401

ROLE = "Frontend Developer"
COMPANY = "Azeemlab"
SOURCES = {"role": ROLE, "company": COMPANY}
PROMPT = (
    "Write a cover letter in English. Role: Frontend Developer. Company: Azeemlab. Applicant: Alex."
)
LETTER = "Dear Hiring Team,\n\nI would like to apply.\n\nThank you."


@pytest.mark.parametrize(
    "language,role,title,letter",
    [
        ("English", ROLE, "Frontend Developer — Azeemlab", LETTER),
        (
            "Russian",
            "Фронтенд разработчик",
            "Фронтенд разработчик — Azeemlab",
            "Уважаемая команда,\nСпасибо.",
        ),
        (
            "Spanish",
            "Desarrollador frontend",
            "Desarrollador frontend — Azeemlab",
            "Estimado equipo,\nGracias.",
        ),
        (
            "French",
            "Développeur frontend",
            "Développeur frontend — Azeemlab",
            "Chère équipe,\nMerci.",
        ),
        (
            "Portuguese",
            "Desenvolvedor frontend",
            "Desenvolvedor frontend — Azeemlab",
            "Prezada equipe,\nObrigado.",
        ),
    ],
)
async def test_single_call_title_is_separate_localized_and_persisted(
    setup_generation, language, role, title, letter
):
    client, auth, call, db, _ = setup_generation
    prompt = (
        f"Write a cover letter in {language}. Role: {role}. Company: {COMPANY}. Applicant: Alex."
    )
    payload = {
        "answer": letter,
        "title": title,
        "title_sources": {"role": role, "company": COMPANY},
    }
    call.return_value = result(json.dumps(payload, ensure_ascii=False))
    response = await client.post("/api/v1/generate/", headers=auth, json={"prompt": prompt})
    assert response.status_code == 200, response.text
    assert response.json()["answer"] == letter
    assert response.json()["title"] == title
    assert set(response.json()) == {"answer", "title", "request_id", "finish_reason"}
    assert call.await_count == 1
    assert call.await_args.kwargs["messages"][-1] == {"role": "user", "content": prompt}
    assert "requested output language" in OUTPUT_INSTRUCTIONS
    final = db.session.scalars(select(FinalResponse)).one()
    assert final.generated_output == letter and final.meta["title"] == title
    assert (
        "title_sources" not in final.meta_fields and "title_sources" not in final.generated_output
    )


@pytest.mark.parametrize(
    "title,sources",
    [
        (None, SOURCES),
        (123, SOURCES),
        (["Frontend"], SOURCES),
        ("Developer Azeemlab", SOURCES),
        ("Application for the Frontend Developer at the Azeemlab", SOURCES),
        ("Frontend Developer — ImaginaryCorp", SOURCES),
        ("Senior Frontend Developer — Azeemlab", SOURCES),
        ("Frontend Developer — Alex", SOURCES),
        ("Frontend Developer — Azeemlab alex@example.test", SOURCES),
        ("Frontend Developer — Azeemlab +923001234567", SOURCES),
        ("Frontend Developer\nAzeemlab", SOURCES),
        ("Frontend Developer\x00Azeemlab", SOURCES),
        ("Frontend Developer\ud800Azeemlab", SOURCES),
        ("<Frontend Developer Azeemlab>", SOURCES),
        ("Frontend Developer — Azeemlab", None),
        ("Frontend Developer — Azeemlab", {"role": "Invented Role", "company": COMPANY}),
        ("Application for Alex", {"role": "Alex", "company": None}),
        ("Frontend Developer — " + "A" * 70, SOURCES),
    ],
)
async def test_invalid_optional_title_never_fails_or_changes_valid_letter(
    setup_generation, title, sources
):
    client, auth, call, db, _ = setup_generation
    call.return_value = result(
        json.dumps({"answer": LETTER, "title": title, "title_sources": sources})
    )
    response = await client.post("/api/v1/generate/", headers=auth, json={"prompt": PROMPT})
    assert response.status_code == 200
    assert response.json()["answer"] == LETTER and response.json()["title"] is None
    assert call.await_count == 1
    final = db.session.scalars(select(FinalResponse)).one()
    assert final.generated_output == LETTER and final.meta["title"] is None


@pytest.mark.parametrize(
    "content",
    [
        LETTER,
        json.dumps({"answer": LETTER}),
        json.dumps({"answer": LETTER, "title": None}),
        '{"answer":' + json.dumps(LETTER) + ',"title": malformed optional metadata}',
        '{"title": malformed optional metadata,"answer":' + json.dumps(LETTER) + "}",
        "```json\n" + json.dumps({"answer": LETTER, "title": None}) + "\n```",
    ],
)
async def test_plain_letter_missing_title_and_malformed_metadata_are_safe(
    setup_generation, content
):
    client, auth, call, _, _ = setup_generation
    call.return_value = result(content)
    response = await client.post("/api/v1/generate/", headers=auth, json={"prompt": PROMPT})
    assert response.status_code == 200, response.text
    assert response.json()["answer"] == LETTER and response.json()["title"] is None


async def test_review_and_repair_can_return_letter_without_title(setup_generation):
    client, auth, call, _, _ = setup_generation
    prompt = "Review and repair this letter without inventing facts: " + LETTER
    call.return_value = result(json.dumps({"answer": LETTER, "title": None, "title_sources": None}))
    response = await client.post("/api/v1/generate/", headers=auth, json={"prompt": prompt})
    assert response.status_code == 200 and response.json()["title"] is None
    assert response.json()["answer"] == LETTER and call.await_count == 1


def test_eighty_unicode_code_points_and_word_bounds():
    role = "é" * 20 + " " + "я" * 20
    company = "ñ" * 36
    title = role + " — " + company
    assert len(title) == 80 and len(title.encode("utf-8")) > 80
    assert (
        valid_title(title, {"role": role, "company": company}, f"Role: {role}. Company: {company}.")
        == title
    )
    assert (
        valid_title(title + "ñ", {"role": role, "company": company + "ñ"}, f"{role} {company}ñ")
        is None
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"title": "Frontend Developer — Azeemlab"},
        {"answer": "", "title": None},
        {"answer": {}, "title": None},
    ],
)
async def test_metadata_cannot_turn_missing_letter_into_success(setup_generation, payload):
    client, auth, call, _, _ = setup_generation
    call.return_value = result(json.dumps(payload))
    response = await client.post("/api/v1/generate/", headers=auth, json={"prompt": PROMPT})
    assert response.status_code == 502
    assert "answer" not in response.json()


def test_word_provenance_rejects_applicant_and_invented_facts():
    assert valid_title("Application for Alex", {"role": None, "company": "Alex"}, PROMPT) is None
    assert (
        valid_title("Frontend Developer — Google", {"role": ROLE, "company": "Google"}, PROMPT)
        is None
    )
    assert split_generation_output(
        json.dumps({"answer": LETTER, "title": {"bad": "metadata"}}), PROMPT
    ) == (LETTER, None)


async def test_only_title_metadata_truncated_keeps_complete_letter(setup_generation):
    client, auth, call, db, _ = setup_generation
    content = '{"answer":' + json.dumps(LETTER) + ',"title":"Frontend'
    call.return_value = result(content, "length")
    response = await client.post("/api/v1/generate/", headers=auth, json={"prompt": PROMPT})
    assert response.status_code == 200, response.text
    assert response.json()["answer"] == LETTER and response.json()["title"] is None
    assert response.json()["finish_reason"] == "unknown"
    final = db.session.scalars(select(FinalResponse)).one()
    assert final.generated_output == LETTER and final.meta["finish_reason"] == "length"


async def test_incomplete_answer_string_still_fails_as_truncated(setup_generation):
    client, auth, call, _, _ = setup_generation
    call.return_value = result('{"answer":"Dear hiring team', "length")
    response = await client.post("/api/v1/generate/", headers=auth, json={"prompt": PROMPT})
    assert response.status_code == 502 and response.json()["detail"]["code"] == "output_truncated"


def test_title_sources_cannot_hide_an_applicant_label_or_control_character():
    assert (
        valid_title("Application for Alex", {"role": "Applicant: Alex", "company": None}, PROMPT)
        is None
    )
    assert valid_title("Frontend Developer\u2028Azeemlab", SOURCES, PROMPT) is None
    assert (
        valid_title(
            "3D Artist at Azeemlab",
            {"role": "3D Artist", "company": COMPANY},
            "Role: 3D Artist. Company: Azeemlab.",
        )
        == "3D Artist at Azeemlab"
    )
