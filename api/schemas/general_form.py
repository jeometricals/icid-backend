from typing import Optional

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class FormModel(BaseModel):
    """
    Base for General Form models: camelCase keys on the wire and in JSONB.
    Accepts either camelCase or snake_case input; rejects unknown keys.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class PayItem(FormModel):
    item_no: str = ""
    budget_code: str = ""
    pay_quantity: str = ""
    quantity_chk: str = ""
    description: str = ""


class Workforce(FormModel):
    superintendent: str = ""
    foreman: str = ""
    operator: str = ""
    flagger: str = ""


class EquipmentEntry(FormModel):
    model: str = ""
    number: str = ""


class Equipment(FormModel):
    front_end_loader: EquipmentEntry = Field(default_factory=EquipmentEntry)
    backhoe: EquipmentEntry = Field(default_factory=EquipmentEntry)
    truck_dump: EquipmentEntry = Field(default_factory=EquipmentEntry)
    excavator: EquipmentEntry = Field(default_factory=EquipmentEntry)


class SafetyChecks(FormModel):
    plastic_barrels: Optional[bool] = None
    pedestrian_barricades: Optional[bool] = None
    timber_curbs: Optional[bool] = None
    timber_breakaway_barricades: Optional[bool] = None
    general_safety: Optional[bool] = None
    local_emergency_access: Optional[bool] = None
    fencing: Optional[bool] = None
    plates: Optional[bool] = None
    arrow_board: Optional[bool] = None
    site_cleaned: Optional[bool] = None


class GeneralFormData(FormModel):
    date: str = ""
    sheet_no: str = ""
    work_activity_start: str = ""
    work_activity_end: str = ""
    inspector_time_start: str = ""
    inspector_time_end: str = ""
    daily_temp_low: str = ""
    daily_temp_high: str = ""
    weather_am: str = Field(default="", alias="weatherAM")
    weather_pm: str = Field(default="", alias="weatherPM")
    description: str = ""
    pay_items: list[PayItem] = Field(default_factory=list)
    workforce: Workforce = Field(default_factory=Workforce)
    equipment: Equipment = Field(default_factory=Equipment)
    safety_checks: SafetyChecks = Field(default_factory=SafetyChecks)
    safety_remarks: str = ""
    comments: str = ""
