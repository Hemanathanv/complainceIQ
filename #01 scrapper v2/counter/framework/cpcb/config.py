from dataclasses import dataclass


@dataclass(frozen=True)
class CPCBListing:
    category: str
    url: str


CIRCULARS = CPCBListing("Current Circulars / Office Orders / Memoranda",
                        "https://cpcb.gov.in/circular/")
