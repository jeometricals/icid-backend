from pydantic import BaseModel


class ExportLink(BaseModel):
    download_url: str
    filename: str
