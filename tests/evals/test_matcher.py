import pytest

from evals.baseline_v1.matcher import SourceIndex, canonical_number, match_element, normalize

SOURCE = (
    "Le président Abdelmadjid Tebboune a reçu jeudi 12 octobre 2026 à Alger les responsables de "
    "Sonatrach. Le budget atteint 1 500 000 dinars, soit une hausse de 12,5 %. "
    "« Nous allons investir dans la jeunesse », a déclaré le ministre."
)


@pytest.fixture(scope="module")
def index() -> SourceIndex:
    return SourceIndex(SOURCE)


def test_normalize_strips_accents_and_unifies_typography() -> None:
    assert normalize("Algérie’s  « Été »") == 'algerie\'s " ete "'


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1 500 000", "1500000"),
        ("1.500.000", "1500000"),
        ("12,5", "12.5"),
        ("1.5", "1.5"),
        ("05", "5"),
    ],
)
def test_canonical_number(raw: str, expected: str) -> None:
    assert canonical_number(raw) == expected


@pytest.mark.parametrize(
    ("element_type", "value", "found"),
    [
        ("number", "1500000 dinars", True),
        ("number", "12,5 %", True),
        ("number", "2 millions de dinars", False),
        ("date", "12 octobre 2026", True),
        ("date", "jeudi 12 octobre", True),
        ("date", "13 octobre 2026", False),
        ("quote", "Nous allons investir dans la jeunesse", True),
        ("quote", "Nous allons investir massivement", False),
        ("person", "Abdelmadjid Tebboune", True),
        ("person", "Abdelmajid Teboune", True),  # transliteration variant, fuzzy
        ("person", "Ahmed Benali", False),
        ("organization", "SONATRACH", True),
        ("place", "Alger", True),
        ("place", "Oran", False),
    ],
)
def test_match_element(element_type: str, value: str, found: bool, index: SourceIndex) -> None:
    assert match_element(element_type, value, index).found is found  # type: ignore[arg-type]


def test_name_windows_do_not_match_inside_longer_words() -> None:
    index = SourceIndex("Le festival se tient en Algérie cette année.")
    assert not match_element("place", "Alger", index).found


def test_fuzzy_score_is_reported_below_100() -> None:
    result = match_element("person", "Abdelmajid Teboune", SourceIndex(SOURCE))
    assert 90 <= result.score < 100
