from pydantic import BaseModel, Field
from typing import List

class TrendItem(BaseModel):
    condition: str
    icd11_code: str
    count: int

class RegionalTrend(BaseModel):
    region: str
    trends: List[TrendItem]

class AnalyticsTrendsResponse(BaseModel):
    total_cases_logged: int
    top_conditions: List[TrendItem]
    regional_breakdown: List[RegionalTrend]
