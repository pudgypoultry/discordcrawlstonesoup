"""Reading the skill menu.

The fixtures below are verbatim captures from crawl 0.34.1 — colour spans,
HTML entities, two-column layout and all — because every awkward detail this
module handles came from looking at the real thing rather than from guessing.
"""

from __future__ import annotations

import pytest

from dcssbot.skills import (
    SkillMenuError,
    find_skill,
    parse_skill_menu,
    strip_markup,
)

#: The default "useful skills" view for a fresh Octopode Shapeshifter.
USEFUL = {
    "0": '      <span class="fg1 bg0">Skill           Level Train  Apt       Skill           Level Train  Apt',
    "1": '  <span class="fg7 bg0">a + Fighting         2.0   </span><span class="fg6 bg0">7%     </span><span class="fg15 bg0">0    </span><span class="fg8 bg0">e + Spellcasting     0.0         </span><span class="fg15 bg0">-1',
    "3": '  <span class="fg7 bg0">b + Unarmed Combat   3.0  </span><span class="fg6 bg0">36%     </span><span class="fg15 bg0">0',
    "4": '                                         <span class="fg7 bg0">f + Shapeshifting    3.0  </span><span class="fg6 bg0">49%    </span><span class="fg15 bg0">-1',
    "6": '  <span class="fg7 bg0">c + Dodging          2.0   </span><span class="fg6 bg0">8%     </span><span class="fg15 bg0">0',
    "7": '  <span class="fg8 bg0">d + Stealth          0.0         </span><span class="fg15 bg0">+4',
    "22": ' <span class="fg7 bg0">[</span><span class="fg14 bg0">?</span><span class="fg7 bg0">] Help                [</span><span class="fg14 bg0">=</span><span class="fg7 bg0">] set a skill target',
}

#: The same character's "all skills" view, verbatim. The keys are different
#: from the useful view and run past `z` into digits.
ALL = {
    "1": "  a + Fighting         2.0   7%     0    n + Spellcasting     0.0         -1",
    "2": "  b + Maces &amp; Flails   0.0          0    o + Conjurations     0.0          0",
    "3": "  c + Axes             0.0          0    p + Hexes            0.0          0",
    "4": "  d + Polearms         0.0          0    q + Summonings       0.0          0",
    "5": "  e + Staves           0.0          0    r + Necromancy       0.0          0",
    "6": "  f + Unarmed Combat   3.0  36%     0    s + Forgecraft       0.0          0",
    "7": "  g + Throwing         0.0          0    t + Translocations   0.0          0",
    "8": "                                         u + Alchemy          0.0         +1",
    "9": "  h + Short Blades     0.0          0    v + Fire Magic       0.0          0",
    "10": "  i + Long Blades      0.0          0    w + Ice Magic        0.0          0",
    "11": "  j + Ranged Weapons   0.0          0    x + Air Magic        0.0          0",
    "12": "                                         y + Earth Magic      0.0          0",
    "13": "  k + Dodging          2.0   8%     0    z + Invocations      0.0         +1",
    "14": "  l + Shields          0.0          0    0 + Evocations       0.0         +1",
    "15": "  m + Stealth          0.0         +4    1 + Shapeshifting    3.0  49%    -1",
}

#: The useful view again after Shift-a, i.e. training Fighting and nothing
#: else. This is where the `-` flag actually appears.
AFTER_TRAINING_ONE = {
    "1": "  a + Fighting         2.0  100%    0    e - Spellcasting     0.0         -1",
    "3": "  b - Unarmed Combat   3.0          0",
    "4": "                                         f - Shapeshifting    3.0         -1",
    "6": "  c - Dodging          2.0          0",
    "7": "  d - Stealth          0.0         +4",
}


def test_strip_markup_removes_spans_and_unescapes() -> None:
    assert strip_markup('<span class="fg7 bg0">Maces &amp; Flails</span>') == "Maces & Flails"


def test_parses_both_columns_of_the_useful_view() -> None:
    rows = {r.name: r for r in parse_skill_menu(USEFUL)}
    assert set(rows) == {
        "Fighting", "Unarmed Combat", "Dodging", "Stealth",
        "Spellcasting", "Shapeshifting",
    }
    assert rows["Fighting"].key == "a"
    assert rows["Spellcasting"].key == "e"   # the right-hand column
    assert rows["Shapeshifting"].key == "f"  # alone on its line, indented


def test_levels_are_read() -> None:
    rows = {r.name: r for r in parse_skill_menu(ALL)}
    assert rows["Unarmed Combat"].level == 3.0
    assert rows["Fighting"].level == 2.0


def test_the_training_flag_distinguishes_plus_from_minus() -> None:
    rows = {r.name: r for r in parse_skill_menu(AFTER_TRAINING_ONE)}
    assert rows["Fighting"].training is True
    assert rows["Dodging"].training is False
    assert rows["Spellcasting"].training is False


def test_entities_in_names_are_decoded() -> None:
    assert "Maces & Flails" in {r.name for r in parse_skill_menu(ALL)}


def test_hotkeys_can_be_digits_once_the_list_is_long() -> None:
    rows = {r.name: r for r in parse_skill_menu(ALL)}
    assert rows["Evocations"].key == "0"
    assert rows["Shapeshifting"].key == "1"


def test_the_same_skill_has_different_keys_in_each_view() -> None:
    # Which is exactly why the keys are read from the menu rather than fixed.
    useful = {r.name: r.key for r in parse_skill_menu(USEFUL)}
    everything = {r.name: r.key for r in parse_skill_menu(ALL)}
    assert useful["Unarmed Combat"] == "b"
    assert everything["Unarmed Combat"] == "f"


def test_headers_and_footers_are_not_mistaken_for_skills() -> None:
    names = {r.name for r in parse_skill_menu(USEFUL)}
    assert not any("Help" in n or "Skill " in n for n in names)


def test_an_empty_menu_yields_nothing() -> None:
    assert parse_skill_menu({}) == []
    assert parse_skill_menu({"0": "", "1": "   "}) == []


# -- targets --------------------------------------------------------------


@pytest.mark.parametrize(
    ("level", "target"),
    [(0.0, 1), (2.0, 3), (3.0, 4), (3.3, 4), (3.9, 4), (12.5, 13)],
)
def test_the_target_is_the_next_whole_level(level: float, target: int) -> None:
    # A target equal to the current level would already be met, so an exact
    # 3.0 still aims at 4.
    from dcssbot.skills import SkillRow

    assert SkillRow("a", "Fighting", level, True).next_target == target


# -- matching -------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("fighting", "Fighting"),
        ("Fighting", "Fighting"),
        ("unarmed", "Unarmed Combat"),
        ("unarmed combat", "Unarmed Combat"),
        ("maces", "Maces & Flails"),
        ("maces & flails", "Maces & Flails"),
        ("fire", "Fire Magic"),
        ("short", "Short Blades"),
        ("evocations", "Evocations"),
    ],
)
def test_skills_match_by_name_prefix_or_fragment(query: str, expected: str) -> None:
    assert find_skill(parse_skill_menu(ALL), query).name == expected


def test_an_ambiguous_fragment_is_refused_with_the_options() -> None:
    # Picking one at random would silently retrain the wrong thing.
    with pytest.raises(SkillMenuError) as exc:
        find_skill(parse_skill_menu(ALL), "magic")
    assert "Fire Magic" in str(exc.value)


def test_an_unknown_skill_is_refused() -> None:
    with pytest.raises(SkillMenuError, match="no skill matching"):
        find_skill(parse_skill_menu(ALL), "basketweaving")


def test_an_exact_name_wins_over_a_longer_one() -> None:
    # "Axes" is a prefix of nothing here, but the principle matters for pairs
    # like Fighting / Fighting-something in future versions.
    assert find_skill(parse_skill_menu(ALL), "axes").name == "Axes"


def test_the_all_view_parses_every_skill_crawl_listed() -> None:
    # 28 in the capture; the fixture keeps all of them.
    assert len(parse_skill_menu(ALL)) == 28
