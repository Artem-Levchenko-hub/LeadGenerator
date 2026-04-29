"""Lead Hunter — pluggable источники B2B-лидов.

Архитектура: каждый источник = отдельный класс наследник `LeadSource`.
Hunter runner вызывает все активные источники по очереди и записывает
найденные компании в Company с stage=prospect.

Дедуп: по комбинации (name_normalized, city_normalized).
"""
