from dataclasses import dataclass


@dataclass(frozen=True)
class LabourListing:
    category: str
    url: str


LISTINGS = (
    LabourListing("Orders and Notices", "https://labour.gov.in/documents/orders-and-notices"),
    LabourListing("Gazettes Notifications", "https://labour.gov.in/documents/gazettes-notifications"),
)
