"""Static state names, USPS abbreviations and 2-digit FIPS codes.

Only the 50 states, the District of Columbia and Puerto Rico (52 entries) have
demographic data. The four island territories appear in the county lookup but have
no demographic rows.
"""

from typing import NamedTuple


class StateName(NamedTuple):
    name: str
    abbr: str
    fips: str


STATES: tuple[StateName, ...] = (
    StateName("Alabama", "AL", "01"),
    StateName("Alaska", "AK", "02"),
    StateName("Arizona", "AZ", "04"),
    StateName("Arkansas", "AR", "05"),
    StateName("California", "CA", "06"),
    StateName("Colorado", "CO", "08"),
    StateName("Connecticut", "CT", "09"),
    StateName("Delaware", "DE", "10"),
    StateName("District of Columbia", "DC", "11"),
    StateName("Florida", "FL", "12"),
    StateName("Georgia", "GA", "13"),
    StateName("Hawaii", "HI", "15"),
    StateName("Idaho", "ID", "16"),
    StateName("Illinois", "IL", "17"),
    StateName("Indiana", "IN", "18"),
    StateName("Iowa", "IA", "19"),
    StateName("Kansas", "KS", "20"),
    StateName("Kentucky", "KY", "21"),
    StateName("Louisiana", "LA", "22"),
    StateName("Maine", "ME", "23"),
    StateName("Maryland", "MD", "24"),
    StateName("Massachusetts", "MA", "25"),
    StateName("Michigan", "MI", "26"),
    StateName("Minnesota", "MN", "27"),
    StateName("Mississippi", "MS", "28"),
    StateName("Missouri", "MO", "29"),
    StateName("Montana", "MT", "30"),
    StateName("Nebraska", "NE", "31"),
    StateName("Nevada", "NV", "32"),
    StateName("New Hampshire", "NH", "33"),
    StateName("New Jersey", "NJ", "34"),
    StateName("New Mexico", "NM", "35"),
    StateName("New York", "NY", "36"),
    StateName("North Carolina", "NC", "37"),
    StateName("North Dakota", "ND", "38"),
    StateName("Ohio", "OH", "39"),
    StateName("Oklahoma", "OK", "40"),
    StateName("Oregon", "OR", "41"),
    StateName("Pennsylvania", "PA", "42"),
    StateName("Rhode Island", "RI", "44"),
    StateName("South Carolina", "SC", "45"),
    StateName("South Dakota", "SD", "46"),
    StateName("Tennessee", "TN", "47"),
    StateName("Texas", "TX", "48"),
    StateName("Utah", "UT", "49"),
    StateName("Vermont", "VT", "50"),
    StateName("Virginia", "VA", "51"),
    StateName("Washington", "WA", "53"),
    StateName("West Virginia", "WV", "54"),
    StateName("Wisconsin", "WI", "55"),
    StateName("Wyoming", "WY", "56"),
    StateName("Puerto Rico", "PR", "72"),
)

# In the county lookup, but without demographic data.
TERRITORIES: tuple[StateName, ...] = (
    StateName("American Samoa", "AS", "60"),
    StateName("Guam", "GU", "66"),
    StateName("Northern Mariana Islands", "MP", "69"),
    StateName("U.S. Virgin Islands", "VI", "78"),
)

# Extra wordings for a state or territory, keyed by USPS abbreviation.
STATE_ALIASES: dict[str, tuple[str, ...]] = {
    "DC": ("Washington DC", "Washington D.C.", "D.C.", "Washington District of Columbia"),
    "VI": ("Virgin Islands", "US Virgin Islands", "United States Virgin Islands"),
    "MP": ("Northern Marianas", "Commonwealth of the Northern Mariana Islands"),
}

_BY_ABBR = {s.abbr: s for s in (*STATES, *TERRITORIES)}


def state_by_abbr(abbr: str) -> StateName:
    """Return the state or territory with this USPS abbreviation (KeyError if unknown)."""
    return _BY_ABBR[abbr.upper()]
