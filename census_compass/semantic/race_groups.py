"""Race and ethnicity population groups (spec section 6.4)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RaceGroup:
    code: str
    slug: str
    label: str
    synonyms: tuple[str, ...]


RACE_GROUPS: tuple[RaceGroup, ...] = (
    RaceGroup("A", "white_alone", "White alone", ("White",)),
    RaceGroup("B", "black_alone", "Black or African American alone",
              ("Black", "African American")),
    RaceGroup("C", "american_indian_alaska_native_alone",
              "American Indian and Alaska Native alone", ("Native American",)),
    RaceGroup("D", "asian_alone", "Asian alone", ("Asian",)),
    RaceGroup("E", "pacific_islander_alone",
              "Native Hawaiian and Other Pacific Islander alone",
              ("Pacific Islander", "Native Hawaiian")),
    RaceGroup("F", "some_other_race_alone", "Some other race alone", ()),
    RaceGroup("G", "two_or_more_races", "Two or more races",
              ("multiracial", "mixed race")),
    RaceGroup("H", "white_alone_not_hispanic", "White alone, not Hispanic or Latino",
              ("non-Hispanic White",)),
    RaceGroup("I", "hispanic_or_latino", "Hispanic or Latino",
              ("Hispanic", "Latino", "Latinx")),
)

_BY_CODE = {g.code: g for g in RACE_GROUPS}


def race_group(code: str) -> RaceGroup:
    return _BY_CODE[code]
