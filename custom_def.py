def merge_dict(existing: dict, new_input: dict):
    updated = existing.copy()
    updated.update(new_input)
    return updated

def overwrite_append_list(existing: list[dict], new_input: list[dict]):
    if new_input == []:
        return []
    return existing + new_input