import pytest

from talking.commands import parse_command


@pytest.mark.parametrize("text,lang", [
    ("переведи на немецкий", "de"),
    ("переведи на испанский", "es"),
    ("Переводи на испанском", "es"),
    ("translate to Spanish", "es"),
    ("traduce al inglés", "en"),
    ("übersetze auf Deutsch", "de"),
    ("Przetłumacz na hiszpański", "es"),
    ("переклади на англійську", "en"),
])
def test_commands(text, lang):
    assert parse_command(text) == lang


@pytest.mark.parametrize("text", [
    "я люблю испанский язык",
    "I like Spanish food",
    "мы говорили о переводе",
    "",
    "hello there",
])
def test_non_commands(text):
    assert parse_command(text) is None
