from dataclasses import dataclass


BASE_URL = "https://taxinformation.cbic.gov.in/"
GST_TAX_ID = 1000001


@dataclass(frozen=True)
class GSTSection:
    name: str
    tab_label: str
    pane_id: str
    category_id: str
    year_id: str
    endpoint: str
    api_path: str


SECTIONS = (
    GSTSection(
        name="Notification",
        tab_label="Notifications",
        pane_id="notifications",
        category_id="#inputGroupSelectCategoryForContentPage",
        year_id="#inputGroupSelectNotificationYearForContentPage",
        endpoint="cbic-notification-msts",
        api_path="api/cbic-notification-msts",
    ),
    GSTSection(
        name="Circular",
        tab_label="Circulars",
        pane_id="circulars",
        category_id="#inputGroupSelectCategory",
        year_id="#inputGroupSelectCircularYearForContentPage",
        endpoint="cbic-circular-msts",
        api_path="api/cbic-circular-msts",
    ),
)
