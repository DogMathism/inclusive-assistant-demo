def make_recommendation(overload_index, readiness_index):
    if overload_index > 0.7:
        return {"action":"Снизить сложность","text":"Высокая перегрузка: снизьте сложность"}
    elif readiness_index > 0.7:
        return {"action":"Повысить сложность","text":"Готовность высокая: можно усложнить задания"}
    else:
        return {"action":"Оставить как есть","text":"Нагрузка в норме"}