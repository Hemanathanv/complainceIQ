from dataclasses import dataclass


@dataclass(frozen=True)
class IncomeTaxListing:
    category: str
    url: str


LISTINGS = (
    IncomeTaxListing("Notification", "https://www.incometaxindia.gov.in/notifications"),
    IncomeTaxListing("Circular", "https://www.incometaxindia.gov.in/circulars"),
)
