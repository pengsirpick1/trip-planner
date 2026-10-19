"""数据模型定义"""

from typing import List, Optional, Union
from pydantic import BaseModel, Field, field_validator
from datetime import date


# ============ 请求模型 ============

class TripRequest(BaseModel):
    """旅行规划请求"""
    city: str = Field(..., description="目的地城市", example="北京")
    start_date: str = Field(..., description="开始日期 YYYY-MM-DD", example="2025-06-01")
    end_date: str = Field(..., description="结束日期 YYYY-MM-DD", example="2025-06-03")
    travel_days: int = Field(..., description="旅行天数", ge=1, le=30, example=3)
    transportation: str = Field(..., description="交通方式", example="公共交通")
    accommodation: str = Field(..., description="住宿偏好", example="经济型酒店")
    preferences: List[str] = Field(default=[], description="旅行偏好标签", example=["历史文化", "美食"])
    free_text_input: Optional[str] = Field(default="", description="额外要求", example="希望多安排一些博物馆")
    
    class Config:
        json_schema_extra = {
            "example": {
                "city": "北京",
                "start_date": "2025-06-01",
                "end_date": "2025-06-03",
                "travel_days": 3,
                "transportation": "公共交通",
                "accommodation": "经济型酒店",
                "preferences": ["历史文化", "美食"],
                "free_text_input": "希望多安排一些博物馆"
            }
        }


class POISearchRequest(BaseModel):
    """POI搜索请求"""
    keywords: str = Field(..., description="搜索关键词", example="故宫")
    city: str = Field(..., description="城市", example="北京")
    citylimit: bool = Field(default=True, description="是否限制在城市范围内")


class RouteRequest(BaseModel):
    """路线规划请求"""
    origin_address: str = Field(..., description="起点地址", example="北京市朝阳区阜通东大街6号")
    destination_address: str = Field(..., description="终点地址", example="北京市海淀区上地十街10号")
    origin_city: Optional[str] = Field(default=None, description="起点城市")
    destination_city: Optional[str] = Field(default=None, description="终点城市")
    route_type: str = Field(default="walking", description="路线类型: walking/driving/transit")


# ============ 响应模型 ============

class Location(BaseModel):
    """地理位置"""
    longitude: float = Field(..., description="经度")
    latitude: float = Field(..., description="纬度")


class Attraction(BaseModel):
    """景点信息"""
    name: str = Field(..., description="景点名称")
    address: str = Field(..., description="地址")
    location: Optional[Location] = Field(default=None, description="经纬度坐标（无坐标时为空，不画 marker）")
    visit_duration: Optional[int] = Field(default=None, description="建议游览时间(分钟)，未知为空")
    description: str = Field(..., description="景点描述")
    category: Optional[str] = Field(default="景点", description="景点类别")
    rating: Optional[float] = Field(default=None, description="评分")
    photos: Optional[List[str]] = Field(default_factory=list, description="景点图片URL列表")
    poi_id: Optional[str] = Field(default="", description="POI ID")
    image_url: Optional[str] = Field(default=None, description="图片URL")
    ticket_price: Optional[int] = Field(default=None, description="门票价格(元)；未知为 null，不归零")
    source: Optional[str] = Field(default=None, description="事实来源标注（provider/查询）")
    evidence_ids: Optional[List[str]] = Field(default=None, description="溯源证据 ID 列表")
    unknown_fields: Optional[List[str]] = Field(default=None, description="未知/未验证字段名")
    xhs_url: Optional[str] = Field(default=None, description="小红书攻略搜索链接（web 直达，非站内抓取）")


class Meal(BaseModel):
    """餐饮信息"""
    type: str = Field(..., description="餐饮类型: breakfast/lunch/dinner/snack")
    name: str = Field(..., description="餐饮名称")
    address: Optional[str] = Field(default=None, description="地址")
    location: Optional[Location] = Field(default=None, description="经纬度坐标")
    description: Optional[str] = Field(default=None, description="描述")
    estimated_cost: Optional[int] = Field(default=None, description="预估费用(元)；未知为空")


class Hotel(BaseModel):
    """酒店信息"""
    name: str = Field(..., description="酒店名称")
    address: str = Field(default="", description="酒店地址")
    location: Optional[Location] = Field(default=None, description="酒店位置")
    price_range: str = Field(default="", description="价格范围")
    rating: str = Field(default="", description="评分")
    distance: str = Field(default="", description="距离景点距离")
    type: str = Field(default="", description="酒店类型")
    estimated_cost: Optional[int] = Field(default=None, description="预估费用(元/晚)；未知为空")


class HotelCandidate(BaseModel):
    """住宿研究候选。只展示地图/路线研究证据，不冒充 OTA 实时报价。"""
    name: str
    address: str = ""
    rating: Optional[str] = None
    distance_summary: str = ""
    commute_summary: str = ""
    selection_reason: str = ""
    rejection_reason: Optional[str] = None
    evidence_ids: List[str] = Field(default_factory=list)
    source_references: List[str] = Field(default_factory=list)
    retrieved_at: Optional[str] = None
    verification_links: dict[str, str] = Field(default_factory=dict)


class DayPlan(BaseModel):
    """单日行程"""
    date: str = Field(..., description="日期 YYYY-MM-DD")
    day_index: int = Field(..., description="第几天(从0开始)")
    description: str = Field(..., description="当日行程描述")
    transportation: str = Field(..., description="交通方式")
    accommodation: str = Field(..., description="住宿")
    hotel: Optional[Hotel] = Field(default=None, description="推荐酒店")
    attractions: List[Attraction] = Field(default=[], description="景点列表")
    meals: List[Meal] = Field(default=[], description="餐饮列表")


class WeatherInfo(BaseModel):
    """天气信息"""
    date: str = Field(..., description="日期 YYYY-MM-DD")
    day_weather: str = Field(default="", description="白天天气")
    night_weather: str = Field(default="", description="夜间天气")
    day_temp: Union[int, str, None] = Field(default=None, description="白天温度（未知为空）")
    night_temp: Union[int, str, None] = Field(default=None, description="夜间温度（未知为空）")
    wind_direction: str = Field(default="", description="风向")
    wind_power: str = Field(default="", description="风力")
    kind: Optional[str] = Field(default=None, description="数据语义：current/forecast")
    source: Optional[str] = Field(default=None, description="来源标注")
    unknown_fields: Optional[List[str]] = Field(default=None, description="未知字段名")

    @field_validator('day_temp', 'night_temp', mode='before')
    @classmethod
    def parse_temperature(cls, v):
        """解析温度，移除°C等单位；无法解析 → None（未知，绝不归零掩盖）。"""
        if v is None or v == "":
            return None
        if isinstance(v, str):
            v = v.replace('°C', '').replace('℃', '').replace('°', '').strip()
            try:
                return int(v)
            except ValueError:
                return None
        return v


class Budget(BaseModel):
    """预算信息（未知分类为空 None，不用 0 掩盖未知）"""
    total_attractions: Optional[int] = Field(default=None, description="景点门票总费用")
    total_hotels: Optional[int] = Field(default=None, description="酒店总费用")
    total_meals: Optional[int] = Field(default=None, description="餐饮总费用")
    total_transportation: Optional[int] = Field(default=None, description="交通总费用")
    total: Optional[int] = Field(default=None, description="总费用")


class TripPlan(BaseModel):
    """旅行计划（展示契约；Phase D 追加溯源/修订/假设字段）"""
    city: str = Field(..., description="目的地城市")
    start_date: str = Field(..., description="开始日期")
    end_date: str = Field(..., description="结束日期")
    days: List[DayPlan] = Field(..., description="每日行程")
    weather_info: List[WeatherInfo] = Field(default=[], description="天气信息")
    overall_suggestions: str = Field(..., description="总体建议")
    budget: Optional[Budget] = Field(default=None, description="预算信息（无可靠预算时为空+assumptions 说明）")
    assumptions: List[str] = Field(default=[], description="关键假设（如坐标/价格未知、合成推断）")
    plan_revision: int = Field(default=1, description="计划修订号")
    graph_version: Optional[int] = Field(default=None, description="来源任务图版本")
    sources: Optional[dict] = Field(default=None, description="溯源摘要：graph/task→provider/查询/证据")
    hotel_candidates: List[HotelCandidate] = Field(default_factory=list, description="住宿研究候选与用户核验入口")


class TripPlanResponse(BaseModel):
    """旅行计划响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: Optional[TripPlan] = Field(default=None, description="旅行计划数据")


class POIInfo(BaseModel):
    """POI信息"""
    id: str = Field(..., description="POI ID")
    name: str = Field(..., description="名称")
    type: str = Field(..., description="类型")
    address: str = Field(..., description="地址")
    location: Location = Field(..., description="经纬度坐标")
    tel: Optional[str] = Field(default=None, description="电话")


class POISearchResponse(BaseModel):
    """POI搜索响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: List[POIInfo] = Field(default=[], description="POI列表")


class RouteInfo(BaseModel):
    """路线信息"""
    distance: float = Field(..., description="距离(米)")
    duration: int = Field(..., description="时间(秒)")
    route_type: str = Field(..., description="路线类型")
    description: str = Field(..., description="路线描述")


class RouteResponse(BaseModel):
    """路线规划响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: Optional[RouteInfo] = Field(default=None, description="路线信息")


class WeatherResponse(BaseModel):
    """天气查询响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(default="", description="消息")
    data: List[WeatherInfo] = Field(default=[], description="天气信息")


# ============ 错误响应 ============

class ErrorResponse(BaseModel):
    """错误响应"""
    success: bool = Field(default=False, description="是否成功")
    message: str = Field(..., description="错误消息")
    error_code: Optional[str] = Field(default=None, description="错误代码")
