from google.cloud import firestore
from doctor_service.analytics.analytics_model import AnalyticsTrendsResponse, TrendItem, RegionalTrend

async def get_public_health_trends(db: firestore.AsyncClient) -> AnalyticsTrendsResponse:
    """Aggregates de-identified analytics records to plot health trend dashboards."""
    events_snap = await db.collection("analytics_events").get()
    
    total_cases = len(events_snap)
    
    # Aggregation structures
    condition_counts = {}
    regional_counts = {}
    
    for doc in events_snap:
        d = doc.to_dict()
        cond = d.get("condition", "Unknown")
        code = d.get("icd11Code", "N/A")
        region = d.get("region", "Unknown Region")
        
        # 1. Condition aggregation
        key = (cond, code)
        condition_counts[key] = condition_counts.get(key, 0) + 1
        
        # 2. Regional aggregation
        if region not in regional_counts:
            regional_counts[region] = {}
        regional_counts[region][key] = regional_counts[region].get(key, 0) + 1
        
    # Format Top Conditions
    top_conditions = []
    for (cond, code), count in sorted(condition_counts.items(), key=lambda x: x[1], reverse=True):
        top_conditions.append(TrendItem(condition=cond, icd11_code=code, count=count))
        
    # Format Regional Breakdown
    regional_breakdown = []
    for region, conds in regional_counts.items():
        trends = []
        for (cond, code), count in sorted(conds.items(), key=lambda x: x[1], reverse=True):
            trends.append(TrendItem(condition=cond, icd11_code=code, count=count))
        regional_breakdown.append(RegionalTrend(region=region, trends=trends))
        
    return AnalyticsTrendsResponse(
        total_cases_logged=total_cases,
        top_conditions=top_conditions,
        regional_breakdown=regional_breakdown
    )
