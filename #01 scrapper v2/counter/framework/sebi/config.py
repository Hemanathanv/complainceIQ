from dataclasses import dataclass


@dataclass(frozen=True)
class SEBIListing:
    category: str
    ssid: int
    path_name: str

    @property
    def url(self) -> str:
        return (
            "https://www.sebi.gov.in/sebiweb/home/HomeAction.do"
            f"?doListing=yes&sid=1&ssid={self.ssid}&smid=0"
        )


HOME_URL = "https://www.sebi.gov.in/"
LISTINGS = (
    SEBIListing("Circular", 7, "circulars"),
    SEBIListing("Gazette Notification", 82, "gazette-notification"),
)
