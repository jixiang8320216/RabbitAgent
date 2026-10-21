from pathlib import Path
import re

PATH_TERMINATORS_PATTERN=re.compile(r'[,\s;!?()[\]{}\'",.、：；？！]')
SHELL_SPECIAL_CHARS_PATTERN=r'[ \t()[\]{};|*?$\'"#&<>!~]'

def process_user_input(user_input:str) -> str:
    if not user_input:
        return ""
    if user_input.strip().startswith("/"):
        return user_input.strip()
    return _process_at_path_replacement(user_input)

def _process_at_path_replacement(query:str) -> str:

    result=""
    current_index=0

    while current_index < len(query):
        at_index=-1
        next_search_index= current_index
        while next_search_index < len(query) :
            is_escaped = next_search_index >0 and query[next_search_index-1] == "\\"
            if query[next_search_index] == "@" and not is_escaped:
                at_index=next_search_index
                break
            next_search_index+=1
        if at_index == -1:
            result += query[current_index:]
            break

        result += query[current_index:at_index]
        path_end_index=at_index+1
        in_escape = False
        while path_end_index < len(query) :
            char = query[path_end_index]
            if in_escape:
                in_escape = False
            elif char == "\\":
                in_escape = True
            elif PATH_TERMINATORS_PATTERN.match(char):
                break
            elif char == ".":
                next_char= query[path_end_index+1]if path_end_index+1 < len(query) else ""
                if not next_char or next_char.isspace():
                    break
            path_end_index+=1

        raw_at_path = query[at_index:path_end_index]
        unescaped_path = _unescape_path(raw_at_path[1:])

        if not unescaped_path:
            result += raw_at_path
        elif not Path(unescaped_path).exists():
            result += raw_at_path
        else:
            absolute_path= str(Path(unescaped_path).resolve())
            result += absolute_path
        current_index= path_end_index
    return result

def _unescape_path(file_path:str) -> str:
    pattern =rf"\\([{SHELL_SPECIAL_CHARS_PATTERN[1:-1]}])"
    return re.sub(pattern, r"\1", file_path)