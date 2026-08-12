"""Reading the item-selection menu.

Fixtures are verbatim from crawl 0.34.1's `use_item` menu, including the
unidentified naming that "unknown" depends on.
"""

from __future__ import annotations

import pytest

from dcssbot.items import (
    ItemMenuError,
    find_item,
    parse_item_menu,
    pick_unknown,
)

POTIONS = [
    {"text": "Potions", "q": None, "hotkeys": None, "level": 1},
    {"text": " L - a potion of lignification", "q": 1, "hotkeys": [76], "level": 2},
    {"text": " d - 4 bubbling green potions", "q": 4, "hotkeys": [100], "level": 2},
    {"text": " f - 2 white potions", "q": 2, "hotkeys": [102], "level": 2},
]

SCROLLS = [
    {"text": "Scrolls", "q": None, "hotkeys": None, "level": 1},
    {"text": " c - 4 scrolls labelled LOUNOCVILOA", "q": 4, "hotkeys": [99], "level": 2},
    {"text": " e - 3 scrolls labelled XYDIOF MEIRA", "q": 3, "hotkeys": [101], "level": 2},
    {"text": " g - 3 scrolls of fog", "q": 3, "hotkeys": [103], "level": 2},
]


def test_category_headings_are_not_items() -> None:
    # "Potions" has no hotkey, so it is a label rather than something to press.
    assert [r.key for r in parse_item_menu(POTIONS)] == ["L", "d", "f"]


def test_counts_and_articles_are_stripped_from_the_name() -> None:
    rows = {r.key: r for r in parse_item_menu(POTIONS)}
    assert rows["L"].name == "potion of lignification"
    assert rows["d"].name == "bubbling green potions"


def test_quantity_comes_from_the_menu_field() -> None:
    rows = {r.key: r for r in parse_item_menu(POTIONS)}
    assert rows["d"].quantity == 4
    assert rows["L"].quantity == 1


def test_identification_follows_crawls_naming() -> None:
    # item-name.cc writes "potion of X" / "scroll of X" only once identified;
    # unidentified ones are described by appearance or a made-up label.
    rows = {r.key: r for r in parse_item_menu(POTIONS)}
    assert rows["L"].identified is True
    assert rows["d"].identified is False
    scrolls = {r.key: r for r in parse_item_menu(SCROLLS)}
    assert scrolls["c"].identified is False   # "labelled LOUNOCVILOA"
    assert scrolls["g"].identified is True    # "scrolls of fog"


def test_an_empty_menu_yields_nothing() -> None:
    assert parse_item_menu([]) == []
    assert parse_item_menu([{"text": "Potions", "level": 1}]) == []


# -- choosing an unknown --------------------------------------------------


def test_unknown_prefers_the_biggest_stack() -> None:
    # Spending a duplicate is the cheapest way to identify by use.
    assert pick_unknown(parse_item_menu(POTIONS)).key == "d"   # 4, not 2


def test_unknown_never_picks_an_identified_item() -> None:
    assert pick_unknown(parse_item_menu(SCROLLS)).identified is False


def test_a_tie_on_count_breaks_alphabetically() -> None:
    tied = [
        {"text": " a - 2 white potions", "q": 2, "hotkeys": [97], "level": 2},
        {"text": " b - 2 fizzy red potions", "q": 2, "hotkeys": [98], "level": 2},
    ]
    # "fizzy" sorts before "white", so the choice does not depend on menu order.
    assert pick_unknown(parse_item_menu(tied)).name == "fizzy red potions"


def test_nothing_unidentified_is_refused() -> None:
    known = [{"text": " a - 2 potions of curing", "q": 2, "hotkeys": [97], "level": 2}]
    with pytest.raises(ItemMenuError, match="nothing unidentified"):
        pick_unknown(parse_item_menu(known))


# -- matching by name -----------------------------------------------------


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("lignification", "potion of lignification"),
        ("potion of lignification", "potion of lignification"),
        ("white", "white potions"),
        ("bubbling", "bubbling green potions"),
        ("bubbling green", "bubbling green potions"),
    ],
)
def test_items_match_by_name_or_appearance(query: str, expected: str) -> None:
    assert find_item(parse_item_menu(POTIONS), query).name == expected


def test_an_ambiguous_fragment_is_refused() -> None:
    # Drinking the wrong potion is not a recoverable mistake.
    with pytest.raises(ItemMenuError) as exc:
        find_item(parse_item_menu(POTIONS), "potion")
    assert "white potions" in str(exc.value)


def test_an_item_not_carried_is_refused() -> None:
    with pytest.raises(ItemMenuError, match="nothing matching"):
        find_item(parse_item_menu(POTIONS), "curing")


def test_a_labelled_scroll_can_be_named_by_its_label() -> None:
    assert find_item(parse_item_menu(SCROLLS), "xydiof").key == "e"
