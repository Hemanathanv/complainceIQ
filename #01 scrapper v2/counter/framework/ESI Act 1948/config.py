from dataclasses import dataclass


@dataclass(frozen=True)
class ESICListing:
    category: str
    url: str


CIRCULARS = ESICListing("Circular", "https://esic.gov.in/circulars")
