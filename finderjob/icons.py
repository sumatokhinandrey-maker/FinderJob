"""Значки кнопок фильтров и их обычные замены для Telegram."""

FILTER_ICONS = {
    "profession": ("💼", "5967389567781703494"),
    "grade": ("⚡️", "5936130851635990622"),
    "salary": ("🪙", "5992430854909989581"),
    "format": ("🏠", "5967822972931542886"),
    "region": ("📍", "5886446115905082831"),
    "sources": ("🔎", "5874960879434338403"),
}

MENU_ICON = ("🎛️", "5875431869842985304")

FALLBACK_BY_ID = {
    custom_id: emoji for emoji, custom_id in (*FILTER_ICONS.values(), MENU_ICON)
}
