from dataclasses import dataclass


@dataclass(frozen=True)
class EPFOListing:
    category: str
    url: str


CIRCULARS = EPFOListing("Circular", "https://www.epfo.gov.in/circulars/")
