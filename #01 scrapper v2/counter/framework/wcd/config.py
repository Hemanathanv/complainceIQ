from dataclasses import dataclass


@dataclass(frozen=True)
class WCDListing:
    category: str
    url: str


LISTINGS = (
    WCDListing("Orders and Notices", "https://wcd.gov.in/documents/orders-and-notices"),
    WCDListing("Notifications", "https://wcd.gov.in/offerings/whatsnew"),
)
